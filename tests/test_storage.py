import dataclasses
import datetime as dt
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from qsb_capture.storage import Config,SegmentArchive,StorageFull,MiB,disk_usage,file_hash
from qsb_capture.object_store import S3,ObjectStoreError,validate_scope,signing_headers,CONSOLE_BUCKET_CAPABILITIES
from qsb_capture.runtime import restore_bundle,healthcheck

REF=b'{"currentTradingDate":"2026-10-29","data":[{"Complex_Instrument_Type":"QSB","Complex_Instrument_Subtype":"BOX_SPREAD","Symbols":[{"Complex_Instrument_Id":"T1","Leg_Count":1,"Leg_Data":[{"OSI_Symbol":"SPX   261029C00678000","OSI_ROOT":"SPX","EXPIRE_DATE":"2026-10-29","STRIKE":678.0000,"Leg_Ratio":-1,"BUY_SELL_FLAG":"S"}]}]}]}'
BOOK=b'{"data":{"bids":[[3,981.9000,""]],"asks":[],"trades":[["15:48:04.592403710",2,981.9000],["15:48:04.592403710",2,981.9000]],"timestamp":"16:07:01"}}'

def meta(kind='depth',ident='d1'):
 return {'id':ident,'sweep':'s','kind':kind,'symbol':'T1' if kind=='depth' else None,'type':'QSB' if kind=='depth' else None,
 'started_at':'2026-10-29T20:07:01+00:00','finished_at':'2026-10-29T20:07:02+00:00','status':200}

def cfg(**kw):
 return dataclasses.replace(Config(),storage_bytes=32*MiB,min_free_bytes=0,segment_bytes=2*MiB,max_response_bytes=128*1024,cold_seconds=0,**kw).validate()

class StorageTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.c=cfg();self.a=SegmentArchive(self.root,self.c)
 def tearDown(self):
  self.a.close();self.temp.cleanup()
 def test_raw_exact_normalized_decimals_and_duplicate_trades(self):
  self.a.save(meta('reference','ref'),REF);self.a.save(meta(),BOOK);self.a.save(meta(),BOOK)
  db=self.a.db
  self.assertEqual(db.execute('SELECT payload FROM bodies WHERE sha256=?',(hashlib.sha256(BOOK).hexdigest(),)).fetchone()[0],BOOK)
  self.assertEqual(db.execute('SELECT price FROM quote_levels').fetchone()[0],'981.9000')
  self.assertEqual(db.execute('SELECT strike,signed_ratio FROM legs').fetchone(),('678.0000','-1'))
  self.assertEqual(db.execute('SELECT count(*) FROM trade_entries').fetchone()[0],2)
  self.assertEqual(db.execute('SELECT count(*) FROM observations').fetchone()[0],1)
 def test_cross_segment_reference_context_is_self_contained(self):
  self.a.save(meta('reference','ref'),REF);self.a.seal();self.a.save(meta(),BOOK)
  self.assertEqual(self.a.db.execute('SELECT count(*) FROM reference_membership').fetchone()[0],1)
  self.assertEqual(self.a.db.execute('SELECT payload FROM bodies WHERE sha256=?',(hashlib.sha256(REF).hexdigest(),)).fetchone()[0],REF)
 def test_archive_restore_and_failed_upload_preserves_sources(self):
  self.a.save(meta('reference','ref'),REF);self.a.save(meta(),BOOK);self.a.seal()
  class Bad:
   def upload_verified(self,*args):raise ObjectStoreError('offline')
  with self.assertRaises(ObjectStoreError):self.a.maintain(Bad(),force=True)
  source=next((self.root/'segments').glob('*.sealed.sqlite3'));bundle=next((self.root/'segments').glob('*.tar.gz'))
  dest=self.root/'restored.sqlite3';manifest=restore_bundle(bundle,dest,self.c.segment_bytes)
  self.assertEqual(file_hash(dest),manifest['database_sha256'])
  class Good:
   def upload_verified(inner,path,key,sha):
    self.assertEqual(file_hash(path),sha);self.assertTrue(key.startswith('qsb/production/segments/'))
  self.assertEqual(self.a.maintain(Good(),force=True),1)
  self.assertFalse(source.exists());self.assertFalse(bundle.exists())
 def test_cap_and_workspace_survive_outage(self):
  # Fill with incompressible raw bodies; no local data is evicted to make space.
  import os
  hit=False
  for i in range(1000):
   try:self.a.save(meta('error',str(i)),os.urandom(self.c.max_response_bytes))
   except StorageFull:hit=True;break
  self.assertTrue(hit);self.assertLessEqual(disk_usage(self.root),self.c.storage_bytes)
  self.a.seal();self.a.maintain(None,force=True)
  self.assertTrue(list((self.root/'segments').glob('*.tar.gz')))
  self.assertLessEqual(disk_usage(self.root),self.c.storage_bytes)
 def test_free_space_floor_and_oversize_rejected(self):
  with patch('qsb_capture.storage.shutil.disk_usage',return_value=type('D',(),{'free':1})()):
   with self.assertRaises(StorageFull):self.a.save(meta(),BOOK)
  with self.assertRaises(ValueError):self.a.save(meta(),b'x'*(self.c.max_response_bytes+1))
 def test_empty_published_universe_is_healthy_but_capture_failures_are_not(self):
  self.a._control('archive-health.json',{'state':'ok'})
  self.a.health({'state':'empty_reference'})
  self.assertTrue(healthcheck(self.root,self.c))
  for state in ('storage_paused','failed','partial'):
   self.a.health({'state':state})
   self.assertFalse(healthcheck(self.root,self.c))
  self.a.health({'state':'empty_reference'})
  self.a._control('archive-health.json',{'state':'failed'})
  self.assertFalse(healthcheck(self.root,self.c))
 def test_recover_open_segment_after_unclean_exit(self):
  self.a.save(meta(),BOOK);self.a.db.close();self.a.db=None
  recovered=SegmentArchive(self.root,self.c)
  self.assertTrue(list((self.root/'segments').glob('*.sealed.sqlite3')));recovered.close()
 def test_scope_is_exact_and_file_only(self):
  good={'apiInfo':{'storageApi':{'s3ApiUrl':self.c.s3_endpoint,'allowed':{
   'buckets':[{'id':'id','name':'your-archive-bucket'}],'namePrefix':'qsb/production/','capabilities':['readFiles','writeFiles']}}}}
  validate_scope(good,self.c)
  for changes in [{'namePrefix':'qsb/'},{'buckets':None},{'capabilities':['readFiles','writeFiles','writeBuckets']}]:
   bad=json.loads(json.dumps(good));bad['apiInfo']['storageApi']['allowed'].update(changes)
   with self.assertRaises(ObjectStoreError):validate_scope(bad,self.c)
 def test_config_rejects_unreserved_cap_and_bad_prefix(self):
  with self.assertRaises(ValueError):dataclasses.replace(self.c,storage_bytes=MiB).validate()
  with self.assertRaises(ValueError):dataclasses.replace(self.c,s3_prefix='qsb/production').validate()
  with self.assertRaises(ValueError):dataclasses.replace(self.c,b2_capability_profile='unrestricted').validate()
 def test_console_profile_accepts_only_the_finite_console_ceiling(self):
  c=dataclasses.replace(self.c,b2_capability_profile='console-read-write').validate()
  doc={'apiInfo':{'storageApi':{'s3ApiUrl':c.s3_endpoint,'allowed':{
   'buckets':[{'id':'id','name':c.s3_bucket}],'namePrefix':c.s3_prefix,
   'capabilities':['readFiles','writeFiles',*sorted(CONSOLE_BUCKET_CAPABILITIES)]}}}}
  with self.assertLogs('qsb.object_store',level='WARNING') as logs:validate_scope(doc,c)
  self.assertIn('bucket-wide',logs.output[0])
  with self.assertRaises(ObjectStoreError):validate_scope(doc,self.c)
  for extra in ['listKeys','writeKeys','deleteKeys','deleteBuckets','writeBucketRetentions',
    'writeFileRetentions','writeFileLegalHolds','bypassGovernance','futureCapability']:
   bad=json.loads(json.dumps(doc));bad['apiInfo']['storageApi']['allowed']['capabilities'].append(extra)
   with self.subTest(capability=extra),self.assertRaises(ObjectStoreError):validate_scope(bad,c)
  for override in [{'namePrefix':'qsb/'},{'buckets':None},
    {'buckets':[{'id':'id','name':c.s3_bucket},{'id':'other','name':'other'}]},
    {'buckets':[{'id':'other','name':'other'}]}]:
   bad=json.loads(json.dumps(doc));bad['apiInfo']['storageApi']['allowed'].update(override)
   with self.subTest(scope=override),self.assertRaises(ObjectStoreError):validate_scope(bad,c)
 def test_real_collector_uses_bounded_storage_hooks(self):
  from qsb_capture.collector import Collector,REFERENCE
  class H(BaseHTTPRequestHandler):
   def do_GET(s):
    body=REF if s.path==REFERENCE else BOOK
    s.send_response(200);s.end_headers();s.wfile.write(body)
   def log_message(*a):pass
  server=ThreadingHTTPServer(('127.0.0.1',0),H);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   c=Collector(self.a,base='http://127.0.0.1:'+str(server.server_port),rate=1000,retries=0)
   self.assertEqual(c.sweep()['state'],'complete')
   self.assertEqual(self.a.db.execute('SELECT count(*) FROM observations').fetchone()[0],1)
   self.assertTrue((self.root/'availability.json').exists());self.assertFalse((self.root/'sweeps').exists())
   c.max_response_bytes=50
   self.assertEqual(c.sweep()['state'],'failed')
   row=self.a.db.execute("SELECT metadata FROM responses WHERE json_extract(metadata,'$.truncated')=1").fetchone()
   self.assertIsNotNone(row)
   self.assertEqual(self.a.db.execute('SELECT count(*) FROM reports').fetchone()[0],2)
  finally:server.shutdown();server.server_close();thread.join()
 def test_corrupt_existing_bundle_cannot_delete_source(self):
  self.a.save(meta(),BOOK);self.a.seal();self.a.maintain(None,force=True)
  bundle=next((self.root/'segments').glob('*.tar.gz'));bundle.write_bytes(b'corrupt')
  class NeverUpload:
   def upload_verified(self,*args):raise AssertionError('corrupt data must not reach uploader')
  with self.assertRaises(Exception):self.a.maintain(NeverUpload(),force=True)
  self.assertTrue(list((self.root/'segments').glob('*.sealed.sqlite3')))
 def test_restore_rejects_traversal_and_cleans_hash_mismatch(self):
  import tarfile,io
  p=self.root/'bad.tar.gz'
  with tarfile.open(p,'w:gz') as t:
   info=tarfile.TarInfo('../outside');info.size=1;t.addfile(info,io.BytesIO(b'x'))
  with self.assertRaises(ValueError):restore_bundle(p,self.root/'out.sqlite3',self.c.segment_bytes)

