"""Real-camera matched A/B + pressure/reconnect/failure acceptance; always restore."""
import argparse
import csv
import datetime
import json
import os
from pathlib import Path
import signal
import statistics
import subprocess
import time
from types import SimpleNamespace
import control as ctl

FFMPEG=Path('/userdata/av_dev/ffmpeg_pipeline/runtime/bin/ffmpeg')


def decode(url, seconds, folder):
    progress=folder/'decode-progress.txt'; error=folder/'decode-error.log'
    started=time.monotonic(); first=None
    command=[str(FFMPEG),'-hide_banner','-nostdin','-loglevel','error','-xerror',
             '-threads','2','-rtsp_transport','tcp','-timeout','5000000','-i',url,
             '-map','0:v:0','-an','-t',str(seconds),'-progress',str(progress),'-f','null','-']
    with error.open('wb') as output:
        p=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                           stderr=output,start_new_session=True)
        try:
            while p.poll() is None:
                if progress.exists() and first is None:
                    values=dict(row.split('=',1) for row in progress.read_text().splitlines() if '=' in row)
                    if int(values.get('frame','0'))>0: first=time.monotonic()-started
                if time.monotonic()-started>seconds+12: raise TimeoutError('Decoder stalled.')
                time.sleep(.05)
        finally:
            if p.poll() is None:
                os.killpg(p.pid,signal.SIGTERM)
                try: p.wait(timeout=3)
                except subprocess.TimeoutExpired: os.killpg(p.pid,signal.SIGKILL);p.wait()
    values=dict(row.split('=',1) for row in progress.read_text().splitlines() if '=' in row) if progress.exists() else {}
    result={'exit_code':p.returncode,'decoded_frames':int(values.get('frame','0')),
            'wall_seconds':time.monotonic()-started,'first_decoded_frame_wait_s':first,
            'error_bytes':error.stat().st_size,'command':command}
    if p.returncode or not result['decoded_frames'] or result['error_bytes']:
        raise RuntimeError('Strict RTSP decode failed: '+error.read_text(errors='replace')[-2000:])
    return result


def sensor_snapshot():
    result={}
    for item in Path('/sys/class/video4linux').glob('v4l-subdev*'):
        name=(item/'name').read_text().strip()
        if 'imx415' not in name: continue
        node='/dev/'+item.name
        args=['v4l2-ctl','-d',node,'--list-ctrls']
        p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        result={'node':node,'name':name,'controls':p.stdout,'stderr':p.stderr,'exit_code':p.returncode}
    return result


def start(mode,config,seconds=0,delay=0):
    return ctl.start(SimpleNamespace(mode=mode,config=str(config),duration=seconds,
                                    encoder_delay_ms=delay,take_camera=True))


