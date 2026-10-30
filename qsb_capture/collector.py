from __future__ import annotations
import argparse
import concurrent.futures
from contextlib import contextmanager
import datetime as dt
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import random
import re
import signal
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from zoneinfo import ZoneInfo

API = 'https://www-api.cboe.com'
REFERENCE = '/us/options/market_statistics/symbol_reference/quoted_spread_book_data/'
DEPTH = '/us/options/market_statistics/symbol_reference/quoted_spread_book/{symbol}/market-depth/'
UTC = dt.timezone.utc
ET = ZoneInfo('America/New_York')
LOG = logging.getLogger('qsb')


def now():
    return dt.datetime.now(UTC).isoformat()


def atomic_write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('wb') as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def instruments(document):
    if not isinstance(document, dict) or not isinstance(document.get('data'), list):
        raise ValueError('reference: expected data array')
    if not isinstance(document.get('currentTradingDate'), str):
        raise ValueError('reference: missing currentTradingDate')
    result = {}
    for group in document['data']:
        typ = group['Complex_Instrument_Type']
        if typ not in ('QSB', 'CPM'):
            raise ValueError(f'unknown instrument type {typ!r}')
        for item in group['Symbols']:
            symbol = item['Complex_Instrument_Id']
            if not isinstance(symbol, str) or not re.fullmatch('[A-Za-z0-9]+', symbol):
                raise ValueError('invalid symbol')
            if symbol in result and result[symbol] != typ:
                raise ValueError('symbol occurs in conflicting types')
            result[symbol] = typ
    return sorted(result.items(), key=lambda x: (x[1], x[0]))