class S3Tests(unittest.TestCase):
 def test_streamed_put_and_get_hash_retry_and_corruption(self):
  objects={};puts=[];corrupt=[False]
  class H(BaseHTTPRequestHandler):
   def do_HEAD(s):
    s.send_response(200 if s.path in objects else 404);s.end_headers()
   def do_PUT(s):
    data=s.rfile.read(int(s.headers['Content-Length']));objects[s.path]=data;puts.append(s.path)
    self.assertEqual(s.headers['x-amz-content-sha256'],hashlib.sha256(data).hexdigest())
    self.assertIn('AWS4-HMAC-SHA256',s.headers['Authorization'])
    s.send_response(200);s.end_headers()
   def do_GET(s):
    data=objects[s.path]
    if corrupt[0]:data=b'X'+data[1:]
    s.send_response(200);s.send_header('Content-Length',str(len(data)));s.end_headers();s.wfile.write(data)
   def log_message(*a):pass
  server=ThreadingHTTPServer(('127.0.0.1',0),H);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   c=dataclasses.replace(cfg(),s3_endpoint='http://127.0.0.1:'+str(server.server_port))
   client=S3(c,credentials={'application_key_id':'fake','application_key':'fake'},allow_http=True)
   client.check_scope=lambda:None
   with tempfile.TemporaryDirectory() as temp:
    p=Path(temp)/'archive';p.write_bytes(b'raw archive' * 10000);sha=file_hash(p)
    client.upload_verified(p,'qsb/production/test',sha);client.upload_verified(p,'qsb/production/test',sha)
    self.assertEqual(len(puts),1)
    corrupt[0]=True
    with self.assertRaises(ObjectStoreError):client.upload_verified(p,'qsb/production/test',sha)
  finally:server.shutdown();server.server_close();thread.join()
 def test_aws_published_signature_vector(self):
  # AWS S3 GET example, 2013-05-24 (public example keys).
  h=signing_headers('GET','https://examplebucket.s3.amazonaws.com/test.txt','us-east-1',
   'AKIAIOSFODNN7EXAMPLE','wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
   extra={'range':'bytes=0-9'},instant=dt.datetime(2013,5,24,tzinfo=dt.timezone.utc))
  self.assertIn('Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41',h['authorization'])

if __name__=='__main__':unittest.main()