def finish_when_done(seconds):
    deadline=time.monotonic()+seconds+15
    while ctl.status()['running'] and time.monotonic()<deadline: time.sleep(.1)
    if ctl.status()['running']: raise RuntimeError('C++ process exceeded the bounded test duration.')
    value=ctl.status()
    ctl.stop(False)
    if not value.get('summary'): raise RuntimeError('Missing C++ summary: '+str(value.get('log')))
    return value


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--seconds',type=int,default=20)
    parser.add_argument('--config',default=str(ctl.ROOT/'ema3588/config.json'))
    args=parser.parse_args()
    if args.seconds<12: parser.error('Use at least 12 seconds per comparison.')
    config=Path(args.config)
    stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    folder=ctl.LOCAL/('acceptance-'+stamp);folder.mkdir(parents=True)
    report={'scope':'Real IMX415; no synthetic input; short bounded experiments, not glass-to-glass latency',
            'config':ctl.load_config(config),'records':[],'started_at':time.time(),
            'exposure_policy':'Vendor ISP/3A unchanged; controls sampled, not claimed locked.',
            'initial_edgevision':ctl.edge_command('status')}
    try:
        # ABBA reduces order/warm-up bias. Both use identical capture/codec/queue/client settings.
        for index,mode in enumerate(('cpu','dma','dma','cpu')):
            run=folder/('{}-{}'.format(index+1,mode));run.mkdir()
            before=sensor_snapshot(); state=start(mode,config,args.seconds)
            decoder=decode(state['local_url'],min(8,args.seconds-4),run)
            end=finish_when_done(args.seconds)
            entry={'test':'matched-abba','mode':mode,'sensor_before':before,'sensor_after':sensor_snapshot(),
                   'decode':decoder,'summary':end['summary'],'log':end['log']}
            report['records'].append(entry)
            if end['summary']['result']!='PASS': raise RuntimeError('C++ pipeline failed: '+str(end['summary']))
            print(json.dumps(entry),flush=True)
        # Isolate sequence behavior without RGA, MPP, RTSP or a decoder.
        run=folder/'capture-only';run.mkdir()
        c=report['config'];trace=run/'timestamps.csv'
        command=[str(ctl.ROOT/'runtime/bin/capture_probe'),'--device',c['device'],
                 '--format','nv12','--width',str(c['width']),'--height',str(c['height']),
                 '--fps',str(c['fps']),'--buffers',str(c['buffers']),'--queue-capacity','1',
                 '--duration','8','--trace-csv',str(trace)]
        p=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20)
        (run/'probe.log').write_text(p.stdout+p.stderr)
        if p.returncode: raise RuntimeError('Capture-only probe failed: '+p.stderr)
        summary=next(ctl.read_fields(line) for line in p.stdout.splitlines() if line.startswith('SUMMARY '))
        rows=list(csv.DictReader(trace.open()));gaps=[]
        for a,b in zip(rows,rows[1:]):
            delta=(int(b['sequence'])-int(a['sequence']))&0xffffffff
            if 1<delta<0x80000000:
                gaps.append({'missing_sequences':delta-1,
                    'dequeue_offset_s':(int(b['dequeue_us'])-int(rows[0]['dequeue_us']))/1e6,
                    'driver_delta_ms':(int(b['driver_us'])-int(a['driver_us']))/1000,
                    'dequeue_delta_ms':(int(b['dequeue_us'])-int(a['dequeue_us']))/1000})
        report['capture_only']={'summary':summary,'sequence_gap_events':gaps,'trace_csv':str(trace)}
        nv12m=ctl.load_config(config);nv12m['format']='nv12m'
        nv12m_path=folder/'nv12m.json';nv12m_path.write_text(json.dumps(nv12m))
        run=folder/'cpu-nv12m';run.mkdir()
        state=start('cpu',nv12m_path,8)
        decoder=decode(state['local_url'],4,run)
        end=finish_when_done(8)
        formats=[ctl.read_fields(line) for line in Path(end['log']).read_text().splitlines()
                 if line.startswith('CAPTURE_INFO ')]
        assert end['summary']['result']=='PASS' and formats[-1]['planes']==2
        report['records'].append({'test':'real-nv12m-cpu','decode':decoder,
                                  'capture_info':formats[-1],'summary':end['summary'],'log':end['log']})
        try:
            start('dma',nv12m_path,2)
            raise AssertionError('DMA incorrectly accepted NV12M.')
        except ValueError as e:
            report['records'].append({'test':'dma-nv12m-rejected','error':str(e),'pass':True})
        run=folder/'pressure';run.mkdir()
        state=start('dma',config,8,80)
        decoder=decode(state['local_url'],4,run)
        end=finish_when_done(8)
        entry={'test':'encoder-pressure-80ms','mode':'dma','decode':decoder,'summary':end['summary'],'log':end['log']}
        report['records'].append(entry)
        assert end['summary']['lease_balance'] and end['summary']['frame_drop_oldest']>0
        print(json.dumps(entry),flush=True)
        # Start once and reconnect two independent clients; the stream keeps running.
        state=start('dma',config,0)
        reconnect=[]
        for index in range(2):
            run=folder/('reconnect-'+str(index));run.mkdir()
            reconnect.append(decode(state['local_url'],3,run))
        current=ctl.read_json(ctl.STATE)
        ctl.terminate(current['server'])  # owned test server only, no system service
        deadline=time.monotonic()+15
        while ctl.status()['running'] and time.monotonic()<deadline: time.sleep(.1)
        end=ctl.status()
        entry={'test':'reader-reconnect-and-server-loss','reconnect':reconnect,
               'exited_within_bound':not end['running'],'summary':end.get('summary'),'log':end['log']}
        report['records'].append(entry)
        assert not end['running'] and end['summary']['result']=='FAIL' and end['summary']['lease_balance']
        ctl.stop(False)
        print(json.dumps(entry),flush=True)
        for index in range(3):
            state=start('dma',config,0 if index==2 else 2)
            if index==2:
                time.sleep(2)
                end=ctl.stop(False)  # explicit SIGTERM, not a natural duration exit
            else: end=finish_when_done(2)
            assert end['summary']['result']=='PASS' and end['summary']['lease_balance']
            report['records'].append({'test':'repeat-start-stop','index':index,'summary':end['summary']})
        report['passed']=True
    except BaseException as e:
        report.update(passed=False,error=repr(e))
        raise
    finally:
        try:
            report['final_control']=ctl.stop(True)
            report['restored_edgevision']=ctl.edge_command('status')
        except Exception as e: report['restore_error']=repr(e);report['passed']=False
        report['finished_at']=time.time()
        (folder/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({'report':str(folder/'report.json'),'passed':report.get('passed',False)}),flush=True)
    if not report.get('passed'): raise SystemExit(1)

if __name__=='__main__': main()
