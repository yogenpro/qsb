"""Production collector entry point. All persistent writes share the capped spool."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
import tarfile
import threading
import time

from .collector import Collector, exclusive
from .storage import Config, SegmentArchive, StorageFull, disk_usage, file_hash
from .object_store import S3, ObjectStoreError

LOG=logging.getLogger('qsb.runtime')


def healthcheck(root, config):
    health=json.loads((root/'health.json').read_text())
    age=(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(health['heartbeat_at'])).total_seconds()
    # A valid empty reference is an observed source state, not a failed collector.
    if age > max(config.interval_seconds+180,120) or health.get('state') in ('storage_paused','failed','partial'):
        return False
    archive=root/'archive-health.json'
    if archive.exists() and json.loads(archive.read_text()).get('state')=='failed':
        return False
    return disk_usage(root) <= config.storage_bytes


def import_legacy(source,archive):
    """Idempotent IDs allow safe reruns. Imports never modify or delete source files."""
    source=Path(source)
    if source.resolve()==archive.root.resolve():raise ValueError('import source must differ from destination')
    old=source/'index.sqlite3'
    count=0
    seen=set()
    for segment in (archive.root/'segments').glob('*.sqlite3'):
        with sqlite3.connect('file:'+str(segment)+'?mode=ro',uri=True) as existing:
            seen.update(r[0] for r in existing.execute('SELECT id FROM responses'))
    if old.exists():
        with sqlite3.connect('file:'+str(old)+'?mode=ro',uri=True) as db:
            # Hold a read transaction, not a mutable view, while backfilling references.
            db.execute('BEGIN')
            for raw,path in db.execute('SELECT metadata,body_path FROM responses ORDER BY started_at,id'):
                meta=json.loads(raw)
                body=(source/path).read_bytes() if path else None
                if body is not None and file_hash(source/path)!=meta['sha256']:
                    raise ValueError('legacy body checksum mismatch')
                if meta['id'] in seen:
                    if meta['kind']=='reference' and meta.get('status')==200 and not meta.get('parse_error'):
                        archive.set_reference_context(meta,body)
                    continue
                archive.save(meta,body);count+=1
    else:
        for p in sorted(source.glob('*/*/metadata.json')):
            meta=json.loads(p.read_text());body_path=p.with_name('body.json')
            body=body_path.read_bytes() if body_path.exists() else None
            if body is not None and file_hash(body_path)!=meta['sha256']:
                raise ValueError('legacy body checksum mismatch')
            # Bootstrap attempts lack IDs; their original path makes reruns stable.
            import hashlib
            meta.setdefault('id',hashlib.sha256(str(p.resolve()).encode()).hexdigest())
            meta.setdefault('sweep',source.name);meta.setdefault('kind','depth')
            if meta['id'] in seen:continue
            archive.save(meta,body);count+=1
    for report in sorted((source/'sweeps').glob('*.json')):
        archive.save_report(json.loads(report.read_text()))
    availability=source/'availability/latest.json'
    if availability.exists():archive.save_availability(json.loads(availability.read_text()))
    return count


def restore_bundle(bundle,destination,max_bytes):
    """Restore only two expected regular members; never extract arbitrary tar paths."""
    from .storage import file_hash
    destination=Path(destination)
    if destination.exists():raise ValueError('restore destination already exists')
    if Path(bundle).stat().st_size>2*max_bytes:raise ValueError('compressed bundle exceeds restore limit')
    with tarfile.open(bundle,'r|gz') as tar:
        entries=iter(tar);first=next(entries,None)
        if first is None or first.name!='manifest.json' or not first.isfile() or first.size>64*1024:
            raise ValueError('unexpected manifest member')
        manifest=json.loads(tar.extractfile(first).read())
        second=next(entries,None)
        if second is None or second.name!='segment.sqlite3' or not second.isfile() or second.size>max_bytes:
            raise ValueError('unexpected database member')
        if manifest.get('schema_version')!=1 or manifest.get('database')!='segment.sqlite3' or manifest['database_bytes']!=second.size:
            raise ValueError('invalid archive manifest')
        try:
            with destination.open('xb') as out:
                with tar.extractfile(second) as src:
                    remaining=second.size
                    while remaining:
                        data=src.read(min(1024*1024,remaining))
                        if not data:raise ValueError('truncated archive')
                        out.write(data);remaining-=len(data)
                out.flush();os.fsync(out.fileno())
            if next(entries,None) is not None:raise ValueError('unexpected extra archive member')
            if file_hash(destination)!=manifest['database_sha256']:
                raise ValueError('restored database hash mismatch')
            with sqlite3.connect('file:'+str(destination)+'?mode=ro',uri=True) as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise ValueError('restored database integrity failure')
        except Exception:
            destination.unlink(missing_ok=True);raise
    return manifest


def run(args,config):
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    with exclusive(args.output):
        archive=SegmentArchive(args.output,config)
        remote_client=None
        # No hot-path index grows forever: control files are bounded, records live in segments.
        def maintenance(force=False):
            nonlocal remote_client
            try:
                try:
                    if remote_client is None:
                        remote_client=S3(config)
                    remote=remote_client
                    remote.check_scope()
                except (OSError,ValueError):
                    # Still compress cold segments while credentials are unavailable.
                    archive.maintain(None,force=force)
                    raise
                uploaded=archive.maintain(remote,force=force)
                archive._control('archive-health.json',{'state':'ok','uploaded_segments':uploaded,
                    'checked_at':dt.datetime.now(dt.timezone.utc).isoformat()})
            except Exception as e:
                remote_client=None
                # No server bodies, keys, URLs containing auth, or exception reprs in logs.
                LOG.warning('archive unavailable (%s); local data retained',type(e).__name__)
                try:archive._control('archive-health.json',{'state':'failed','error_class':type(e).__name__,
                    'checked_at':dt.datetime.now(dt.timezone.utc).isoformat()})
                except StorageFull:pass
        def worker():
            while not stop.is_set():
                maintenance()
                stop.wait(60)
        thread=threading.Thread(target=worker,name='archiver',daemon=True)
        thread.start()
        collector=Collector(archive,rate=config.rate,workers=config.workers,timeout=config.timeout_seconds,
            retries=config.retries,stop=stop,max_response_bytes=config.max_response_bytes)
        try:
            while not stop.is_set():
                started=time.monotonic()
                try:
                    report=collector.sweep()
                    if args.command=='once':return 0 if report['state']=='complete' else 1
                except StorageFull as e:
                    LOG.error('%s',e)
                    archive.seal()
                    maintenance(force=True)
                    try:archive.health({'state':'storage_paused','reason':str(e),'storage_bytes':disk_usage(archive.root)})
                    except StorageFull:pass
                    if args.command=='once':return 2
                stop.wait(max(1,config.interval_seconds-(time.monotonic()-started)))
        finally:
            stop.set();thread.join(timeout=100)
            if thread.is_alive():
                raise RuntimeError('archiver did not stop within its network timeout budget')
            archive.close()
    return 0


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['run','once','maintain','status','check-health','import-legacy','restore','export'])
    p.add_argument('--output',type=Path,default=Path('/data'))
    p.add_argument('--config',type=Path,default=Path('/app/config/collector.json'))
    p.add_argument('--source',type=Path)
    p.add_argument('--segment',type=Path)
    p.add_argument('--object-key')
    p.add_argument('--object-bytes',type=int)
    p.add_argument('--sha256')
    args=p.parse_args();config=Config.load(args.config)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    if args.command=='status':
        result={'storage_bytes':disk_usage(args.output),'storage_cap_bytes':config.storage_bytes}
        for name in ['health','archive-health','last-upload']:
            path=args.output/(name+'.json')
            result[name]=json.loads(path.read_text()) if path.exists() else None
        print(json.dumps(result,indent=2));return
    if args.command=='check-health':
        try:ok=healthcheck(args.output,config)
        except (OSError,ValueError,KeyError):ok=False
        raise SystemExit(0 if ok else 1)
    if args.command in ('run','once'):
        raise SystemExit(run(args,config))
    with exclusive(args.output):
        archive=SegmentArchive(args.output,config)
        try:
            if args.command=='maintain':print(json.dumps({'uploaded_segments':archive.maintain(S3(config),force=True)}))
            elif args.command=='import-legacy':
                if not args.source:p.error('--source is required')
                print(json.dumps({'imported':import_legacy(args.source,archive)}))
            elif args.command=='restore':
                if not args.object_key or not args.sha256 or not args.object_bytes:p.error('restore requires --object-key, --sha256 and --object-bytes')
                if not 0<args.object_bytes<=2*config.segment_bytes:p.error('object exceeds archive size limit')
                archive.budget.check(4*config.segment_bytes+4*1024*1024)
                remote=S3(config);bundle=args.output/'restore.tar.gz';dest=args.output/'restored.sqlite3'
                if bundle.exists() or dest.exists():raise ValueError('remove or relocate previous restore before another restore')
                try:
                    with bundle.open('xb') as out:remote.verify(args.object_key,args.object_bytes,args.sha256,destination=out)
                    print(json.dumps(restore_bundle(bundle,dest,config.segment_bytes)))
                finally:bundle.unlink(missing_ok=True)
            elif args.command=='export':
                if not args.segment:p.error('--segment is required')
                segment=args.segment.resolve()
                if not segment.is_relative_to(args.output.resolve()) or segment.suffix!='.sqlite3':raise ValueError('segment must be a SQLite file in the capped data directory')
                with sqlite3.connect('file:'+str(segment)+'?mode=ro',uri=True) as db:
                    for row in db.execute('SELECT metadata FROM responses ORDER BY started_at,id'):print(row[0])
                os.utime(segment,None) # explicit access, not filesystem atime, delays cold packaging
        finally:archive.close()

if __name__=='__main__':main()