def book_summary(document):
    data = document['data']
    for key in ('bids', 'asks', 'trades'):
        if not isinstance(data[key], list):
            raise ValueError(f'{key}: expected list')
        for row in data[key]:
            if not isinstance(row, list) or len(row) != 3:
                raise ValueError(f'{key}: expected three-element rows')
    if not isinstance(data['timestamp'], str):
        raise ValueError('timestamp: expected string')
    def fingerprint(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {k: len(data[k]) for k in ('bids', 'asks', 'trades')} | {
        'timestamp': data['timestamp'],
        'depth_hash': fingerprint([data['bids'], data['asks']]),
        'trades_hash': fingerprint(data['trades']),
    }


class Archive:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(self.root / 'index.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS responses (
            id TEXT PRIMARY KEY, sweep TEXT NOT NULL, kind TEXT NOT NULL,
            symbol TEXT, type TEXT, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
            status INTEGER, sha256 TEXT, body_path TEXT, metadata TEXT NOT NULL)''')
        self.db.execute('CREATE INDEX IF NOT EXISTS response_lookup ON responses(sweep,kind,symbol)')
        self.db.commit()

    def save(self, meta, body):
        # Save first, parse later. Invalid JSON and HTTP error pages survive too.
        meta = dict(meta, id=uuid.uuid4().hex, finished_at=now())
        if body is not None:
            digest = hashlib.sha256(body).hexdigest()
            rel = Path('bodies') / digest[:2] / (digest + '.body')
            dest = self.root / rel
            if not dest.exists():
                atomic_write(dest, body)
            meta.update(sha256=digest, body_path=str(rel), bytes=len(body))
        relmeta = Path('responses') / meta['started_at'][:10] / (meta['id'] + '.json')
        # Standalone provenance remains recoverable if the index is lost.
        atomic_write(self.root / relmeta, json.dumps(meta, indent=2).encode())
        with self.lock:
            self.db.execute('INSERT INTO responses VALUES (?,?,?,?,?,?,?,?,?,?,?)', (
                meta['id'], meta['sweep'], meta['kind'], meta.get('symbol'), meta.get('type'),
                meta['started_at'], meta['finished_at'], meta.get('status'), meta.get('sha256'),
                meta.get('body_path'), json.dumps(meta)))
            self.db.commit()
        return meta

    def close(self):
        self.db.close()


class Collector:
    def __init__(self, archive, base=API, rate=5, workers=6, timeout=15, retries=2, stop=None, max_response_bytes=2*1024*1024):
        self.archive, self.base = archive, base.rstrip('/')
        self.rate, self.workers, self.timeout, self.retries = rate, workers, timeout, retries
        self.stop = stop or threading.Event()
        self.rate_lock = threading.Lock()
        self.next_request = 0.0
        self.max_response_bytes = max_response_bytes

    def reserve(self):
        while not self.stop.is_set():
            with self.rate_lock:
                remaining = self.next_request - time.monotonic()
                if remaining <= 0:
                    self.next_request = time.monotonic() + 1 / self.rate
                    return True
            if self.stop.wait(remaining):
                break
        return False

    def fetch(self, sweep, kind, path, symbol=None, typ=None):
        for attempt in range(self.retries + 1):
            if hasattr(self.archive, 'admit'):
                self.archive.admit()
            if not self.reserve():
                return None
            url = self.base + path
            meta = dict(sweep=sweep, kind=kind, symbol=symbol, type=typ,
                        url=url, started_at=now(), attempt=attempt + 1,
                        request_headers={'User-Agent':'qsb-capture/1.0', 'Accept-Encoding':'identity', 'Accept':'application/json'})
            body = None
            status = None
            retry_after = 0
            document = None
            try:
                request = urllib.request.Request(url, headers=meta['request_headers'])
                try:
                    response = urllib.request.urlopen(request, timeout=self.timeout)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    status = response.status
                    meta.update(status=status, headers=list(response.headers.items()), final_url=response.url)
                    body = response.read(self.max_response_bytes + 1)
                    if len(body) > self.max_response_bytes:
                        body = body[:self.max_response_bytes]
                        meta['truncated'] = True
                        meta['parse_error'] = 'response exceeds configured byte limit'
                    value = response.headers.get('Retry-After', '')
                    if value.isdigit():
                        retry_after = int(value)
                    elif value:
                        from email.utils import parsedate_to_datetime
                        try:
                            retry_after = max(0, (parsedate_to_datetime(value) - dt.datetime.now(UTC)).total_seconds())
                        except (ValueError, TypeError):
                            pass
                if status == 200 and not meta.get('truncated'):
                    try:
                        document = json.loads(body)
                        if kind == 'reference':
                            symbols = instruments(document)
                            meta['summary'] = {'instruments':len(symbols), 'currentTradingDate': document['currentTradingDate']}
                        else:
                            meta['summary'] = book_summary(document)
                    except (ValueError, KeyError, TypeError) as error:
                        meta['parse_error'] = str(error)
                        document = None
            except (OSError, TimeoutError) as error:
                meta['error'] = str(error)
            saved = self.archive.save(meta, body)
            if document is not None:
                return document, saved
            retryable = status is None or status in (408, 429) or (status is not None and status >= 500)
            if not retryable or attempt == self.retries:
                LOG.warning('capture failed %s status=%s error=%s', url, status, saved.get('error', saved.get('parse_error')))
                return None
            delay = max(retry_after, 2 ** attempt + random.random())
            # Coordinate server backoff across all workers, including queued requests.
            with self.rate_lock:
                self.next_request = max(self.next_request, time.monotonic() + delay)
            if self.stop.wait(delay):
                return None
        return None

    def sweep(self):
        sweep = uuid.uuid4().hex
        report = dict(sweep=sweep, started_at=now(), state='running')
        self.health(report)
        reference = self.fetch(sweep, 'reference', REFERENCE)
        if reference is None:
            report.update(state='failed', reason='reference unavailable; no stale symbol universe substituted', finished_at=now())
            self.finish(report)
            return report
        document, refmeta = reference
        symbols = instruments(document)
        report.update(trading_date=document['currentTradingDate'], reference_id=refmeta['id'], expected=len(symbols), successful=0, failed=0, nonempty=0, types={})
        previous_path = self.archive.root / 'availability' / 'latest.json'
        previous = self.archive.load_availability() if hasattr(self.archive, 'load_availability') else (json.loads(previous_path.read_text()) if previous_path.exists() else {})
        previous = {s: v for s, v in previous.items() if v.get('trading_date') == report['trading_date']}
        observed = {}
        availability = {typ: dict(expected=sum(t == typ for _, t in symbols), valid=0,
            failed=0, empty_depth=0, nonempty_depth=0, with_trades=0, first_observation=0,
            depth_changed=0, trade_window_changed=0, timestamp_changed=0) for typ in ('QSB', 'CPM')}
        # Round robin types to give both immediate coverage.
        buckets = [[item for item in symbols if item[1] == typ] for typ in ('QSB','CPM')]
        ordered = []
        for i in range(max(map(len, buckets), default=0)):
            ordered.extend(b[i] for b in buckets if i < len(b))
        def capture(item):
            symbol, typ = item
            return typ, self.fetch(sweep, 'depth', DEPTH.format(symbol=symbol), symbol, typ)
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
            for typ, result in pool.map(capture, ordered):
                if result:
                    report['successful'] += 1
                    report['types'][typ] = report['types'].get(typ, 0) + 1
                    summary = result[1]['summary']
                    report['nonempty'] += bool(summary['bids'] or summary['asks'])
                    stats = availability[typ]
                    stats['valid'] += 1
                    nonempty = bool(summary['bids'] or summary['asks'])
                    stats['nonempty_depth' if nonempty else 'empty_depth'] += 1
                    stats['with_trades'] += bool(summary['trades'])
                    symbol = result[1]['symbol']
                    prior = previous.get(symbol)
                    if prior and prior.get('trading_date') == report['trading_date']:
                        stats['depth_changed'] += prior['depth_hash'] != summary['depth_hash']
                        stats['trade_window_changed'] += prior['trades_hash'] != summary['trades_hash']
                        stats['timestamp_changed'] += prior['timestamp'] != summary['timestamp']
                    else:
                        stats['first_observation'] += 1
                    observed[symbol] = summary | {'type':typ, 'trading_date':report['trading_date'],
                        'observed_at':result[1]['finished_at'], 'response_id':result[1]['id']}
                else:
                    report['failed'] += 1
                    availability[typ]['failed'] += 1
                if (report['successful'] + report['failed']) % 50 == 0:
                    self.health(report)
        report.update(finished_at=now(), state='interrupted' if self.stop.is_set() else ('complete' if report['failed'] == 0 else 'partial'))
        if not symbols:
            report['state'] = 'empty_reference'
        report['stale_reference'] = document['currentTradingDate'] != dt.datetime.now(ET).date().isoformat()
        report['availability'] = availability
        # Retain the last valid observation when an instrument request fails or disappears.
        # Comparisons are against the previous successful sample, never inferred events.
        if hasattr(self.archive, 'save_availability'):
            self.archive.save_availability(previous | observed)
        else:
            atomic_write(previous_path, json.dumps(previous | observed, indent=2).encode())
        self.finish(report)
        return report

    def finish(self, report):
        self.health(report)
        if hasattr(self.archive, 'save_report'):
            self.archive.save_report(report)
        else:
            atomic_write(self.archive.root / 'sweeps' / (report['sweep'] + '.json'), json.dumps(report, indent=2).encode())
        LOG.info('%s', json.dumps(report))

    def health(self, report):
        if hasattr(self.archive, 'health'):
            self.archive.health(report)
        else:
            atomic_write(self.archive.root / 'health.json', json.dumps(report | {'heartbeat_at':now()}, indent=2).encode())


def session_active(instant, sessions=None, start='09:25', end='16:20'):
    local = instant.astimezone(ET)
    key = local.date().isoformat()
    if sessions is not None and key in sessions:
        window = sessions[key]
        if window is None:
            return False
        start, end = window['open'], window['close']
    elif local.weekday() >= 5:
        return False
    return dt.time.fromisoformat(start) <= local.time().replace(tzinfo=None) < dt.time.fromisoformat(end)


@contextmanager
def exclusive(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.collector.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('another collector owns this archive')
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['once','run','status','verify'])
    parser.add_argument('--output', type=Path, default=Path('captures/live'))
    parser.add_argument('--interval', type=float, default=300, help='seconds between sweep starts (no overlap)')
    parser.add_argument('--rate', type=float, default=5, help='maximum aggregate requests per second')
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--timeout', type=float, default=15)
    parser.add_argument('--retries', type=int, default=2)
    parser.add_argument('--sessions', type=Path, help='JSON date -> {open,close} in New York time, or null for closure')
    parser.add_argument('--start', default='09:25')
    parser.add_argument('--end', default='16:20')
    parser.add_argument('--all-hours', action='store_true', help='observe availability 24/7; ignore session windows')
    args = parser.parse_args()
    if args.command in ('run','once'):
        parser.error('legacy capture CLI is retired; use python -m qsb_capture.runtime with a capped storage config')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if args.command == 'status':
        print((args.output / 'health.json').read_text())
        return
    if args.command == 'verify':
        errors = []
        count = 0
        for p in args.output.glob('responses/*/*.json'):
            meta = json.loads(p.read_text())
            if 'body_path' in meta:
                count += 1
                body = args.output / meta['body_path']
                if not body.exists() or hashlib.sha256(body.read_bytes()).hexdigest() != meta['sha256']:
                    errors.append(str(p))
        print(json.dumps({'responses_checked': count, 'errors': errors}))
        raise SystemExit(bool(errors))
    if args.rate <= 0 or args.workers < 1 or args.interval <= 0 or args.timeout <= 0 or args.retries < 0:
        parser.error('rate, workers, interval and timeout must be positive; retries must be nonnegative')
    start, end = dt.time.fromisoformat(args.start), dt.time.fromisoformat(args.end)
    if start >= end:
        parser.error('start must precede end within the same New York day')
    sessions = json.loads(args.sessions.read_text()) if args.sessions else None
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    with exclusive(args.output):
        archive = Archive(args.output)
        collector = Collector(archive, rate=args.rate, workers=args.workers, timeout=args.timeout, retries=args.retries, stop=stop)
        try:
            if args.command == 'once':
                result = collector.sweep()
                raise SystemExit(0 if result['state'] == 'complete' else 1)
            while not stop.is_set():
                if args.all_hours or session_active(dt.datetime.now(UTC), sessions, args.start, args.end):
                    started = time.monotonic()
                    collector.sweep()
                    stop.wait(max(1, args.interval - (time.monotonic() - started)))
                else:
                    collector.health({'state':'outside_capture_window'})
                    stop.wait(30)
        finally:
            archive.close()
