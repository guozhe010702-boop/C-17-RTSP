"""Private board process manager. Video capture/RGA/MPP/mux remain C++17."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT/'local'
STATE = LOCAL/'session.json'
EDGE = Path('/userdata/edgevision-yolov8')


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else (default if default is not None else {})


def save(state):
    LOCAL.mkdir(exist_ok=True)
    temp = STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(state, indent=2)+'\n')
    temp.chmod(0o600)
    temp.replace(STATE)


def identity(pid):
    try:
        fields = Path('/proc/{}/stat'.format(pid)).read_text().rsplit(')', 1)[1].split()
        if fields[0] != 'Z': return fields[19]
    except OSError: pass
    return None


def alive(process):
    return bool(process and process.get('pid') and process.get('start_ticks') and
                identity(process['pid']) == process['start_ticks'])


def launch(command, log):
    environment = os.environ.copy()
    for key in ('LD_LIBRARY_PATH', 'LD_PRELOAD'):
        environment.pop(key, None)
    with log.open('ab') as output:
        p = subprocess.Popen(command, cwd=str(ROOT), env=environment, stdin=subprocess.DEVNULL,
                             stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    return {'pid': p.pid, 'start_ticks': identity(p.pid), 'executable': str(Path(command[0]).resolve()),
            'log': str(log)}


def terminate(process):
    if not alive(process): return
    executable = Path(os.readlink('/proc/{}/exe'.format(process['pid'])).split(' (deleted)', 1)[0])
    if executable.resolve() != Path(process['executable']).resolve() or executable.parent != ROOT/'runtime/bin':
        raise RuntimeError('Stored PID is not an owned runtime process; refusing to signal it.')
    os.killpg(process['pid'], signal.SIGTERM)
    deadline = time.monotonic()+20
    while alive(process) and time.monotonic()<deadline: time.sleep(.05)
    if alive(process):
        os.killpg(process['pid'], signal.SIGKILL)
        raise RuntimeError('Process required SIGKILL; inspect log: '+process['log'])


def owners(device):
    p = subprocess.run(['lsof', '-t', device], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    return sorted(set(int(x) for x in p.stdout.split() if x.isdigit()))


def edge_command(*args):
    result = subprocess.run(['python3', str(EDGE/'ema3588/board_control.py')]+list(args),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=45)
    if result.returncode: raise RuntimeError('EdgeVision control failed: '+result.stderr[-2000:])
    return json.loads(result.stdout.splitlines()[-1])


def take_camera(state, device):
    pids = owners(device)
    if not pids: return
    edge = edge_command('status')
    if not edge.get('running') or pids != [edge.get('pid')]:
        raise RuntimeError('Camera is occupied by unrelated PIDs {}; stop that application first.'.format(pids))
    preview = read_json(EDGE/'local/hdmi-preview.json')
    player = preview.get('placement', {}).get('player_pid')
    preview_active = False
    if player and identity(player):
        command = Path('/proc/{}/cmdline'.format(player)).read_bytes().split(b'\0')
        preview_active = b'EdgeVision IMX415 HDMI' in command
    state['previous'] = {'application': 'EdgeVision', 'profile': edge['profile'],
                         'hdmi': preview_active, 'fullscreen': bool(preview.get('fullscreen')),
                         'pending_restore': True}
    save(state)  # Persist rollback BEFORE interrupting the old application.
    if preview_active:
        subprocess.run(['python3', str(EDGE/'ema3588/open_hdmi_preview.py'), '--stop'], check=True, timeout=15)
    edge_command('stop')
    if owners(device): raise RuntimeError('Camera was not released by EdgeVision.')


def restore_previous(state):
    previous = state.get('previous', {})
    if not previous.get('pending_restore'): return
    if owners(state.get('config', {}).get('device', '/dev/video44')):
        raise RuntimeError('Camera is still occupied; did not restart the previous application.')
    edge_command('start', '--profile', previous['profile'])
    if previous.get('hdmi'):
        flag = '--fullscreen' if previous.get('fullscreen') else '--windowed'
        subprocess.run(['python3', str(EDGE/'ema3588/open_hdmi_preview.py'), flag], check=True, timeout=40)
    previous['pending_restore'] = False
    state['restored_at'] = time.time()
    save(state)


def load_config(path):
    c = read_json(path)
    for key, low, high in (('width',32,3840),('height',16,2160),('fps',1,60),('buffers',4,32),
                          ('bitrate',100000,200000000),('gop',1,600),('frame_queue',1,8),
                          ('packet_queue',1,128),('rtsp_port',1024,65535)):
        if not isinstance(c.get(key), int) or not low <= c[key] <= high:
            raise ValueError('Invalid config: '+key)
    if c['width']%2 or c['height']%2 or c.get('format') not in ('nv12','nv12m'):
        raise ValueError('Only even-sized NV12/NV12M input is supported.')
    if c.get('path') != 'imx415' or not c.get('device', '').startswith('/dev/video'):
        raise ValueError('This adapter uses path imx415 and a /dev/video capture node.')
    return c


def read_fields(line):
    result = {}
    for token in shlex.split(line)[1:]:
        key, sep, value = token.partition('=')
        if not sep: continue
        if value in ('true','false'): result[key] = value == 'true'
        else:
            try: result[key] = float(value) if '.' in value else int(value)
            except ValueError: result[key] = value
    return result


def status():
    state = read_json(STATE)
    running = alive(state.get('stream'))
    snapshots = []
    summary = None
    log = state.get('stream', {}).get('log')
    if log and Path(log).exists():
        with Path(log).open('rb') as f:
            f.seek(0,2); f.seek(max(0,f.tell()-65536))
            for line in f.read().decode(errors='replace').splitlines():
                try:
                    if line.startswith(('STATUS ', 'STREAM_STATUS ', 'DMABUF_STATUS ')): snapshots.append(read_fields(line))
                    if line.startswith(('STREAM_SUMMARY ', 'DMABUF_STREAM_SUMMARY ')): summary=read_fields(line)
                except ValueError: pass
    metrics = snapshots[-1] if snapshots else {}
    age = max(0, time.monotonic()-metrics['sample_us']/1e6) if 'sample_us' in metrics else None
    rates = {}
    if len(snapshots)>1:
        a,b=snapshots[-2:]; delta=(b.get('sample_us',0)-a.get('sample_us',0))/1e6
        if delta>0:
            rates={key+'_fps':(b[key]-a[key])/delta for key in ('captured','encoded','published')
                   if key in a and key in b and b[key]>=a[key]}
    return {'running':running,'server_running':alive(state.get('server')),'mode':state.get('mode'),
            'pid':state.get('stream',{}).get('pid'),'config':state.get('config'),
            'local_url':state.get('url'),'log':log,'metric_age_seconds':age,
            'metrics_stale':not running or age is None or age>3,'metrics':metrics,'rates':rates,
            'summary':summary,'previous_restore_pending':state.get('previous',{}).get('pending_restore',False)}


def stop(restore=False):
    state = read_json(STATE)
    errors=[]
    if (LOCAL/'hdmi-preview.json').exists():
        try:
            subprocess.run(['python3',str(ROOT/'ema3588/preview_hdmi.py'),'--stop'],
                           check=True,stdout=subprocess.DEVNULL,timeout=15)
        except Exception as e: errors.append('Preview cleanup: '+str(e))
    for key in ('stream','server'):
        try: terminate(state.get(key))
        except Exception as e: errors.append(str(e))
    state['stopped_at']=time.time()
    save(state)
    if restore:
        try: restore_previous(state)
        except Exception as e: errors.append('Restore: '+str(e))
    if errors: raise RuntimeError('; '.join(errors))
    return status()


def start(args):
    c=load_config(Path(args.config))
    state=read_json(STATE)
    if alive(state.get('stream')):
        if state.get('config') != c or state.get('mode')!=args.mode:
            raise RuntimeError('A different configuration is running. Stop it before changing parameters.')
        return status()
    if args.mode=='dma' and c['format']!='nv12':
        raise ValueError('DMA requires contiguous single-memory-plane NV12; use CPU mode for NV12M.')
    if args.duration<0 or args.encoder_delay_ms<0: raise ValueError('Duration/delay must be nonnegative.')
    subprocess.run(['sha256sum','--quiet','-c','SHA256SUMS'],cwd=str(ROOT/'runtime'),check=True)
    with socket.socket() as port_check:
        port_check.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        port_check.bind(('0.0.0.0',c['rtsp_port']))
    state={key:value for key,value in state.items() if key=='previous'}
    state.update(config=c,mode=args.mode,started_at=time.time())
    save(state)
    try:
        if owners(c['device']):
            if not args.take_camera: raise RuntimeError('Camera busy: {}; add --take-camera for known EdgeVision handoff.'.format(owners(c['device'])))
            take_camera(state,c['device'])
        stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        run=LOCAL/stamp;run.mkdir()
        config=run/'mediamtx.yml'
        config.write_text('''logLevel: info
rtsp: true
rtspAddress: :PORT
rtspTransports: [tcp]
rtmp: false
hls: false
webrtc: false
srt: false
moq: false
api: false
metrics: false
authInternalUsers:
  - user: any
    pass:
    ips: ['127.0.0.1']
    permissions:
      - action: publish
        path: imx415
  - user: any
    pass:
    ips: []
    permissions:
      - action: read
        path: imx415
paths:
  imx415:
    source: publisher
'''.replace('PORT',str(c['rtsp_port'])))
        state['server']=launch([str(ROOT/'runtime/bin/mediamtx'),str(config)],run/'mediamtx.log')
        save(state)
        deadline=time.monotonic()+10
        while True:
            if not alive(state['server']): raise RuntimeError('MediaMTX exited; inspect '+state['server']['log'])
            try:
                with socket.create_connection(('127.0.0.1',c['rtsp_port']),timeout=.3): break
            except OSError:
                if time.monotonic()>deadline: raise RuntimeError('MediaMTX listener timeout.')
                time.sleep(.1)
        url='rtsp://127.0.0.1:{}/{}'.format(c['rtsp_port'],c['path'])
        binary='dmabuf_rtsp_stream' if args.mode=='dma' else 'rtsp_stream'
        command=[str(ROOT/'runtime/bin'/binary),'--device',c['device'],'--url',url,
                 '--duration',str(args.duration)]
        for key,flag in (('width','width'),('height','height'),('fps','fps'),('buffers','buffers'),
                         ('bitrate','bitrate'),('gop','gop'),('format','format'),
                         ('frame_queue','frame-queue'),('packet_queue','packet-queue')):
            command+=['--'+flag,str(c[key])]
        if args.mode=='dma': command+=['--encoder-delay-ms',str(args.encoder_delay_ms)]
        state['command']=command; state['url']=url
        state['stream']=launch(command,run/'stream.log')
        save(state)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            if not alive(state['stream']):
                raise RuntimeError('C++ stream exited: '+Path(state['stream']['log']).read_text(errors='replace')[-2500:])
            if 'STREAM_READY' in Path(state['stream']['log']).read_text(errors='replace'): return status()
            time.sleep(.1)
        raise RuntimeError('C++ stream did not become ready within 15 seconds.')
    except BaseException:
        stop(restore=True)
        raise


def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=('start','stop','status'))
    p.add_argument('--mode',choices=('cpu','dma'),default='dma')
    p.add_argument('--config',default=str(ROOT/'ema3588/config.json'))
    p.add_argument('--duration',type=int,default=0)
    p.add_argument('--encoder-delay-ms',type=int,default=0)
    p.add_argument('--take-camera',action='store_true')
    p.add_argument('--restore-previous',action='store_true')
    args=p.parse_args()
    LOCAL.mkdir(exist_ok=True)
    try:
        if args.action=='status': result=status()
        else:
            with (LOCAL/'control.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                result=start(args) if args.action=='start' else stop(args.restore_previous)
        print(json.dumps(result,ensure_ascii=True))
    except Exception as e:
        print(json.dumps({'ok':False,'error':str(e)},ensure_ascii=True))
        raise SystemExit(1)
if __name__=='__main__': main()
