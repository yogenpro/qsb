"""Bounded batch materialization of sealed capture segments into static dashboard files.

Each invocation rebuilds one collection date from its complete set of segments.
Source files are opened read-only. Output is derived and independently capped.
"""
from __future__ import annotations
import argparse
import datetime as dt
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

from .storage import MiB, StorageFull, canonical, disk_usage, file_hash

SCHEMA = 1
MAX_DOCUMENT = 8 * MiB
PART_SIZE = 256


def load_json(path):
    raw = Path(path).read_bytes()
    if str(path).endswith('.gz'):
        with gzip.open(path, 'rb') as stream:
            raw = stream.read(MAX_DOCUMENT + 1)
    if len(raw) > MAX_DOCUMENT:
        raise ValueError('dashboard document exceeds decoded limit')
    return json.loads(raw)


class Output:
    def __init__(self, root, cap, min_free=256*MiB, compressed=True):
        self.root, self.cap, self.min_free, self.compressed = Path(root), cap, min_free, compressed
        self.root.mkdir(parents=True, exist_ok=True)
        self.admit(0)

    def admit(self, extra):
        if disk_usage(self.root) + extra > self.cap:
            raise StorageFull('dashboard output cap reached; raw collection is unaffected')
        if shutil.disk_usage(self.root).free - extra < self.min_free:
            raise StorageFull('dashboard filesystem free-space floor reached')

    def object(self, value):
        raw = canonical(value).encode()
        if len(raw) > MAX_DOCUMENT:
            raise ValueError('split dashboard document before publishing')
        encoded = gzip.compress(raw, mtime=0) if self.compressed else raw
        name = 'objects/' + hashlib.sha256(encoded).hexdigest() + ('.json.gz' if self.compressed else '.json')
        path = self.root/name
        if not path.exists():
            self.admit(2*len(encoded)+16384)
            path.parent.mkdir(exist_ok=True)
            temporary = path.with_suffix(path.suffix+'.partial')
            temporary.write_bytes(encoded)
            temporary.replace(path)
        return name

    def commit(self, manifest):
        raw = canonical(manifest).encode()
        if len(raw) > MAX_DOCUMENT:
            raise ValueError('manifest needs pagination')
        self.admit(2*len(raw)+16384)
        temporary = self.root/'manifest.json.partial'
        temporary.write_bytes(raw)
        temporary.replace(self.root/'manifest.json')
        # Only unreachable derived objects are removed, after the new root exists.
        live = reachable(self.root, manifest)
        for path in (self.root/'objects').glob('*'):
            if path.name.endswith('.partial') or 'objects/'+path.name not in live:
                path.unlink()


def reachable(root, manifest):
    live = set()
    for day in manifest['days']:
        live.add(day['path'])
        index = load_json(Path(root)/day['path'])
        for instrument in index['instruments']:
            live.update(instrument.get('details', []))
    return live


def source_db(path):
    path = Path(path).resolve()
    if not path.name.endswith('.sealed.sqlite3'):
        raise ValueError('only immutable sealed SQLite segments are accepted')
    db = sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    if db.execute('PRAGMA user_version').fetchone()[0] != 1:
        db.close()
        raise ValueError('unsupported capture schema')
    return db


