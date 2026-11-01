#!/usr/bin/env python3
"""Generate explicitly synthetic static files from the real exporter contract."""
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qsb_capture.storage import SegmentArchive,Config,MiB
from qsb_capture.dashboard import export_day


def make_demo(output):
    with tempfile.TemporaryDirectory() as tmp:
        archive=SegmentArchive(Path(tmp)/'capture',Config(storage_bytes=32*MiB,segment_bytes=2*MiB,max_response_bytes=128*1024,min_free_bytes=0))
        def meta(ident,kind='depth',symbol='DEMO-BOX',typ='QSB',minute=0,status=200):
            stamp=f'2026-10-29T{12+minute//60:02}:{minute%60:02}:00+00:00'
            return dict(id=ident,sweep='synthetic',kind=kind,symbol=symbol,type=typ,started_at=stamp,finished_at=stamp,status=status)
        reference={'currentTradingDate':'2026-10-29','data':[]}
        for typ,symbol,subtype in [('QSB','DEMO-BOX','BOX_SPREAD'),('CPM','DEMO-CONDOR','IRON_CONDOR')]:
            expiry='2026-10-30' if typ=='QSB' else '2026-11-06'
            osi_date='261030' if typ=='QSB' else '261106'
            legs=[dict(OSI_Symbol=f'DEMO  {osi_date}C00{100+i:03}000',OSI_ROOT='DEMO',EXPIRE_DATE=expiry,STRIKE=str(100+i*5)+'.0000',Leg_Ratio='1' if i%2 else '-1',BUY_SELL_FLAG='B' if i%2 else 'S') for i in range(4)]
            reference['data'].append(dict(Complex_Instrument_Type=typ,Complex_Instrument_Subtype=subtype,Symbols=[dict(Complex_Instrument_Id=symbol,Leg_Count=4,Leg_Data=legs)]))
        archive.save(meta('demo-reference','reference'),json.dumps(reference).encode())
        for typ,symbol in [('QSB','DEMO-BOX'),('CPM','DEMO-CONDOR')]:
            for i in range(30):
                m=meta(f'{symbol}-{i}',symbol=symbol,typ=typ,minute=i*5,status=503 if i==10 else 200)
                if i==10:archive.save(m,b'Synthetic outage');continue
                empty=i in (15,16)
                data={'data':{'bids':[] if empty else [[3+(i%4),f'{10+i%5/10:.4f}',''],[8,'9.8000','']], 'asks':[] if empty else [[2,f'{10.6+i%5/10:.4f}','']], 'trades':[['09:48:04.592403710',2,'10.1000'],['09:48:04.592403710',2,'10.1000']], 'timestamp':f'{9+(i*5)//60:02}:{i*5%60:02}:00'}}
                archive.save(m,json.dumps(data).encode())
        archive.close()
        export_day(list((Path(tmp)/'capture/segments').glob('*.sealed.sqlite3')),output,'2026-10-29',as_of=dt.datetime(2026,10,30,tzinfo=dt.timezone.utc),min_free_bytes=0,compressed=False)

if __name__=='__main__':make_demo(Path(sys.argv[1] if len(sys.argv)>1 else 'dashboard/public/demo-data'))
