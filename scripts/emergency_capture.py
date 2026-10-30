"""One-off bootstrap archive; response bodies are saved before inspection."""
import concurrent.futures
import datetime as dt
import hashlib
import json
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request

ROOT = Path('captures') / ('initial-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
ROOT.mkdir(parents=True)
BASE = 'https://www-api.cboe.com/us/options/market_statistics/symbol_reference/quoted_spread_book/'
items = []
for group in json.loads(Path('captures/bootstrap/reference.json').read_bytes())['data']:
    for symbol in group['Symbols']:
        items.append((group['Complex_Instrument_Type'], symbol['Complex_Instrument_Id']))
# Interleave types so both have coverage immediately.
items.sort(key=lambda v: v[1])
lock = threading.Lock()
next_request = 0.0

def capture(item):
    global next_request
    typ, symbol = item
    with lock:
        delay = max(0, next_request - time.monotonic())
        next_request = max(next_request, time.monotonic()) + .1
    time.sleep(delay)
    url = BASE + symbol + '/market-depth/'
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    meta = dict(url=url, started_at=started, type=typ, symbol=symbol)
    dest = ROOT / typ / symbol
    dest.mkdir(parents=True, exist_ok=True)
    try:
        try:
            response = urllib.request.urlopen(urllib.request.Request(url, headers={'Accept-Encoding':'identity','User-Agent':'qsb-archive/0.1'}), timeout=15)
        except urllib.error.HTTPError as e:
            response = e
        body = response.read()
        (dest / 'body.json').write_bytes(body)
        meta.update(status=response.status, headers=list(response.headers.items()), sha256=hashlib.sha256(body).hexdigest(), bytes=len(body))
        try:
            data = json.loads(body)['data']
            meta.update(bids=len(data['bids']), asks=len(data['asks']), trades=len(data['trades']), timestamp=data['timestamp'])
        except Exception as e: meta['parse_error'] = str(e)
    except Exception as e: meta['error'] = str(e)
    meta['finished_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    (dest / 'metadata.json').write_text(json.dumps(meta,indent=2))
    return meta

print(str(ROOT), 'symbols', len(items), flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    for i, result in enumerate(pool.map(capture, items), 1):
        if i % 100 == 0: print(i, result['finished_at'], flush=True)
print('complete', flush=True)