def ingest(db, paths, date):
    db.executescript('''
      CREATE TABLE samples(id TEXT PRIMARY KEY,instrument TEXT,at TEXT,data TEXT);
      CREATE INDEX by_instrument ON samples(instrument,at,id);
      CREATE TABLE instruments(id TEXT PRIMARY KEY,data TEXT);
      CREATE TABLE contexts(sweep TEXT,type TEXT,symbol TEXT,instrument TEXT,data TEXT,
        PRIMARY KEY(sweep,type,symbol));
    ''')
    # Failed attempts have no normalized observation. Resolve definitions only from
    # their own sweep's published reference, never from a later instrument version.
    count = 0
    for path in paths:
        source = source_db(path)
        try:
            for sweep, payload in source.execute("SELECT r.sweep,b.payload FROM responses r JOIN bodies b ON b.sha256=r.body_sha256 WHERE r.kind='reference' AND r.status=200"):
                try:
                    reference = json.loads(payload,parse_float=Decimal)
                    for group in reference['data']:
                        for definition in group['Symbols']:
                            typ, symbol = group['Complex_Instrument_Type'], definition['Complex_Instrument_Id']
                            ident = hashlib.sha256(canonical([reference['currentTradingDate'],typ,symbol,definition]).encode()).hexdigest()
                            info = {'id':ident,'symbol':symbol,'type':typ,'subtype':group['Complex_Instrument_Subtype'],
                              'trading_date':reference['currentTradingDate'],'definition':definition}
                            db.execute('INSERT OR IGNORE INTO contexts VALUES (?,?,?,?,?)',
                              (sweep,typ,symbol,ident,canonical(info)))
                except (ValueError,KeyError,TypeError):
                    continue
            # Joins use normalized states, preserving decimal strings and ordered trades.
            query = '''SELECT r.*,o.instrument_id,o.trading_date,o.observed_at,o.source_time,
              d.data_json AS depth,t.data_json AS trades,i.definition_json,i.subtype
              FROM responses r LEFT JOIN observations o ON o.response_id=r.id
              LEFT JOIN depth_states d ON d.id=o.depth_id
              LEFT JOIN trade_windows t ON t.id=o.trades_id
              LEFT JOIN instrument_versions i ON i.id=o.instrument_id
              WHERE r.kind='depth' AND substr(r.finished_at,1,10)=?
              ORDER BY r.finished_at,r.id'''
            for row in source.execute(query, (date,)):
                meta = json.loads(row['metadata'])
                ident = row['instrument_id'] or hashlib.sha256(canonical([date,row['type'],row['symbol'],'unknown']).encode()).hexdigest()
                good = row['depth'] is not None
                depth = json.loads(row['depth']) if good else None
                trades = json.loads(row['trades']) if good else None
                sample = {'id':row['id'],'at':row['finished_at'],'started_at':row['started_at'],
                  'source_time':row['source_time'],'trading_date':row['trading_date'],
                  'http_status':row['status'],'status':('nonempty' if any(depth) else 'empty') if good else 'failed',
                  'depth':depth,'trades':trades,
                  'error': next((str(meta[k])[:160] for k in ('normalization_error','parse_error','error') if meta.get(k)),None)}
                definition = json.loads(row['definition_json']) if row['definition_json'] else {}
                info = {'id':ident,'symbol':row['symbol'],'type':row['type'],'subtype':row['subtype'] or 'Unknown',
                  'trading_date':row['trading_date'],'definition':definition}
                if row['instrument_id'] is None:
                    context = db.execute('SELECT instrument,data FROM contexts WHERE sweep=? AND type=? AND symbol=?',
                      (row['sweep'],row['type'],row['symbol'])).fetchone()
                    if context:
                        ident, encoded = context
                        info = json.loads(encoded)
                        sample['trading_date'] = info['trading_date']
                db.execute('INSERT OR IGNORE INTO instruments VALUES (?,?)',(ident,canonical(info)))
                count += db.execute('INSERT OR IGNORE INTO samples VALUES (?,?,?,?)',
                  (row['id'],ident,row['finished_at'],canonical(sample))).rowcount
        finally:
            source.close()
        db.commit()
    return count


def best(levels, side):
    values = [Decimal(str(row[1])) for row in levels]
    return str((max if side == 'bid' else min)(values)) if values else None


def summary_and_parts(db, ident, output, details):
    info = json.loads(db.execute('SELECT data FROM instruments WHERE id=?',(ident,)).fetchone()[0])
    stats = {'attempts':0,'valid':0,'nonempty':0,'failed':0,'depth_changes':0,'trade_window_changes':0}
    hours, parts, samples = {}, [], []
    states, trades = {}, {}
    first = last = prior_depth = prior_trades = None
    for raw, in db.execute('SELECT data FROM samples WHERE instrument=? ORDER BY at,id',(ident,)):
        sample = json.loads(raw)
        first = first or sample['at']; last = sample['at']
        stats['attempts'] += 1
        good = sample['status'] != 'failed'
        stats['valid' if good else 'failed'] += 1
        stats['nonempty'] += sample['status'] == 'nonempty'
        hour = sample['at'][:13]+':00:00+00:00'
        bucket = hours.setdefault(hour,{'at':hour,'attempts':0,'valid':0,'nonempty':0,'bid_min':None,'bid_max':None,'ask_min':None,'ask_max':None})
        bucket['attempts'] += 1; bucket['valid'] += good; bucket['nonempty'] += sample['status']=='nonempty'
        if good:
            depth, window = sample.pop('depth'), sample.pop('trades')
            depth_id = hashlib.sha256(canonical(depth).encode()).hexdigest()
            trade_id = hashlib.sha256(canonical(window).encode()).hexdigest()
            stats['depth_changes'] += prior_depth is not None and prior_depth != depth_id
            stats['trade_window_changes'] += prior_trades is not None and prior_trades != trade_id
            prior_depth, prior_trades = depth_id, trade_id
            sample.update(depth_id=depth_id,trades_id=trade_id,bid=best(depth[0],'bid'),ask=best(depth[1],'ask'))
            for side in ('bid','ask'):
                value = sample[side]
                if value is not None:
                    for suffix, fn in (('min',min),('max',max)):
                        key = side+'_'+suffix
                        old = bucket[key]
                        bucket[key] = value if old is None else str(fn(Decimal(old),Decimal(value)))
            if details:
                states[depth_id] = depth; trades[trade_id] = window
        else:
            # A failure breaks change comparisons across a gap.
            prior_depth = prior_trades = None
            sample.pop('depth');sample.pop('trades')
        if details:
            samples.append(sample)
            candidate = {'schema_version':SCHEMA,'instrument_id':ident,'observations':samples,'states':states,'trade_windows':trades}
            if len(canonical(candidate).encode()) > MAX_DOCUMENT:
                raise ValueError('instrument detail part exceeds decoded ceiling')
            if len(samples) >= PART_SIZE:
                parts.append(output.object(candidate)); samples=[];states={};trades={}
    if samples:
        parts.append(output.object({'schema_version':SCHEMA,'instrument_id':ident,'observations':samples,'states':states,'trade_windows':trades}))
    return {**info,**stats,'first':first,'last':last,'hourly':list(hours.values()),'details':parts}


