import datetime as dt
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from qsb_capture.collector import Archive, Collector, REFERENCE, book_summary, instruments, session_active

REFERENCE_BODY = json.dumps({'currentTradingDate':'2026-10-29','data':[
    {'Complex_Instrument_Type':typ, 'Symbols':[{'Complex_Instrument_Id':symbol}]}
    for typ,symbol in [('QSB','T1'),('CPM','U1')]]}).encode()
BOOK = b'{"data":{"bids":[[11,981.6,""]],"asks":[],"trades":[["15:48:04.592403710",2,981.9000]],"timestamp":"15:54:10"}}'

class Handler(BaseHTTPRequestHandler):
    paths = []
    failures = 0
    def do_GET(self):
        type(self).paths.append(self.path)
        if self.path == REFERENCE:
            body = REFERENCE_BODY
        elif '/T1/' in self.path:
            if type(self).failures:
                type(self).failures -= 1
                self.send_response(503); self.end_headers(); self.wfile.write(b'maintenance'); return
            body = BOOK
        else:
            body = b'{"data":{"bids":[],"asks":[],"trades":[],"timestamp":"15:54:10"}}'
        self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
    def log_message(self,*args): pass

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.archive = Archive(self.root)
        Handler.paths = []; Handler.failures = 0
        self.server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.collector = Collector(self.archive,base='http://127.0.0.1:'+str(self.server.server_port),rate=1000,retries=1)
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.archive.close(); self.temp.cleanup()
    def test_full_sweep_preserves_numeric_lexemes_and_empty_books(self):
        report = self.collector.sweep()
        self.assertEqual(report['state'],'complete')
        self.assertEqual(report['types'],{'QSB':1,'CPM':1})
        self.assertEqual(report['nonempty'],1)
        rows = self.archive.db.execute('SELECT metadata FROM responses').fetchall()
        self.assertEqual(len(rows),3)
        metas = [json.loads(row[0]) for row in rows]
        qsb = next(m for m in metas if m.get('symbol') == 'T1')
        raw = (self.root/qsb['body_path']).read_bytes()
        self.assertEqual(raw,BOOK)
        self.assertIn(b'981.9000',raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),qsb['sha256'])
        self.assertEqual(len(list(self.root.glob('responses/*/*.json'))),3)
    def test_http_failure_is_archived_before_retry(self):
        Handler.failures = 1
        self.assertEqual(self.collector.sweep()['successful'],2)
        row = self.archive.db.execute('SELECT body_path FROM responses WHERE status=503').fetchone()
        self.assertEqual((self.root/row[0]).read_bytes(),b'maintenance')
    def test_malformed_json_archived(self):
        global REFERENCE_BODY
        original = REFERENCE_BODY
        try:
            REFERENCE_BODY = b'<html>error</html>'
            self.assertEqual(self.collector.sweep()['state'],'failed')
            row = self.archive.db.execute('SELECT metadata FROM responses').fetchone()
            self.assertIn('parse_error',json.loads(row[0]))
            self.assertEqual(len(Handler.paths),1)
        finally: REFERENCE_BODY = original
    def test_schema_drift_rejected(self):
        with self.assertRaises(ValueError): book_summary({'data':{'bids':[[1,2]],'asks':[],'trades':[],'timestamp':'x'}})
        with self.assertRaises(ValueError): instruments({'data':[]})
    def test_availability_distinguishes_clock_from_content(self):
        first = self.collector.sweep()
        second = self.collector.sweep()
        for typ in ('QSB','CPM'):
            self.assertEqual(first['availability'][typ]['first_observation'],1)
            self.assertEqual(second['availability'][typ]['depth_changed'],0)
            self.assertEqual(second['availability'][typ]['trade_window_changed'],0)
        self.assertEqual(second['availability']['CPM']['empty_depth'],1)
        self.assertEqual(second['availability']['QSB']['nonempty_depth'],1)
        changed_clock = json.loads(BOOK)
        changed_clock['data']['timestamp'] = '16:30:00'
        self.assertEqual(book_summary(changed_clock)['depth_hash'],book_summary(json.loads(BOOK))['depth_hash'])
        self.assertTrue((self.root/'sweeps'/(second['sweep']+'.json')).exists())
    def test_dst_weekend_closure_and_early_close(self):
        self.assertTrue(session_active(dt.datetime(2026,10,29,19,59,tzinfo=dt.timezone.utc)))
        self.assertTrue(session_active(dt.datetime(2026,11,2,20,59,tzinfo=dt.timezone.utc)))
        self.assertFalse(session_active(dt.datetime(2026,10,31,16,tzinfo=dt.timezone.utc)))
        self.assertFalse(session_active(dt.datetime(2026,10,29,16,tzinfo=dt.timezone.utc),{'2026-10-29':None}))
        self.assertFalse(session_active(dt.datetime(2026,10,29,18,tzinfo=dt.timezone.utc),{'2026-10-29':{'open':'09:30','close':'13:00'}}))
    def test_published_schema_fixtures(self):
        root = Path(__file__).resolve().parent/'fixtures'
        reference = json.loads((root/'reference.json').read_bytes())
        self.assertEqual({typ for _,typ in instruments(reference)},{'QSB','CPM'})
        self.assertEqual(book_summary(json.loads((root/'book.json').read_bytes()))['bids'],1)

if __name__ == '__main__': unittest.main()
