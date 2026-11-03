"""Board-local packaging: never include runtime sessions, images or credentials."""
from pathlib import Path
import datetime
import hashlib
import json
import shutil
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parent.parent
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    runtime=ROOT/'runtime'
    source=json.loads((ROOT/'source-manifest.json').read_text())
    old=json.loads((runtime/'build-info.json').read_text())
    for name,digest in source['source_sha256'].items():
        if sha(ROOT/name)!=digest: raise ValueError('Source inventory mismatch: '+name)
    for name,digest in old['source_sha256'].items():
        # Only docs/Python/PS can change after the last native build. Exact
        # C++ header changes must trigger rebuild even when they are comments.
        if name=='CMakeLists.txt' or name.endswith(('.cpp','.h')):
            if source['source_sha256'].get(name)!=digest:
                raise ValueError('Native source changed; rebuild before release: '+name)
    sdk=Path('/userdata/av_dev/ffmpeg_pipeline/build')
    if (sdk/'mpp/LICENSES').exists():
        target=runtime/'licenses/MPP';target.mkdir(exist_ok=True)
        for p in (sdk/'mpp/LICENSES').rglob('*'):
            if p.is_file():
                dest=target/p.relative_to(sdk/'mpp/LICENSES');dest.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(p,dest)
    if (sdk/'rga/COPYING').exists(): shutil.copyfile(sdk/'rga/COPYING',runtime/'licenses/RGA-COPYING')
    for p in (ROOT/'ema3588/licenses').glob('*'):
        if p.is_file(): shutil.copyfile(p,runtime/'licenses'/p.name)
    old.update(source)
    old['packaged_at']=datetime.datetime.now().astimezone().isoformat()
    old['source_inventory_verified']=True
    old['libc']=subprocess.check_output(['getconf','GNU_LIBC_VERSION'],text=True).strip()
    (runtime/'build-info.json').write_text(json.dumps(old,indent=2)+'\n')
    rows=[sha(p)+'  '+p.relative_to(runtime).as_posix() for p in sorted(runtime.rglob('*'))
          if p.is_file() and p.name!='SHA256SUMS']
    (runtime/'SHA256SUMS').write_text('\n'.join(rows)+'\n')
    subprocess.run(['sha256sum','--quiet','-c','SHA256SUMS'],cwd=str(runtime),check=True)
    release=ROOT/'releases'/('ema3588-low-latency-stream-arm64-'+datetime.datetime.now().strftime('%Y%m%d')+'.tar.gz')
    release.parent.mkdir(exist_ok=True)
    def include(info):
        if '__pycache__' in Path(info.name).parts or info.name.endswith(('.pyc','.log')): return None
        return info
    with tarfile.open(release,'w:gz') as t:
        t.add(runtime,arcname='runtime',filter=include)
        t.add(ROOT/'source-manifest.json',arcname='source-manifest.json')
        for name in source['source_sha256']:
            t.add(ROOT/name,arcname=name,filter=include)
        # Permit replacement/relinking of the LGPL FFmpeg static libraries.
        # Application source, compile settings and its non-FFmpeg objects are
        # included; matching third-party source revisions are locked separately.
        build=ROOT/'build-ema3588'
        for target in ('rtsp_stream','dmabuf_rtsp_stream'):
            for file in (build/'CMakeFiles'/(target+'.dir')).rglob('*'):
                if file.is_file() and (file.suffix=='.o' or file.name=='link.txt'):
                    t.add(file,arcname='relink/'+file.relative_to(build).as_posix())
        for p in build.glob('librkstream_*.a'): t.add(p,arcname='relink/'+p.name)
    receipt={'file':release.name,'bytes':release.stat().st_size,'sha256':sha(release),
             'private_images_or_sessions_included':False,
             'native_cpp_source_inventory_matches_last_build':True}
    release.with_suffix(release.suffix+'.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))

if __name__=='__main__': main()
