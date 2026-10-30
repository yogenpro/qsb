import json
import os
from pathlib import Path
import re
import runpy
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]

class ReleaseTests(unittest.TestCase):
    def tags(self,ref,commit='a'*40,repo='yogenpro/qsb'):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'output'
            with patch.dict(os.environ,{'GITHUB_REF':ref,'GITHUB_SHA':commit,
                    'GITHUB_REPOSITORY':repo,'GITHUB_OUTPUT':str(output)},clear=False),patch('subprocess.run') as git:
                runpy.run_path(str(ROOT/'scripts/image-tags.py'),run_name='__main__')
            return output.read_text(),git
    def test_main_is_commit_addressable(self):
        tags,git=self.tags('refs/heads/main')
        self.assertIn(':sha-'+'a'*40,tags);self.assertIn(':main',tags)
        self.assertNotIn(':latest',tags);git.assert_not_called()
    def test_release_requires_version_and_main_ancestry(self):
        tags,git=self.tags('refs/tags/v0.2.0')
        self.assertIn(':v0.2.0',tags);self.assertIn(':latest',tags)
        git.assert_called_once_with(['git','merge-base','--is-ancestor','a'*40,'origin/main'],check=True)
        with self.assertRaises(SystemExit):self.tags('refs/tags/v999.0.0')
    def test_untrusted_refs_repositories_and_commits_cannot_publish(self):
        for ref,commit,repo in [('refs/pull/1/merge','a'*40,'yogenpro/qsb'),
                ('refs/heads/topic','a'*40,'yogenpro/qsb'),('refs/heads/main','bad','yogenpro/qsb'),
                ('refs/heads/main','a'*40,'someone/fork')]:
            with self.subTest(ref=ref,repo=repo),self.assertRaises(SystemExit):self.tags(ref,commit,repo)
    def test_workflow_actions_are_pinned_and_pr_has_no_publisher(self):
        body=(ROOT/'.github/workflows/image.yml').read_text()
        actions=re.findall(r'uses: (\S+)',body)
        self.assertTrue(actions)
        self.assertTrue(all(re.fullmatch(r'[\w-]+/[\w-]+@[a-f0-9]{40}',x) for x in actions))
        self.assertIn("github.event_name != 'pull_request'",body)
        self.assertNotIn('pull_request_target',body)
        self.assertIn('persist-credentials: false',body)
    def test_image_has_only_generic_configuration(self):
        config=json.loads((ROOT/'config/example.json').read_text())
        self.assertEqual(config['s3_bucket'],'your-archive-bucket')
        self.assertEqual(config['s3_prefix'],'qsb/production/')
        dockerfile=(ROOT/'Dockerfile').read_text()
        self.assertIn('config/example.json /app/config/collector.json',dockerfile)
        base=json.loads((ROOT/'deploy/image-base.json').read_text())
        self.assertIn(base['digest'],dockerfile)

if __name__=='__main__':unittest.main()