def export_day(paths, root, date, *, as_of=None, detail_days=14, max_output_bytes=512*MiB,
               max_workspace_bytes=256*MiB, min_free_bytes=256*MiB, compressed=True, workspace=None,
               path_factory=None, source_fingerprint=None):
    dt.date.fromisoformat(date)
    as_of = as_of or dt.datetime.now(dt.timezone.utc)
    if as_of.tzinfo is None or not 1 <= detail_days <= 365:
        raise ValueError('timezone-aware as_of and 1..365 detail days required')
    if max_workspace_bytes < 4*MiB or max_output_bytes < MiB:
        raise ValueError('workspace/output budget is too small')
    output = Output(root,max_output_bytes,min_free_bytes,compressed)
    manifest_path = output.root/'manifest.json'
    manifest = load_json(manifest_path) if manifest_path.exists() else {'schema_version':SCHEMA,'days':[]}
    if manifest['schema_version'] != SCHEMA:
        raise ValueError('unsupported dashboard schema')
    cutoff = (as_of.astimezone(dt.timezone.utc).date()-dt.timedelta(days=detail_days-1)).isoformat()
    # SQLite has a hard page cap; its journal/temp files use bounded in-memory buffers.
    # Only one instrument's detail part is held in Python at a time.
    workspace = Path(workspace) if workspace else output.root.parent
    workspace.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(workspace).free < max_workspace_bytes + min_free_bytes:
        raise StorageFull('dashboard workspace free-space floor reached')
    paths = sorted(set(Path(p).resolve() for p in paths)) if path_factory is None else []
    if not paths and path_factory is None:
        raise ValueError('provide the complete nonempty segment set for this date')
    if any(not p.name.endswith('.sealed.sqlite3') for p in paths):
        raise ValueError('only immutable sealed SQLite segments are accepted')
    fingerprint = source_fingerprint if path_factory else hashlib.sha256(canonical([file_hash(p) for p in paths]).encode()).hexdigest()
    if not isinstance(fingerprint,str) or len(fingerprint)!=64:
        raise ValueError("source fingerprint is required for streamed inputs")
    with tempfile.TemporaryDirectory(prefix='qsb-export-',dir=workspace) as temporary:
        db = sqlite3.connect(str(Path(temporary)/'day.sqlite3'))
        try:
            db.execute('PRAGMA page_size=4096');db.execute('PRAGMA journal_mode=MEMORY')
            db.execute('PRAGMA temp_store=MEMORY');db.execute('PRAGMA cache_size=-2048')
            db.execute('PRAGMA max_page_count='+str(max_workspace_bytes//4096))
            count = ingest(db,path_factory() if path_factory else paths,date)
            instruments = [summary_and_parts(db,row[0],output,date>=cutoff)
              for row in db.execute('SELECT id FROM instruments ORDER BY id')]
        finally:
            db.close()
    # Prune recent-detail references from old daily indexes, preserving summaries.
    days = []
    for old in manifest['days']:
        if old['date'] == date:
            continue
        if old['date'] < cutoff and old.get('has_detail'):
            index = load_json(output.root/old['path'])
            for item in index['instruments']:
                item['details'] = []
            old = {**old,'path':output.object(index),'has_detail':False}
        days.append(old)
    index = {'schema_version':SCHEMA,'date':date,'instruments':instruments,'source_fingerprint':fingerprint}
    days.append({'date':date,'path':output.object(index),'has_detail':date>=cutoff,
      'attempts':count,'valid':sum(i['valid'] for i in instruments),'instruments':len(instruments)})
    manifest.update(generated_at=as_of.isoformat(),detail_days=detail_days,
      days=sorted(days,key=lambda d:d['date'],reverse=True))
    output.commit(manifest)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--segments',nargs='+',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--date',required=True,help='UTC collection date, YYYY-MM-DD')
    parser.add_argument('--detail-days',type=int,default=14)
    parser.add_argument('--max-output-bytes',type=int,default=512*MiB)
    parser.add_argument('--max-workspace-bytes',type=int,default=256*MiB)
    parser.add_argument('--min-free-bytes',type=int,default=256*MiB)
    parser.add_argument('--workspace',type=Path)
    args=parser.parse_args()
    from .collector import exclusive
    with exclusive(args.output):
        result=export_day(args.segments,args.output,args.date,detail_days=args.detail_days,
          max_output_bytes=args.max_output_bytes,max_workspace_bytes=args.max_workspace_bytes,
          min_free_bytes=args.min_free_bytes,workspace=args.workspace)
    print(json.dumps({'days':len(result['days']),'output_bytes':disk_usage(args.output)}))

if __name__=='__main__':
    main()
