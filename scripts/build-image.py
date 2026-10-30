#!/usr/bin/env python3
"""Build a Docker-loadable image from verified official Python layers without a daemon.
No package installation or third-party Python modules; same filesystem/config as Dockerfile.
"""
import argparse
import copy
import datetime as dt
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tomllib
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
VERSION=tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
REGISTRY='https://registry-1.docker.io/v2/library/python/'
ACCEPT=','.join(['application/vnd.oci.image.index.v1+json','application/vnd.docker.distribution.manifest.list.v2+json',
                'application/vnd.oci.image.manifest.v1+json','application/vnd.docker.distribution.manifest.v2+json'])

def sha(data):return 'sha256:'+hashlib.sha256(data).hexdigest()
def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--resolve-base',action='store_true');p.add_argument('--tag',default='qsb-capture:'+VERSION);p.add_argument('--output',type=Path,default=ROOT/('dist/qsb-capture-'+VERSION+'-amd64.tar'));args=p.parse_args()
 token=json.load(urllib.request.urlopen('https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull',timeout=60))['token']
 def fetch(path):
  req=urllib.request.Request(REGISTRY+path,headers={'Authorization':'Bearer '+token,'Accept':ACCEPT})
  return urllib.request.urlopen(req,timeout=120)
 lockpath=ROOT/'deploy/image-base.json'
 if args.resolve_base:
  with fetch('manifests/3.12-slim') as r:index=json.load(r)
  entry=next(x for x in index['manifests'] if x.get('platform')=={'architecture':'amd64','os':'linux'})
  lock={'repository':'docker.io/library/python','tag':'3.12-slim','platform':'linux/amd64','digest':entry['digest']}
  lockpath.write_text(json.dumps(lock,indent=2)+'\n')
 else:lock=json.loads(lockpath.read_text())
 if lock['digest'] not in (ROOT/'Dockerfile').read_text():raise ValueError('Dockerfile and base lock disagree; update both deliberately')
 with fetch('manifests/'+lock['digest']) as r:raw=r.read()
 if sha(raw)!=lock['digest']:raise ValueError('base manifest digest mismatch')
 manifest=json.loads(raw)
 with fetch('blobs/'+manifest['config']['digest']) as r:rawconfig=r.read()
 if sha(rawconfig)!=manifest['config']['digest']:raise ValueError('base config digest mismatch')
 config=json.loads(rawconfig)
 if config['architecture']!='amd64' or config['os']!='linux':raise ValueError('unexpected base platform')
 cache=ROOT/'dist/base-cache';cache.mkdir(parents=True,exist_ok=True)
 layers=[]
 for i,layer in enumerate(manifest['layers']):
  digest=layer['digest'];path=cache/digest.replace(':','_')
  if not path.exists():
   print('Downloading base layer',i+1,flush=True)
   partial=path.with_suffix('.partial')
   with fetch('blobs/'+digest) as response,partial.open('wb') as out:
    while chunk:=response.read(1024*1024):out.write(chunk)
   partial.replace(path)
  h=hashlib.sha256()
  with path.open('rb') as f:
   while chunk:=f.read(1024*1024):h.update(chunk)
  if 'sha256:'+h.hexdigest()!=digest:raise ValueError('base layer digest mismatch')
  if 'gzip' not in layer['mediaType']:raise ValueError('unsupported base layer compression')
  size=0;h=hashlib.sha256()
  with gzip.open(path,'rb') as f:
   while chunk:=f.read(1024*1024):size+=len(chunk);h.update(chunk)
  if 'sha256:'+h.hexdigest()!=config['rootfs']['diff_ids'][i]:raise ValueError('uncompressed layer digest mismatch')
  layers.append((path,size,config['rootfs']['diff_ids'][i].split(':')[1]+'/layer.tar'))
 # Source allowlist: credentials, captures, build cache and neighboring infra are excluded.
 app=io.BytesIO()
 with tarfile.open(fileobj=app,mode='w') as tar:
  for path in sorted((ROOT/'qsb_capture').glob('*'))+[ROOT/'config/example.json']:
   if not path.is_file() or path.suffix not in ('.py','.sql','.json'):continue
   body=path.read_bytes();info=tarfile.TarInfo('app/config/collector.json' if path.name=='example.json' else 'app/'+str(path.relative_to(ROOT)));info.size=len(body);info.mode=0o444
   tar.addfile(info,io.BytesIO(body))
  for name,mode in [('app',0o555),('app/qsb_capture',0o555),('app/config',0o555),('data',0o700)]:
   info=tarfile.TarInfo(name);info.type=tarfile.DIRTYPE;info.mode=mode
   if name=='data':info.uid=65532;info.gid=65532
   tar.addfile(info)
 appbytes=app.getvalue();appid=sha(appbytes)
 final=copy.deepcopy(config)
 final['created']='1970-01-01T00:00:00Z'
 final['rootfs']['diff_ids'].append(appid)
 final['history']=final.get('history',[])+[{'created':'1970-01-01T00:00:00Z','created_by':'qsb bounded collector v'+VERSION}]
 final['config'].update({'User':'65532:65532','WorkingDir':'/app','Entrypoint':['python','-m','qsb_capture.runtime'],
  'Cmd':['run'],'StopSignal':'SIGTERM','Healthcheck':{'Test':['CMD','python','-m','qsb_capture.runtime','check-health'],
  'Interval':30000000000,'Timeout':10000000000,'StartPeriod':60000000000,'Retries':3}})
 final['config']['Env']=[e for e in final['config'].get('Env',[]) if not e.startswith(('PYTHONDONTWRITEBYTECODE=','PYTHONUNBUFFERED='))]+['PYTHONDONTWRITEBYTECODE=1','PYTHONUNBUFFERED=1']
 final['config']['Labels']={'org.opencontainers.image.source':'https://github.com/yogenpro/qsb','org.opencontainers.image.title':'Cboe QSB/CPM bounded collector','org.opencontainers.image.version':VERSION,
  'org.opencontainers.image.base.digest':lock['digest'],'org.opencontainers.image.revision':appid.split(':')[1]}
 final.pop('container_config',None)
 cfg=encoded(final);cfgname=sha(cfg).split(':')[1]+'.json';applayer=appid.split(':')[1]+'/layer.tar'
 docker_manifest=[{'Config':cfgname,'RepoTags':[args.tag],'Layers':[x[2] for x in layers]+[applayer]}]
 args.output.parent.mkdir(parents=True,exist_ok=True)
 tmp=args.output.with_suffix('.partial')
 with tarfile.open(tmp,'w') as tar:
  for path,size,name in layers:
   info=tarfile.TarInfo(name);info.size=size;info.mode=0o444
   with gzip.open(path,'rb') as f:tar.addfile(info,f)
  for name,body in [(applayer,appbytes),(cfgname,cfg),('manifest.json',encoded(docker_manifest))]:
   info=tarfile.TarInfo(name);info.size=len(body);info.mode=0o444;tar.addfile(info,io.BytesIO(body))
 tmp.replace(args.output)
 h=hashlib.sha256()
 with args.output.open('rb') as f:
  while chunk:=f.read(1024*1024):h.update(chunk)
 receipt={'tag':args.tag,'platform':'linux/amd64','image_id':sha(cfg),'base_digest':lock['digest'],'source_layer_digest':appid,
  'archive':args.output.name,'archive_sha256':h.hexdigest(),'archive_bytes':args.output.stat().st_size}
 args.output.with_suffix('.json').write_text(json.dumps(receipt,indent=2)+'\n')
 print(json.dumps(receipt,indent=2))

if __name__=='__main__':main()
