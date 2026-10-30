"""Small streaming S3 SigV4 client; verifies B2 key scope and full object read-back."""
from __future__ import annotations
import base64
import datetime as dt
import hashlib
import hmac
import http.client
import json
import logging
import os
from pathlib import Path
import stat
import urllib.parse

EMPTY = hashlib.sha256(b'').hexdigest()
LOG = logging.getLogger('qsb.object_store')
FILE_CAPABILITIES = frozenset({'listBuckets','listAllBucketNames','readBuckets','listFiles',
    'readFiles','writeFiles','deleteFiles','shareFiles','readBucketEncryption'})
CONSOLE_BUCKET_CAPABILITIES = frozenset({'writeBuckets','writeBucketEncryption',
    'readBucketLifecycleRules','writeBucketLifecycleRules','readBucketLogging','writeBucketLogging',
    'readBucketNotifications','writeBucketNotifications','readBucketReplications','writeBucketReplications'})

class ObjectStoreError(RuntimeError):
    pass


def signing_headers(method, url, region, access_id, secret, payload_hash=EMPTY, extra=None, instant=None):
    instant = instant or dt.datetime.now(dt.timezone.utc)
    stamp=instant.strftime('%Y%m%dT%H%M%SZ'); day=stamp[:8]
    parsed=urllib.parse.urlsplit(url)
    headers={'host':parsed.netloc,'x-amz-date':stamp,'x-amz-content-sha256':payload_hash}
    headers.update({k.lower():' '.join(str(v).split()) for k,v in (extra or {}).items()})
    names=';'.join(sorted(headers))
    canonical_headers=''.join(k+':'+headers[k]+'\n' for k in sorted(headers))
    query='&'.join(urllib.parse.quote(k,safe='-_.~')+'='+urllib.parse.quote(v,safe='-_.~')
        for k,v in sorted(urllib.parse.parse_qsl(parsed.query,keep_blank_values=True)))
    canonical='\n'.join([method,parsed.path or '/',query,canonical_headers,names,payload_hash])
    scope=day+'/'+region+'/s3/aws4_request'
    to_sign='AWS4-HMAC-SHA256\n'+stamp+'\n'+scope+'\n'+hashlib.sha256(canonical.encode()).hexdigest()
    def mac(key,value):return hmac.new(key,value.encode(),hashlib.sha256).digest()
    key=mac(mac(mac(mac(('AWS4'+secret).encode(),day),region),'s3'),'aws4_request')
    signature=hmac.new(key,to_sign.encode(),hashlib.sha256).hexdigest()
    headers['authorization']='AWS4-HMAC-SHA256 Credential='+access_id+'/'+scope+', SignedHeaders='+names+', Signature='+signature
    return headers


def validate_scope(document, config):
    storage=document['apiInfo']['storageApi']; allowed=storage['allowed']
    buckets=allowed.get('buckets')
    if not isinstance(buckets,list) or len(buckets)!=1 or buckets[0].get('name')!=config.s3_bucket or not buckets[0].get('id'):
        raise ObjectStoreError('B2 key must be restricted to exactly the configured bucket')
    if allowed.get('namePrefix') != config.s3_prefix:
        raise ObjectStoreError('B2 key must be restricted to exactly the configured prefix, including trailing slash')
    caps=set(allowed.get('capabilities',[]))
    profile=config.b2_capability_profile
    if profile not in ('file-only','console-read-write'):
        raise ObjectStoreError('unknown B2 capability profile')
    accepted=FILE_CAPABILITIES | (CONSOLE_BUCKET_CAPABILITIES if profile=='console-read-write' else frozenset())
    if not {'readFiles','writeFiles'} <= caps or caps-accepted:
        raise ObjectStoreError('B2 key lacks required file rights or exceeds the selected capability profile')
    if storage.get('s3ApiUrl','').rstrip('/') != config.s3_endpoint.rstrip('/'):
        raise ObjectStoreError('B2 authorization reports a different S3 endpoint')
    if profile=='console-read-write':
        LOG.warning('console-read-write B2 profile accepted: bucket-setting privileges are bucket-wide, not prefix-isolated')


