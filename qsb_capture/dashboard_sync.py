"""Incremental B2-to-dashboard export and verified R2 publication, independent of collection."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import time
import urllib.parse
import xml.etree.ElementTree as ET

from .dashboard import export_day,load_json,reachable,MAX_DOCUMENT
from .object_store import S3,signing_headers,EMPTY,ObjectStoreError
from .runtime import restore_bundle
from .storage import Config,MiB,StorageFull,canonical,disk_usage

OBJECT = re.compile(r'objects/[a-f0-9]{64}\.json\.gz\Z')
ARCHIVE = re.compile(r'(\d{4})/(\d{2})/(\d{2})/[^/]+-([a-f0-9]{64})\.tar\.gz\Z')
NS={'s':'http://s3.amazonaws.com/doc/2006-03-01/'}


def list_objects(client, prefix):
    """S3 ListObjectsV2, exact configured bucket/prefix; paginated and size bounded."""
    continuation=None
    while True:
        query={'list-type':'2','prefix':prefix,'max-keys':'1000'}
        if continuation:query['continuation-token']=continuation
        base=client.config.s3_endpoint.rstrip('/')+'/'+urllib.parse.quote(client.config.s3_bucket,safe='')
        url=base+'?'+urllib.parse.urlencode(query,quote_via=urllib.parse.quote)
        headers=signing_headers('GET',url,client.config.s3_region,client.access_id,client.secret)
        conn,r=client._request('GET',url,headers)
        try:
            if r.status!=200:raise ObjectStoreError('object listing failed HTTP '+str(r.status))
            raw=r.read(4*MiB+1)
            if len(raw)>4*MiB:raise ObjectStoreError('object listing exceeds limit')
            root=ET.fromstring(raw)
        finally:conn.close()
        for entry in root.findall('s:Contents',NS):
            key=entry.findtext('s:Key',namespaces=NS)
            if not key or not key.startswith(prefix):raise ObjectStoreError('listing outside requested prefix')
            yield {'key':key,'size':int(entry.findtext('s:Size',namespaces=NS)),
              'modified':entry.findtext('s:LastModified',namespaces=NS)}
        if root.findtext('s:IsTruncated',namespaces=NS)!='true':break
        token=root.findtext('s:NextContinuationToken',namespaces=NS)
        if not token or token==continuation:raise ObjectStoreError('invalid listing continuation')
        continuation=token


class R2:
    def __init__(self, endpoint, bucket, credentials_file):
        p=urllib.parse.urlsplit(endpoint)
        if p.scheme!='https' or not re.fullmatch(r'[a-f0-9]{32}\.r2\.cloudflarestorage\.com',p.netloc) or p.path not in ('','/') or p.query or p.fragment:
            raise ValueError('expected exact Cloudflare R2 HTTPS endpoint')
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]',bucket):raise ValueError('invalid R2 bucket')
        fd=os.open(credentials_file,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            st=os.fstat(stream.fileno())
            if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) not in (0o400,0o440,0o600):
                raise ValueError('R2 credentials must be a protected regular file')
            credentials=json.load(stream)
        if set(credentials)!={'access_key_id','secret_access_key'} or not all(isinstance(v,str) and v for v in credentials.values()):
            raise ValueError('invalid R2 credential document')
        self.access_id=credentials['access_key_id'];self.secret=credentials['secret_access_key']
        self.config=Config(s3_endpoint=endpoint,s3_bucket=bucket,s3_region='auto')
    _request=S3._request

    def request(self,method,key,body=None):
        if key!='manifest.json' and not OBJECT.fullmatch(key):raise ValueError('key outside dashboard namespace')
        url=self.config.s3_endpoint.rstrip('/')+'/'+self.config.s3_bucket+'/'+key
        sha=hashlib.sha256(body).hexdigest() if body is not None else EMPTY
        extra={'content-length':str(len(body)),'content-type':'application/json'} if body is not None else {}
        if body is not None:
            extra['cache-control']='public, max-age=60' if key=='manifest.json' else 'public, max-age=31536000, immutable'
            if key.endswith('.gz'):extra['content-encoding']='gzip'
        conn,r=self._request(method,url,signing_headers(method,url,'auto',self.access_id,self.secret,sha,extra),body)
        try:
            raw=r.read(MAX_DOCUMENT+1)
            if len(raw)>MAX_DOCUMENT:raise ObjectStoreError('R2 response exceeds document ceiling')
            if r.status not in (200,204,404):raise ObjectStoreError('R2 '+method+' failed HTTP '+str(r.status))
            return r.status,raw
        finally:conn.close()

    def put_verified(self,key,raw):
        status,_=self.request('PUT',key,raw)
        if status!=200:raise ObjectStoreError('R2 upload failed')
        status,readback=self.request('GET',key)
        if status!=200 or hashlib.sha256(readback).digest()!=hashlib.sha256(raw).digest():
            raise ObjectStoreError('R2 read-back verification failed')


def publish(root,remote,cap,now):
    manifest=load_json(root/'manifest.json')
    needed=reachable(root,manifest)
    existing={item['key']:item for item in list_objects(remote,'')}
    if any(key!='manifest.json' and not OBJECT.fullmatch(key) for key in existing):
        raise ValueError('R2 dashboard bucket contains unexpected keys; refusing cleanup')
    # Count orphan objects and the old manifest too. Never assume cache eviction
    # or garbage collection creates room before publishing a complete replacement.
    used=sum(item['size'] for item in existing.values())
    additions=sum((root/key).stat().st_size for key in needed if key not in existing)+(root/'manifest.json').stat().st_size
    if used+additions>cap:raise StorageFull('R2 publication cap reached; existing dashboard retained')
    for key in sorted(needed):
        if key not in existing:
            remote.put_verified(key,(root/key).read_bytes())
        elif existing[key]['size']!=(root/key).stat().st_size:
            raise ObjectStoreError('immutable R2 object size mismatch')
    remote.put_verified('manifest.json',(root/'manifest.json').read_bytes())
    # Keep unreachable objects for a full day so cached manifests/open tabs can
    # finish. Only derived objects in the dedicated bucket are eligible.
    removed=0
    for key,item in existing.items():
        if key not in needed and OBJECT.fullmatch(key) and (now-dt.datetime.fromisoformat(item['modified'].replace('Z','+00:00'))).total_seconds()>86400:
            remote.request('DELETE',key);removed+=1
    return {'objects':len(needed),'remote_bytes_before':used,'removed':removed}


def sync(config,root,now=None,export_only=False):
    now=now or dt.datetime.now(dt.timezone.utc)
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    cap=int(config.get('local_bytes',1024*MiB));free=int(config.get('min_free_bytes',2048*MiB))
    output_cap=int(config.get('output_bytes',512*MiB));workspace_cap=int(config.get('workspace_bytes',256*MiB))
    segment_cap=int(config.get('max_segment_bytes',32*MiB))
    if min(cap,output_cap,workspace_cap,segment_cap)<=0 or output_cap+workspace_cap+4*segment_cap+8*MiB>cap:
        raise ValueError('local cap must reserve output, SQLite workspace, input archive/restore and controls')
    if disk_usage(root)+workspace_cap+4*segment_cap>cap or shutil.disk_usage(root).free<free+workspace_cap+4*segment_cap:
        raise StorageFull('dashboard job storage budget/free-space floor reached')
    source_config=Config.load(config['archive_config']);source=S3(source_config);source.check_scope()
    prefix=source_config.s3_prefix+'segments/'
    grouped={}
    for item in list_objects(source,prefix):
        match=ARCHIVE.fullmatch(item['key'][len(prefix):])
        if not match:raise ValueError('unexpected archive object key')
        date='-'.join(match.group(1,2,3))
        item['sha']=match.group(4);grouped.setdefault(date,[]).append(item)
    state_path=root/'sync-state.json'
    state=load_json(state_path) if state_path.exists() else {'schema_version':1,'dates':{}}
    output=root/'published'
    published=load_json(output/'manifest.json') if (output/'manifest.json').exists() else {'days':[]}
    local_days={day['date']:day for day in published['days']}
    dates=set(grouped)
    dates.update((dt.date.fromisoformat(d)+dt.timedelta(days=1)).isoformat() for d in grouped)
    # Today's empty period also drives detail expiry even when endpoints are shut.
    dates.add(now.date().isoformat())
    exported=[]
    for date in sorted(d for d in dates if d<=now.date().isoformat()):
        previous=(dt.date.fromisoformat(date)-dt.timedelta(days=1)).isoformat()
        items=sorted(grouped.get(previous,[])+grouped.get(date,[]),key=lambda i:i['key'])
        if not items:continue
        fingerprint=hashlib.sha256(canonical([(i['key'],i['size']) for i in items]).encode()).hexdigest()
        cutoff=(now.date()-dt.timedelta(days=int(config.get('detail_days',14))-1)).isoformat()
        expected={'fingerprint':fingerprint,'detail':date>=cutoff}
        if state['dates'].get(date)==expected:continue
        local=local_days.get(date)
        if local and local['has_detail']==expected['detail'] and load_json(output/local['path']).get('source_fingerprint')==fingerprint:
            state['dates'][date]=expected
            continue
        with tempfile.TemporaryDirectory(prefix='inputs-',dir=root) as temporary:
            def paths():
                for item in items:
                    if item['size']>2*segment_cap:raise StorageFull('archive exceeds reserved input ceiling')
                    bundle=Path(temporary)/'input.tar.gz';database=Path(temporary)/'input.sealed.sqlite3'
                    for attempt in range(3):
                        try:
                            with bundle.open('wb') as stream:source.verify(item['key'],item['size'],item['sha'],destination=stream)
                            break
                        except (OSError,ObjectStoreError):
                            if attempt==2:raise
                            time.sleep(2**attempt)
                    restore_bundle(bundle,database,segment_cap)
                    try:yield database
                    finally:
                        database.unlink(missing_ok=True);bundle.unlink(missing_ok=True)
            export_day([],output,date,as_of=now,detail_days=int(config.get('detail_days',14)),
              max_output_bytes=output_cap,max_workspace_bytes=workspace_cap,min_free_bytes=free,
              workspace=root,path_factory=paths,source_fingerprint=fingerprint)
        state['dates'][date]=expected;exported.append(date)
    if not (output/'manifest.json').exists():return {'state':'empty_archive','exported':[]}
    if export_only:
        receipt={'objects':len(reachable(output,load_json(output/'manifest.json')))}
    else:
        remote=R2(config['r2_endpoint'],config['r2_bucket'],config['r2_credentials_file'])
        receipt=publish(output,remote,int(config.get('remote_bytes',8_000_000_000)),now)
    state.update(updated_at=now.isoformat())
    raw=canonical(state).encode()
    if len(raw)>MiB:raise StorageFull('dashboard checkpoint control ceiling reached')
    temporary=state_path.with_suffix('.partial');temporary.write_bytes(raw);temporary.replace(state_path)
    return {'state':'exported' if export_only else 'ok','exported':exported,**receipt,'local_bytes':disk_usage(root)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--export-only',action='store_true',help='materialize locally without R2 access')
    args=parser.parse_args()
    from .collector import exclusive
    with exclusive(args.output):
        result=sync(json.loads(args.config.read_text()),args.output,export_only=args.export_only)
        print(json.dumps(result))

if __name__=='__main__':main()
