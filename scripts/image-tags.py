#!/usr/bin/env python3
"""Generate registry tags from GitHub-owned context; reject unrecognized refs."""
import os
import re
import subprocess
import tomllib
from pathlib import Path

ref=os.environ['GITHUB_REF'];commit=os.environ['GITHUB_SHA'];repo=os.environ['GITHUB_REPOSITORY'].lower()
if repo!='yogenpro/qsb' or not re.fullmatch('[0-9a-f]{40}',commit):
    raise SystemExit('unexpected repository or commit')
image='ghcr.io/'+repo
tags=[image+':sha-'+commit]
if ref=='refs/heads/main':
    tags.append(image+':main')
elif re.fullmatch(r'refs/tags/v\d+\.\d+\.\d+',ref):
    tag=ref.removeprefix('refs/tags/')
    version=tomllib.loads(Path('pyproject.toml').read_text())['project']['version']
    if tag!='v'+version:raise SystemExit('release tag must match project version')
    subprocess.run(['git','merge-base','--is-ancestor',commit,'origin/main'],check=True)
    tags.extend([image+':'+tag,image+':latest'])
else:
    raise SystemExit('publishing is restricted to main and version tags on main')
with open(os.environ['GITHUB_OUTPUT'],'a') as output:
    output.write('tags<<TAGS\n'+'\n'.join(tags)+'\nTAGS\n')
