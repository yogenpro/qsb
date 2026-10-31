#!/usr/bin/env python3
"""Request a branch-scoped build after trusted push checks; no Cloudflare API token."""
import json
import os
import re
import urllib.error
import urllib.request


def request_build(env,opener=urllib.request.urlopen):
    branches={'refs/heads/main':'main','refs/heads/dev':'dev'}
    if env.get('GITHUB_EVENT_NAME')!='push' or env.get('GITHUB_REPOSITORY')!='yogenpro/qsb' or env.get('GITHUB_REF') not in branches:
        raise ValueError('Only trusted main/dev pushes can request deployment')
    sha=env.get('GITHUB_SHA','')
    if not re.fullmatch(r'[a-f0-9]{40}',sha):raise ValueError('Invalid source revision')
    branch=branches[env['GITHUB_REF']]
    # Do not let a delayed workflow deploy a branch whose newer checks are pending.
    url='https://api.github.com/repos/yogenpro/qsb/git/ref/heads/'+branch
    request=urllib.request.Request(url,headers={'Accept':'application/vnd.github+json','Authorization':'Bearer '+env['GITHUB_TOKEN'],'User-Agent':'qsb-dashboard-deploy'})
    try:
        with opener(request,timeout=30) as response:
            raw=response.read(65537)
            if len(raw)>65536:raise ValueError('Branch metadata exceeds limit')
            head=json.loads(raw)['object']['sha']
    except (OSError,KeyError,json.JSONDecodeError):raise ValueError('Unable to verify branch head') from None
    if head!=sha:return {'state':'superseded','branch':branch}
    hook=env.get('DASHBOARD_DEPLOY_HOOK','')
    if not re.fullmatch(r'https://api[.]cloudflare[.]com/client/v4/workers/builds/deploy_hooks/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}',hook):
        raise ValueError('Missing or invalid branch deployment capability')
    request=urllib.request.Request(hook,method='POST',data=b'')
    try:
        with opener(request,timeout=30) as response:
            if response.status not in (200,201,202):raise ValueError('Build request failed')
            response.read(65536)
    except OSError:raise ValueError('Cloudflare build request failed; hook value withheld') from None
    return {'state':'queued','branch':branch}

if __name__=='__main__':
    try:print(json.dumps(request_build(os.environ)))
    except ValueError as error:raise SystemExit(str(error))
