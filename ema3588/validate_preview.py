"""Bounded visual check with automatic restoration, including interrupted SSH."""
import argparse
import json
import signal
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
import control as ctl


def interrupt(signum, frame):
    raise KeyboardInterrupt('Visual check interrupted by signal '+str(signum))


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--seconds',type=int,default=240)
    args=p.parse_args()
    if not 10<=args.seconds<=600: p.error('Use 10..600 seconds.')
    for signum in (signal.SIGTERM, signal.SIGHUP): signal.signal(signum,interrupt)
    finish=ctl.LOCAL/'finish-preview'
    if finish.exists(): finish.unlink()
    record={}
    try:
        state=ctl.start(SimpleNamespace(mode='dma',config=str(ctl.ROOT/'ema3588/config.json'),
            duration=args.seconds+30,encoder_delay_ms=0,take_camera=True))
        preview=subprocess.run(['python3',str(ctl.ROOT/'ema3588/preview_hdmi.py'),'--fullscreen'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=40)
        if preview.returncode: raise RuntimeError(preview.stderr)
        record['preview']=json.loads(preview.stdout.splitlines()[-1])
        pid=state['pid'];devices=[]
        for fd in Path('/proc/{}/fd'.format(pid)).iterdir():
            try:
                target=str(fd.resolve())
                if target.startswith('/dev/'): devices.append(target)
            except OSError: pass
        record['device_fds']=sorted(set(devices))
        record['started_at']=time.time()
        print(json.dumps({'ready':True,'pid':pid,'devices':record['device_fds'],
            'hdmi':record['preview'],'url':'rtsp://192.168.2.221:8556/imx415'}),flush=True)
        deadline=time.monotonic()+args.seconds
        while time.monotonic()<deadline and not finish.exists():
            if not ctl.status()['running']: raise RuntimeError('Stream exited during visual check.')
            time.sleep(.25)
        record['before_stop']=ctl.status()
    finally:
        record['stopped']=ctl.stop(True)
        record['restored_edgevision']=ctl.edge_command('status')
        (ctl.LOCAL/'visual-report.json').write_text(json.dumps(record,indent=2)+'\n')
        print(json.dumps({'finished':True,'record':str(ctl.LOCAL/'visual-report.json')}),flush=True)

if __name__=='__main__': main()
