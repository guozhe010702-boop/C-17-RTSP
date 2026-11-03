"""Windows/Linux: deterministic source inventory; no recordings or credentials."""
from pathlib import Path
import hashlib,json,subprocess,tarfile

ROOT=Path(__file__).resolve().parent.parent
def main():
    cache=ROOT/'.cache'; cache.mkdir(exist_ok=True)
    files=[p for folder in ('include','src','apps','tests','ema3588','scripts')
           for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    files += [ROOT/p for p in ('CMakeLists.txt','LICENSE','THIRD_PARTY.md','TODO_EMA3588.md','README.md','.gitignore')]
    files += [p for p in (ROOT/'docs').iterdir() if p.is_file() and p.suffix in ('.md','.json')]
    if (ROOT/'README_EMA3588.md').exists(): files.append(ROOT/'README_EMA3588.md')
    manifest={'upstream':'https://github.com/xxgqiu/rk3588-low-latency-stream',
              'upstream_commit':'15138da285e62f6de2f8a78ce7dc6a3c1cf96e9c',
              'source_sha256':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    (cache/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    archive=cache/'ema3588-source.tar.gz'
    with tarfile.open(archive,'w:gz') as t:
        for p in files: t.add(p,arcname=p.relative_to(ROOT).as_posix())
        t.add(cache/'source-manifest.json',arcname='source-manifest.json')
    print(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,
                      'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}))
if __name__=='__main__': main()
