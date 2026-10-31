import importlib.util
import io
from pathlib import Path
import unittest
s=importlib.util.spec_from_file_location('deploy',Path(__file__).resolve().parents[2]/'scripts/dashboard-deploy.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
ENV={'GITHUB_EVENT_NAME':'push','GITHUB_REPOSITORY':'yogenpro/qsb','GITHUB_REF':'refs/heads/dev','GITHUB_SHA':'a'*40,'GITHUB_TOKEN':'fixture-github-read','DASHBOARD_DEPLOY_HOOK':'https://api.cloudflare.com/client/v4/workers/builds/deploy_hooks/'+'a'*8+'-'+'b'*4+'-'+'c'*4+'-'+'d'*4+'-'+'e'*12}
class Response(io.BytesIO):status=200
class Tests(unittest.TestCase):
 def test_matching_push_queues_only_fixed_hook_and_does_not_forward_github_token(self):
  calls=[]
  def opener(req,**kw):
   calls.append(req)
   return Response(('{"object":{"sha":"'+'a'*40+'"}}').encode() if len(calls)==1 else b'{}')
  self.assertEqual(m.request_build(ENV,opener),{'state':'queued','branch':'dev'})
  self.assertEqual(calls[1].method,'POST');self.assertNotIn('Authorization',calls[1].headers)
 def test_superseded_push_does_not_trigger_build(self):
  calls=[]
  def opener(req,**kw):calls.append(req);return Response(b'{"object":{"sha":"newer"}}')
  self.assertEqual(m.request_build(ENV,opener)['state'],'superseded');self.assertEqual(len(calls),1)
 def test_untrusted_context_never_contacts_provider(self):
  for key,value in [('GITHUB_EVENT_NAME','pull_request'),('GITHUB_REPOSITORY','fork/qsb'),('GITHUB_REF','refs/heads/topic'),('GITHUB_SHA','bad')]:
   with self.subTest(key=key),self.assertRaises(ValueError):m.request_build({**ENV,key:value},lambda *a,**k:self.fail('Unexpected network'))
 def test_missing_and_foreign_hooks_fail_without_post(self):
  for hook in ('','https://foreign.test/secret','https://api.cloudflare.com/other'):
   calls=[]
   def opener(req,**kw):calls.append(req);return Response(('{"object":{"sha":"'+'a'*40+'"}}').encode())
   with self.assertRaises(ValueError):m.request_build({**ENV,'DASHBOARD_DEPLOY_HOOK':hook},opener)
   self.assertEqual(len(calls),1)
 def test_error_does_not_include_capability(self):
  def opener(req,**kw):
   if req.get_method()=='POST':raise OSError(ENV['DASHBOARD_DEPLOY_HOOK'])
   return Response(('{"object":{"sha":"'+'a'*40+'"}}').encode())
  with self.assertRaises(ValueError) as error:m.request_build(ENV,opener)
  self.assertNotIn(ENV['DASHBOARD_DEPLOY_HOOK'],str(error.exception))
if __name__=='__main__':unittest.main()
