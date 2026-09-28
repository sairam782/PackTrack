"""Verify the paired phone HTTP flow with QR photos and a temporary database."""
import base64
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import requests

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='packtrack-phone-check-') as folder:
        api_url, phone_url = 'http://127.0.0.1:8774', 'http://127.0.0.1:8775'
        token = secrets.token_urlsafe(32)
        env = {**os.environ, 'PACKTRACK_DB_URL': f'sqlite:///{folder}/test.db', 'PACKTRACK_DEMO':'0',
               'PACKTRACK_API_BASE_URL':api_url, 'PACKTRACK_PHONE_TOKEN':token,
               'PACKTRACK_PHONE_LOCATIONS':str(ROOT/'examples/phone-locations.demo.json')}
        processes=[]
        try:
            for module, port in [('api:app',8774),('phone_server:app',8775)]:
                processes.append(subprocess.Popen([sys.executable,'-m','uvicorn',module,'--host','127.0.0.1','--port',str(port),'--log-level','warning'],cwd=ROOT,env=env))
            for process, url in zip(processes,[api_url+'/dashboard',phone_url+'/health']):
                for _ in range(80):
                    if process.poll() is not None:
                        raise RuntimeError('Test service exited; ports 8774 and 8775 must be free')
                    try:
                        requests.get(url,timeout=1).raise_for_status();break
                    except requests.RequestException:
                        time.sleep(.1)
                else:
                    raise RuntimeError('Test service startup timed out')
            assert requests.get(phone_url+'/phone/config',timeout=5).status_code==401
            headers={'X-PackTrack-Token':token}
            assert requests.get(phone_url+'/phone/config',headers=headers,timeout=5).json()['example_coordinates']
            device=str(uuid4())
            def scan(path, location=None):
                payload={'request_id':str(uuid4()),'device_id':device,'captured_at':datetime.now(timezone.utc).isoformat(),
                         'image':base64.b64encode((ROOT/path).read_bytes()).decode(),'location_token':location}
                response=requests.post(phone_url+'/phone/scan',json=payload,headers=headers,timeout=10)
                response.raise_for_status()
                return response.json(), payload
            marker,_=scan('assets/locations/location-1.png')
            result,payload=scan('assets/qr/TEST-001.png',marker['location_token'])
            assert result['results'][0]['status']=='placed'
            retry=requests.post(phone_url+'/phone/scan',json=payload,headers=headers,timeout=10)
            retry.raise_for_status()
            boxes=requests.get(api_url+'/boxes',timeout=5).json()
            assert boxes[0]['position_source']=='location_marker' and boxes[0]['floor_x']==2 and boxes[0]['floor_y']==3
            history=requests.get(api_url+'/boxes/TEST-001/observations',timeout=5).json()
            assert len(history)==1
            marker,_=scan('assets/locations/location-4.png')
            result,_=scan('assets/qr/TEST-001.png',marker['location_token'])
            assert result['results'][0]['status']=='collected'
            stats=requests.get(api_url+'/dashboard',timeout=5).json()
            assert stats['present_boxes']==0 and stats['collected_today']==1
            print('PHONE VERIFICATION PASSED: pairing, marker -> box -> dashboard, retry deduplication, truck collection.')
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait()


if __name__=='__main__':
    main()