class S3:
    def __init__(self, config, credentials=None, allow_http=False):
        self.config=config
        parsed=urllib.parse.urlsplit(config.s3_endpoint)
        if parsed.scheme not in (['https','http'] if allow_http else ['https']) or not parsed.hostname or parsed.path not in ('','/') or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('S3 endpoint must be an HTTPS origin')
        if not allow_http and not parsed.hostname.endswith('.backblazeb2.com'):
            raise ValueError('production endpoint must be Backblaze B2')
        self.allow_http=allow_http
        if credentials is None:
            path=Path(config.credentials_file)
            fd=os.open(path,os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd,'r') as f:
                mode=stat.S_IMODE(os.fstat(f.fileno()).st_mode)
                if mode not in (0o400,0o600,0o440) or not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                    raise ValueError('credential file must be 0400, 0600 or 0440')
                credentials=json.load(f)
        if set(credentials) != {'application_key_id','application_key'} or not all(isinstance(v,str) and v for v in credentials.values()):
            raise ValueError('expected application_key_id and application_key in credential JSON')
        self.access_id=credentials['application_key_id'];self.secret=credentials['application_key']
        self.scope_checked_at=None

    def _request(self, method, url, headers, body=None):
        parsed=urllib.parse.urlsplit(url)
        factory=http.client.HTTPSConnection if parsed.scheme=='https' else http.client.HTTPConnection
        conn=factory(parsed.hostname,parsed.port,timeout=30)
        path=parsed.path or '/'
        if parsed.query:path+='?'+parsed.query
        try:
            conn.request(method,path,body=body,headers=headers)
            response=conn.getresponse()
            return conn,response
        except Exception:
            conn.close();raise

    def check_scope(self):
        if self.scope_checked_at and (dt.datetime.now(dt.timezone.utc)-self.scope_checked_at).total_seconds()<3600:
            return
        token=base64.b64encode((self.access_id+':'+self.secret).encode()).decode()
        conn,response=self._request('GET','https://api.backblazeb2.com/b2api/v4/b2_authorize_account',{'Authorization':'Basic '+token})
        try:
            if response.status!=200:
                raise ObjectStoreError('B2 scope authorization failed (HTTP '+str(response.status)+')')
            body=response.read(1024*1024+1)
            if len(body)>1024*1024:raise ObjectStoreError('B2 authorization response too large')
            try:
                validate_scope(json.loads(body),self.config)
            except (KeyError,TypeError,ValueError) as e:
                raise ObjectStoreError('malformed B2 authorization response') from e
            self.scope_checked_at=dt.datetime.now(dt.timezone.utc)
        finally:conn.close()

    def _url(self,key,version=None):
        if not key.startswith(self.config.s3_prefix) or any(p in ('..','.') for p in key.split('/')):
            raise ValueError('object key outside configured prefix')
        url=self.config.s3_endpoint.rstrip('/')+'/'+urllib.parse.quote(self.config.s3_bucket,safe='')+'/'+urllib.parse.quote(key,safe='/-_.~')
        if version:url+='?versionId='+urllib.parse.quote(version,safe='-_.~')
        return url

    def _signed(self, method, key, extra=None, body=None, sha=EMPTY, version=None):
        url=self._url(key,version)
        return self._request(method,url,signing_headers(method,url,self.config.s3_region,self.access_id,self.secret,sha,extra),body)

    def upload_verified(self,path,key,sha):
        self.check_scope()
        size=Path(path).stat().st_size
        conn,r=self._signed('HEAD',key)
        try:
            status=r.status;version=r.getheader('x-amz-version-id')
            if status not in (200,404):raise ObjectStoreError('S3 HEAD failed (HTTP '+str(status)+')')
        finally:conn.close()
        if status==404:
            with Path(path).open('rb') as f:
                conn,r=self._signed('PUT',key,{'content-length':str(size),'content-type':'application/gzip',
                    'x-amz-meta-sha256':sha,'x-amz-server-side-encryption':'AES256'},f,sha)
                try:
                    if r.status!=200:raise ObjectStoreError('S3 PUT failed (HTTP '+str(r.status)+')')
                    version=r.getheader('x-amz-version-id')
                finally:conn.close()
        self.verify(key,size,sha,version)

    def verify(self,key,size,sha,version=None,destination=None):
        self.check_scope()
        conn,r=self._signed('GET',key,{'cache-control':'no-cache'},version=version)
        try:
            if r.status!=200:raise ObjectStoreError('S3 GET failed (HTTP '+str(r.status)+')')
            if r.getheader('content-length') != str(size):raise ObjectStoreError('remote object size mismatch')
            h=hashlib.sha256();count=0
            while True:
                chunk=r.read(min(1024*1024,size-count+1))
                if not chunk:break
                count+=len(chunk)
                if count>size:raise ObjectStoreError('remote object exceeds expected size')
                h.update(chunk)
                if destination is not None:destination.write(chunk)
            if count!=size or h.hexdigest()!=sha:raise ObjectStoreError('remote object hash mismatch')
        finally:conn.close()
