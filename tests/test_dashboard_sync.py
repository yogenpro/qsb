import datetime as dt
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from qsb_capture.dashboard import Output
from qsb_capture.dashboard_sync import publish,R2
from qsb_capture.storage import MiB,StorageFull

class Remote:
 def __init__(self):self.files={};self.calls=[]
 def put_verified(self,key,raw):self.calls.append(('PUT',key));self.files[key]=raw
 def request(self,method,key):
  self.calls.append((method,key))
  if method=='DELETE':self.files.pop(key,None)
  return 204,b''

class PublicationTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
  output=Output(self.root,8*MiB,0)
  detail=output.object({'schema_version':1,'observations':[]})
  index=output.object({'schema_version':1,'instruments':[{'details':[detail]}]})
  output.commit({'schema_version':1,'days':[{'path':index}]})
  self.remote=Remote();self.now=dt.datetime(2026,10,31,tzinfo=dt.timezone.utc)
 def tearDown(self):self.temp.cleanup()
 def test_publish_objects_before_manifest_and_gc_only_after_grace(self):
  old='objects/'+'b'*64+'.json.gz';young='objects/'+'c'*64+'.json.gz'
  items=[{'key':old,'size':1,'modified':'2026-10-29T00:00:00Z'},{'key':young,'size':1,'modified':'2026-10-30T23:50:00Z'}]
  with patch('qsb_capture.dashboard_sync.list_objects',return_value=iter(items)):
   publish(self.root,self.remote,MiB,self.now)
  puts=[key for method,key in self.remote.calls if method=='PUT']
  self.assertEqual(puts[-1],'manifest.json')
  self.assertIn(('DELETE',old),self.remote.calls);self.assertNotIn(('DELETE',young),self.remote.calls)
  self.assertGreater(self.remote.calls.index(('DELETE',old)),self.remote.calls.index(('PUT','manifest.json')))
 def test_remote_cap_is_checked_before_any_upload(self):
  with patch('qsb_capture.dashboard_sync.list_objects',return_value=iter([])):
   with self.assertRaises(StorageFull):publish(self.root,self.remote,1,self.now)
  self.assertEqual(self.remote.calls,[])
 def test_unexpected_bucket_keys_are_not_deleted_or_overwritten(self):
  with patch('qsb_capture.dashboard_sync.list_objects',return_value=iter([{'key':'unrelated','size':1}])):
   with self.assertRaises(ValueError):publish(self.root,self.remote,MiB,self.now)
  self.assertEqual(self.remote.calls,[])
 def test_credentials_reject_insecure_file_and_foreign_endpoint(self):
  p=self.root/'key.json';p.write_text('{}');p.chmod(0o644)
  with self.assertRaises(ValueError):R2('https://'+'a'*32+'.r2.cloudflarestorage.com','test-bucket',p)
  with self.assertRaises(ValueError):R2('https://evil.test','test-bucket',p)

if __name__=='__main__':unittest.main()
