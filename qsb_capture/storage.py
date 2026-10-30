"""Bounded, self-contained raw + normalized SQLite segments (standard library only)."""
from __future__ import annotations
from dataclasses import dataclass, fields
from decimal import Decimal
import datetime as dt
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import threading
import time
import uuid

MiB = 1024 * 1024
CONTROL = MiB

class StorageFull(RuntimeError):
    """No more persistent writes are admitted; unarchived data is never evicted."""

@dataclass
class Config:
    storage_bytes: int = 2048 * MiB
    min_free_bytes: int = 2048 * MiB
    segment_bytes: int = 32 * MiB
    segment_seconds: int = 1800
    cold_seconds: int = 900
    max_response_bytes: int = 2 * MiB
    interval_seconds: float = 300
    rate: float = 5
    workers: int = 6
    timeout_seconds: float = 15
    retries: int = 2
    s3_endpoint: str = 'https://s3.us-west-004.backblazeb2.com'
    s3_region: str = 'us-west-004'
    s3_bucket: str = 'your-archive-bucket'
    s3_prefix: str = 'qsb/production/'
    credentials_file: str = '/run/secrets/b2.json'

    @classmethod
    def load(cls, path):
        obj = json.loads(Path(path).read_text())
        unknown = set(obj) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError('unknown config fields: ' + ', '.join(sorted(unknown)))
        return cls(**obj).validate()

    def validate(self):
        for name in ('storage_bytes','segment_bytes','segment_seconds','max_response_bytes','workers'):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(name + ' must be a positive integer')
        if self.segment_bytes < MiB or self.segment_bytes % 4096:
            raise ValueError('segment_bytes must be a multiple of 4096, at least 1 MiB')
        if self.storage_bytes < 6 * self.segment_bytes + 8 * CONTROL:
            raise ValueError('storage cap must reserve six segments plus 8 MiB of headroom')
        if not 0 < self.max_response_bytes <= self.segment_bytes // 4:
            raise ValueError('response limit must be at most a quarter segment')
        for name in ('min_free_bytes','cold_seconds','retries'):
            if type(getattr(self,name)) is not int or getattr(self,name) < 0:
                raise ValueError(name + ' must be a nonnegative integer')
        for name in ('interval_seconds','rate','timeout_seconds'):
            value = getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not 0 < value < float('inf'):
                raise ValueError(name + ' must be positive and finite')
        if self.workers > 32 or self.retries > 10:
            raise ValueError('workers/retries exceed supported bounds')
        if not self.s3_prefix.endswith('/') or self.s3_prefix.startswith('/') or '..' in self.s3_prefix.split('/'):
            raise ValueError('object prefix must be a relative prefix ending in /')
        return self


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=lambda v: str(v) if isinstance(v,Decimal) else None)

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(MiB), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_bundle(bundle, source):
    """A stale/partial/corrupt derived bundle must never justify deleting its source."""
    with tarfile.open(bundle, 'r|gz') as tar:
        entries=iter(tar)
        first=next(entries, None)
        if first is None or first.name!='manifest.json' or not first.isfile() or first.size>65536:
            raise ValueError('invalid bundle manifest member')
        manifest=json.loads(tar.extractfile(first).read())
        second=next(entries, None)
        if second is None or second.name!='segment.sqlite3' or not second.isfile() or second.size!=source.stat().st_size:
            raise ValueError('invalid bundle database member')
        h=hashlib.sha256()
        with tar.extractfile(second) as db:
            for chunk in iter(lambda:db.read(MiB),b''):h.update(chunk)
        if manifest.get('schema_version')!=1 or manifest.get('database_bytes')!=second.size or manifest.get('database')!='segment.sqlite3' or h.hexdigest()!=manifest.get('database_sha256') or h.hexdigest()!=file_hash(source):
            raise ValueError('bundle does not match its source database')
        if next(entries,None) is not None:
            raise ValueError('unexpected bundle member')


def disk_usage(root):
    """Charge allocated blocks or logical length, whichever is larger, including directories."""
    total = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        for path in [Path(base)] + [Path(base)/n for n in files]:
            if path.is_symlink():
                raise ValueError('symlinks are not allowed in the data directory')
            st = path.stat()
            total += max(st.st_size, st.st_blocks * 512)
        if any((Path(base)/n).is_symlink() for n in dirs):
            raise ValueError('symlink directories are not allowed')
    return total


