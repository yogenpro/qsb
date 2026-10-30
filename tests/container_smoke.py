"""Run inside the built image with no network, a read-only root and a bounded tmpfs."""
import json
from pathlib import Path
import sqlite3
import tempfile
from qsb_capture.storage import Config,SegmentArchive,MiB,disk_usage
from qsb_capture.runtime import restore_bundle

config=Config(storage_bytes=32*MiB,min_free_bytes=0,segment_bytes=2*MiB,max_response_bytes=128*1024,cold_seconds=0)
root=Path('/data')
archive=SegmentArchive(root,config)
meta={'id':'smoke','sweep':'s','kind':'depth','symbol':'T1','type':'QSB','status':200,
      'started_at':'2026-10-29T20:00:00+00:00','finished_at':'2026-10-29T20:00:01+00:00'}
body=b'{"data":{"bids":[[1,1.2500,""]],"asks":[],"trades":[],"timestamp":"16:00:00"}}'
archive.save(meta,body)
assert archive.db.execute('SELECT price FROM quote_levels').fetchone()[0]=='1.2500'
archive.seal();archive.maintain(None,force=True)
bundle=next((root/'segments').glob('*.tar.gz'))
restore_bundle(bundle,root/'restored.sqlite3',config.segment_bytes)
assert disk_usage(root)<config.storage_bytes
archive.close()
print('Container storage/archive/restore smoke test passed')
