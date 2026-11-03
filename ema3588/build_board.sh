#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SDK_PREFIX="${SDK_PREFIX:-/userdata/av_dev/ffmpeg_pipeline/runtime}"
BUILD_DIR="$ROOT/build-ema3588"
INSTALL="$ROOT/runtime"
test "$(uname -m)" = aarch64
grep -q RK3588 /proc/device-tree/model
test -r "$SDK_PREFIX/lib/pkgconfig/libavformat.pc"
# All libraries are private. Never install packages, change ldconfig or overwrite
# a vendor library. Reject rebuild of the live target before cmake --install.
python3 - "$INSTALL/bin" <<'PY'
import os,sys
from pathlib import Path
folder=Path(sys.argv[1]).resolve()
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try: executable=Path(os.readlink(str(p/'exe')).split(' (deleted)',1)[0]).resolve()
    except OSError: continue
    if executable.parent==folder:
        raise SystemExit('Stop this runtime before rebuilding: '+str(executable))
PY
export PKG_CONFIG_PATH="$SDK_PREFIX/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
cmake -S "$ROOT" -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=ON -DRKSTREAM_REQUIRE_HARDWARE=ON -DCMAKE_INSTALL_PREFIX="$INSTALL" \
  -DMPP_INCLUDE_DIR="$SDK_PREFIX/include/rockchip" \
  -DMPP_LIBRARY="$SDK_PREFIX/lib/librockchip_mpp.so" \
  -DRGA_INCLUDE_DIR="$SDK_PREFIX/include/rga" -DRGA_LIBRARY="$SDK_PREFIX/lib/librga.so"
cmake --build "$BUILD_DIR" --parallel 4
( cd "$BUILD_DIR" && ctest --output-on-failure )
cmake --install "$BUILD_DIR"
mkdir -p "$INSTALL/lib" "$INSTALL/licenses"
cp -a "$SDK_PREFIX"/lib/librockchip_mpp.so* "$SDK_PREFIX"/lib/librga.so* "$INSTALL/lib/"
cp "$SDK_PREFIX/bin/mediamtx" "$INSTALL/bin/mediamtx"
cp "$ROOT/LICENSE" "$INSTALL/licenses/upstream-MIT.txt"
if [[ -d "$ROOT/ema3588/licenses" ]]; then cp -a "$ROOT/ema3588/licenses/." "$INSTALL/licenses/"; fi
if [[ -d /userdata/av_dev/ffmpeg_pipeline/build/mpp/LICENSES ]]; then
  mkdir -p "$INSTALL/licenses/MPP"
  cp -a /userdata/av_dev/ffmpeg_pipeline/build/mpp/LICENSES/. "$INSTALL/licenses/MPP/"
fi
if [[ -f /userdata/av_dev/ffmpeg_pipeline/build/rga/COPYING ]]; then
  cp /userdata/av_dev/ffmpeg_pipeline/build/rga/COPYING "$INSTALL/licenses/RGA-COPYING"
fi
for license in COPYING.LGPLv3 COPYING.LGPLv2.1 COPYING.GPLv3; do
  file="/userdata/av_dev/ffmpeg_pipeline/build/ffmpeg/$license"
  if [[ -f "$file" ]]; then cp "$file" "$INSTALL/licenses/FFmpeg-$license"; fi
done
python3 - "$ROOT" "$SDK_PREFIX" <<'PY'
from pathlib import Path
import hashlib,json,platform,subprocess,sys
root=Path(sys.argv[1]); sdk=Path(sys.argv[2]); runtime=root/'runtime'
manifest=json.loads((root/'source-manifest.json').read_text())
manifest.update(system=subprocess.check_output(['uname','-srmo'],text=True).strip(),
    libc=subprocess.check_output(['getconf','GNU_LIBC_VERSION'],text=True).strip(),
    compiler=subprocess.check_output(['g++','--version'],text=True).splitlines()[0],
    sdk_prefix=str(sdk),pkg_versions=subprocess.check_output(['pkg-config','--modversion','libavformat','libavcodec','libavutil','rockchip_mpp','librga'],text=True).splitlines(),
    mediamtx=subprocess.check_output([str(runtime/'bin/mediamtx'),'--version'],text=True).strip(),
    flags=['C++17','Release','BUILD_TESTING=ON','RKSTREAM_REQUIRE_HARDWARE=ON','parallel=4'],
    ffmpeg_linkage='static, LGPL 3 or later; source and relinkable build retained',
    sdk_build_manifest=json.loads((sdk/'build-manifest.json').read_text()))
(runtime/'build-info.json').write_text(json.dumps(manifest,indent=2)+'\n')
rows=[]
for p in sorted(runtime.rglob('*')):
    if p.is_file() and p.name!='SHA256SUMS':
        rows.append(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(runtime).as_posix())
(runtime/'SHA256SUMS').write_text('\n'.join(rows)+'\n')
PY
( cd "$INSTALL" && sha256sum --quiet -c SHA256SUMS && ldd bin/dmabuf_rtsp_stream )
echo __EMA3588_BUILD_OK__