class Budget:
    def __init__(self, root, config):
        self.root, self.config = Path(root), config
    def check(self, extra=0):
        used = disk_usage(self.root)
        free = shutil.disk_usage(self.root).free
        if used + extra > self.config.storage_bytes:
            raise StorageFull('local storage cap reached; collection paused, unarchived data retained')
        if free - extra < self.config.min_free_bytes:
            raise StorageFull('filesystem free-space floor reached; collection paused')
        return used


class SegmentArchive:
    def __init__(self, root, config, clock=time.time):
        self.root, self.config, self.clock = Path(root), config.validate(), clock
        if self.root.is_symlink():
            raise ValueError('data root must not be a symlink')
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.budget = Budget(self.root, config)
        self.budget.check(2 * CONTROL)
        self.db = None
        self.active = None
        self.opened = 0
        self.context = None
        self.symbols = {}
        self.reference_id = None
        (self.root/'segments').mkdir(exist_ok=True)
        for p in (self.root/'segments').glob('*.sqlite3'):
            if p.stat().st_size>config.segment_bytes:
                raise ValueError('existing segment exceeds configured ceiling; restore the original configuration before maintenance')
        # Exact collector-owned temporary names only. No source-data cleanup.
        for p in (self.root/'segments').glob('*.tar.gz.partial'):
            p.unlink()
        opens = sorted((self.root/'segments').glob('*.open.sqlite3'))
        if len(opens) > 1:
            raise ValueError('multiple active segments; operator inspection required')
        if opens:
            self._open(opens[0])
            # Recovered data is sealed before a new process begins writing.
            self.seal()

    def _open(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        if self.db.execute('PRAGMA page_size').fetchone()[0]!=4096:
            self.db.close();self.db=None
            raise ValueError('unsupported SQLite page size')
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA temp_store=MEMORY')
        self.db.execute('PRAGMA cache_size=-2048')
        self.db.execute('PRAGMA max_page_count=' + str(self.config.segment_bytes // 4096))
        self.active, self.opened = path, self.clock()

    def admit(self):
        with self.lock:
            # A transaction can grow the DB and its rollback journal to one segment each.
            # In addition to DB/journal growth, reserve a complete compression workspace.
            # Otherwise an upload outage could leave too little room to package the backlog.
            self.budget.check(4 * self.config.segment_bytes + 4 * CONTROL)

    def _ensure(self):
        self.admit()
        if self.db is not None and self.clock() - self.opened >= self.config.segment_seconds:
            self.seal()
        if self.db is None:
            stamp = dt.datetime.fromtimestamp(self.clock(),dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            self._open(self.root/'segments'/(stamp + '-' + uuid.uuid4().hex + '.open.sqlite3'))
            self.db.executescript(Path(__file__).with_name('schema.sql').read_text())
            self.db.commit()

    def _body_response(self, meta, body):
        sha = hashlib.sha256(body).hexdigest() if body is not None else None
        if body is not None:
            self.db.execute('INSERT OR IGNORE INTO bodies VALUES (?,?,?)',(sha,len(body),body))
        self.db.execute('INSERT OR IGNORE INTO responses VALUES (?,?,?,?,?,?,?,?,?,?)',(
            meta['id'],meta['sweep'],meta['kind'],meta.get('symbol'),meta.get('type'),
            meta['started_at'],meta['finished_at'],meta.get('status'),sha,canonical(meta)))

    def _instrument(self, symbol):
        if symbol not in self.symbols:
            return None
        typ, subtype, definition = self.symbols[symbol]
        key = digest([self.trading_date,typ,symbol,definition])
        self.db.execute('INSERT OR IGNORE INTO instrument_versions VALUES (?,?,?,?,?,?)',(
            key,self.trading_date,symbol,typ,subtype,canonical(definition)))
        for i, leg in enumerate(definition.get('Leg_Data',[]),1):
            self.db.execute('INSERT OR IGNORE INTO legs VALUES (?,?,?,?,?,?,?,?)',(
                key,i,leg.get('OSI_Symbol'),leg.get('OSI_ROOT'),leg.get('EXPIRE_DATE'),
                str(leg.get('STRIKE')),str(leg.get('Leg_Ratio')),leg.get('BUY_SELL_FLAG')))
        return key

    def _normalize(self, meta, body):
        if meta.get('status') != 200 or meta.get('parse_error') or meta.get('truncated'):
            return
        doc = json.loads(body,parse_float=Decimal)
        if meta['kind'] == 'reference':
            self.set_reference_context(meta,body)
            for symbol in self.symbols:
                key = self._instrument(symbol)
                self.db.execute('INSERT OR IGNORE INTO reference_membership VALUES (?,?)',(meta['id'],key))
        elif meta['kind'] == 'depth':
            d = doc['data']
            if any(not isinstance(d[k],list) or any(not isinstance(r,list) or len(r)!=3 for r in d[k]) for k in ('bids','asks','trades')):
                raise ValueError('invalid depth/trade rows')
            instrument = self._instrument(meta['symbol'])
            # Embed the reference response in each segment that needs it, so archive recovery
            # never depends on an unbounded global index or another segment remaining local.
            if self.context:
                self._body_response(*self.context)
                if instrument:
                    self.db.execute('INSERT OR IGNORE INTO reference_membership VALUES (?,?)',(self.reference_id,instrument))
            depth = digest([d['bids'],d['asks']]); trades = digest(d['trades'])
            self.db.execute('INSERT OR IGNORE INTO depth_states VALUES (?,?)',(depth,canonical([d['bids'],d['asks']])))
            for side in ('bids','asks'):
                for i,row in enumerate(d[side]):
                    self.db.execute('INSERT OR IGNORE INTO quote_levels VALUES (?,?,?,?,?,?)',(
                        depth,side,i,str(row[0]),str(row[1]),canonical(row[2])))
            self.db.execute('INSERT OR IGNORE INTO trade_windows VALUES (?,?)',(trades,canonical(d['trades'])))
            for i,row in enumerate(d['trades']):
                self.db.execute('INSERT OR IGNORE INTO trade_entries VALUES (?,?,?,?,?)',(trades,i,row[0],str(row[1]),str(row[2])))
            self.db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(
                meta['id'],self.reference_id,instrument,getattr(self,'trading_date',None),meta['symbol'],meta['type'],
                meta['finished_at'],d['timestamp'],depth,trades,len(d['bids']),len(d['asks']),len(d['trades'])))

    def set_reference_context(self,meta,body):
        from .collector import instruments
        doc=json.loads(body,parse_float=Decimal)
        instruments(doc)
        symbols={item['Complex_Instrument_Id']:(g['Complex_Instrument_Type'],g['Complex_Instrument_Subtype'],item)
            for g in doc['data'] for item in g['Symbols']}
        self.trading_date=doc['currentTradingDate'];self.symbols=symbols
        self.context=(dict(meta),body);self.reference_id=meta['id']

    def save(self, meta, body):
        if body is not None and len(body) > self.config.max_response_bytes:
            raise ValueError('response exceeds configured limit')
        meta = dict(meta, id=meta.get('id',uuid.uuid4().hex), finished_at=meta.get('finished_at',dt.datetime.now(dt.timezone.utc).isoformat()))
        if body is not None:
            meta.update(sha256=hashlib.sha256(body).hexdigest(),bytes=len(body))
        if len(canonical(meta).encode()) > 128 * 1024:
            raise ValueError('response metadata exceeds 128 KiB limit')
        with self.lock:
            for attempt in range(2):
                self._ensure()
                try:
                    with self.db:
                        self._body_response(meta,body)
                        self.db.execute('SAVEPOINT normalization')
                        try:
                            if body is not None:
                                self._normalize(meta,body)
                        except (ValueError,TypeError,KeyError) as e:
                            self.db.execute('ROLLBACK TO normalization')
                            meta['normalization_error'] = type(e).__name__
                            self.db.execute('UPDATE responses SET metadata=? WHERE id=?',(canonical(meta),meta['id']))
                        self.db.execute('RELEASE normalization')
                    if self.active.stat().st_size >= self.config.segment_bytes * .75:
                        self.seal()
                    return meta
                except sqlite3.OperationalError as e:
                    if 'full' not in str(e).lower():
                        raise
                    self.db.rollback()
                    self.seal()
                    if attempt:
                        raise StorageFull('one response cannot fit within a segment') from e
        raise AssertionError('unreachable')

    def _control(self,name,value):
        payload = json.dumps(value,indent=2).encode()
        if len(payload) > CONTROL:
            raise ValueError('control state exceeds 1 MiB limit')
        with self.lock:
            self.budget.check(2 * CONTROL)
            path = self.root/name
            tmp = path.with_suffix('.tmp')
            with tmp.open('wb') as f:
                f.write(payload); f.flush(); os.fsync(f.fileno())
            os.replace(tmp,path)

    def load_availability(self):
        p=self.root/'availability.json'
        return json.loads(p.read_text()) if p.exists() else {}

    def save_availability(self,value):
        self._control('availability.json',value)

    def health(self,value):
        self._control('health.json',value | {'heartbeat_at':dt.datetime.now(dt.timezone.utc).isoformat()})

    def save_report(self,value):
        with self.lock:
            self._ensure()
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO reports VALUES (?,?,?)',(
                    value['sweep'],value.get('finished_at',value['started_at']),canonical(value)))

    def seal(self):
        with self.lock:
            if self.db is None:
                return
            self.db.commit()
            if self.db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('segment integrity check failed')
            self.db.close(); self.db=None
            with self.active.open('rb') as f: os.fsync(f.fileno())
            os.replace(self.active,self.active.with_name(self.active.name.replace('.open.','.sealed.')))
            self.active=None
            self._fsync_directory()

    def _fsync_directory(self):
        fd=os.open(self.root/'segments',os.O_RDONLY | os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)

    def close(self):
        self.seal()

    def package(self, source):
        """Called under the archive lock. No source deletion until remote verification."""
        target=source.with_name(source.name.replace('.sealed.sqlite3','.tar.gz'))
        if target.exists():
            verify_bundle(target,source)
            return target
        self.budget.check(2 * self.config.segment_bytes + 2 * CONTROL)
        manifest={'schema_version':1,'database':'segment.sqlite3','database_sha256':file_hash(source),
                  'database_bytes':source.stat().st_size,'segment':source.name}
        with sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True) as c:
            manifest['response_count']=c.execute('SELECT count(*) FROM responses').fetchone()[0]
            manifest['observation_count']=c.execute('SELECT count(*) FROM observations').fetchone()[0]
            manifest['first_observed_at'],manifest['last_observed_at']=c.execute('SELECT min(started_at),max(finished_at) FROM responses').fetchone()
        tmp=target.with_name(target.name+'.partial')
        try:
            with tmp.open('wb') as f:
                with gzip.GzipFile(filename='',mode='wb',fileobj=f,mtime=0,compresslevel=6) as gz:
                    with tarfile.open(fileobj=gz,mode='w|') as tar:
                        for name,body in [('manifest.json',json.dumps(manifest,sort_keys=True).encode())]:
                            info=tarfile.TarInfo(name);info.size=len(body);info.mode=0o600
                            tar.addfile(info,io.BytesIO(body))
                        info=tarfile.TarInfo('segment.sqlite3');info.size=source.stat().st_size;info.mode=0o600
                        with source.open('rb') as src:tar.addfile(info,src)
                f.flush();os.fsync(f.fileno())
            if tmp.stat().st_size > 2 * self.config.segment_bytes:
                raise ValueError('compressed archive exceeded reserved space')
            os.replace(tmp,target);self._fsync_directory()
        finally:
            tmp.unlink(missing_ok=True)
        verify_bundle(target,source)
        return target

    def maintain(self, remote=None, force=False):
        with self.lock:
            if self.db is not None and (force or self.clock()-self.opened >= self.config.segment_seconds):
                self.seal()
            pressure=disk_usage(self.root) > self.config.storage_bytes * .65
            count=0
            for source in sorted((self.root/'segments').glob('*.sealed.sqlite3')):
                if not force and not pressure and self.clock()-source.stat().st_mtime < self.config.cold_seconds:
                    continue
                bundle=self.package(source)
                if remote is None:
                    # One ready-to-upload bundle is sufficient during an outage. Keeping
                    # extra compressed duplicates would consume the recovery workspace.
                    break
                if remote is not None:
                    sha=file_hash(bundle)
                    stem=source.name.removesuffix('.sealed.sqlite3')
                    date=stem[:8]
                    key=self.config.s3_prefix+'segments/'+date[:4]+'/'+date[4:6]+'/'+date[6:8]+'/'+stem+'-'+sha+'.tar.gz'
                    remote.upload_verified(bundle,key,sha)
                    self._control('last-upload.json',{'key':key,'sha256':sha,'verified_at':dt.datetime.now(dt.timezone.utc).isoformat()})
                    # The source stays until the uploaded object has been downloaded and hashed.
                    bundle.unlink();source.unlink();self._fsync_directory();count+=1
            return count
