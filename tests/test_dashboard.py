import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from qsb_capture.dashboard import export_day,load_json,reachable
from qsb_capture.storage import SegmentArchive,MiB,StorageFull,file_hash
from test_storage import REF,BOOK,meta,cfg

class DashboardTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
  self.a=SegmentArchive(self.root/'capture',cfg())
  self.a.save(meta('reference','ref'),REF)
  self.a.save(meta('depth','ok'),BOOK)
  m=meta('depth','empty');m['finished_at']='2026-10-29T20:12:02+00:00'
  self.a.save(m,b'{"data":{"bids":[],"asks":[],"trades":[],"timestamp":"16:12:01"}}')
  m=meta('depth','failure');m.update(status=503,finished_at='2026-10-29T20:17:02+00:00')
  self.a.save(m,b'offline');self.a.close()
  self.paths=list((self.root/'capture/segments').glob('*.sealed.sqlite3'))
  self.out=self.root/'output'
 def tearDown(self):self.temp.cleanup()
 def export(self,**kw):
  return export_day(self.paths,self.out,'2026-10-29',as_of=dt.datetime(2026,10,30,tzinfo=dt.timezone.utc),min_free_bytes=0,**kw)
 def index(self,m):return load_json(self.out/m['days'][0]['path'])
 def test_dedup_exact_decimals_ordered_trades_empty_and_failures(self):
  before=[file_hash(p) for p in self.paths]
  # Duplicate/overlapping source segments must not count captures twice.
  copy=self.root/'copy.sealed.sqlite3';copy.write_bytes(self.paths[0].read_bytes());self.paths.append(copy)
  m=self.export();i=self.index(m)['instruments'][0]
  self.assertEqual((i['attempts'],i['valid'],i['nonempty'],i['failed']),(3,2,1,1))
  d=load_json(self.out/i['details'][0]);self.assertEqual([x['status'] for x in d['observations']],['nonempty','empty','failed'])
  first=d['observations'][0]
  self.assertEqual(d['states'][first['depth_id']][0][0][1],'981.9000')
  self.assertEqual(len(d['trade_windows'][first['trades_id']]),2)
  self.assertIsNone(d['observations'][2].get('depth_id'))
  self.assertEqual(before,[file_hash(p) for p in self.paths[:-1]])
 def test_rerun_same_inputs_same_objects_and_counts(self):
  m=self.export();files=reachable(self.out,m);m2=self.export()
  self.assertEqual(files,reachable(self.out,m2));self.assertEqual(m['days'],m2['days'])
  self.assertFalse(list(self.out.rglob('*.partial')))
 def test_expiry_keeps_summary_and_removes_only_derived_detail(self):
  m=self.export();index=self.index(m);detail=index['instruments'][0]['details'][0]
  # Process the next collection date; the old summary remains discoverable.
  newer=export_day(self.paths,self.out,'2026-10-30',as_of=dt.datetime(2026,11,20,tzinfo=dt.timezone.utc),min_free_bytes=0)
  old=next(d for d in newer['days'] if d['date']=='2026-10-29')
  retained=load_json(self.out/old['path'])['instruments'][0]
  self.assertEqual(retained['hourly'],index['instruments'][0]['hourly'])
  self.assertEqual(retained['details'],[]);self.assertFalse((self.out/detail).exists())
  self.assertTrue(all(p.exists() for p in self.paths))
 def test_output_cap_failure_preserves_published_manifest(self):
  self.export();before=(self.out/'manifest.json').read_bytes()
  (self.out/'reserved').write_bytes(b'x'*(2*MiB))
  with self.assertRaises(StorageFull):self.export(max_output_bytes=MiB)
  self.assertEqual(before,(self.out/'manifest.json').read_bytes())
 def test_reject_active_source_and_naive_time(self):
  with self.assertRaises(ValueError):export_day([self.root/'active.open.sqlite3'],self.out,'2026-10-29',min_free_bytes=0)
  with self.assertRaises(ValueError):export_day(self.paths,self.out,'2026-10-29',as_of=dt.datetime(2026,10,30),min_free_bytes=0)

if __name__=='__main__':unittest.main()
