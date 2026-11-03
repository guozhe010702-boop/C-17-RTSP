param([string]$Serial='07c7cd5e6c097097',[string]$AdbPath)
$ErrorActionPreference='Stop'
$projectRoot=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $AdbPath) { $AdbPath=Join-Path $projectRoot '..\..\Tools\platform-tools\adb.exe' }
if (-not (Test-Path -LiteralPath $AdbPath)) { throw 'Pass -AdbPath with the full path to adb.exe.' }
function Invoke-Adb {
    param([string[]]$Arguments)
    $result=& $script:AdbPath -s $script:Serial @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'ADB command failed.' }
    return $result
}
$model=Invoke-Adb @('shell','cat /proc/device-tree/model') | Out-String
if ($model -notmatch 'RK3588') { throw 'Actual device tree is not RK3588.' }
$probe=@'
from pathlib import Path
import os
folder=Path('/userdata/low-latency-stream/runtime/bin')
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try: executable=Path(os.readlink(str(p/'exe')).split(' (deleted)',1)[0])
    except OSError: continue
    if executable.parent==folder: raise SystemExit('Stop this runtime before deploying.')
print('__DEPLOY_READY__')
'@
$probeBytes=[Text.Encoding]::UTF8.GetBytes($probe) -join ','
$ready=Invoke-Adb @('shell',"python3 -c 'exec(bytes([$probeBytes]))'") | Out-String
if ($ready -notmatch '__DEPLOY_READY__') { throw 'Runtime is active or process inspection failed.' }
& python -X utf8 (Join-Path $PSScriptRoot 'package_source.py')
if ($LASTEXITCODE -ne 0) { throw 'Source packaging failed.' }
$archive=Join-Path $projectRoot '.cache\ema3588-source.tar.gz'
$digest=(Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
$remoteRoot='/userdata/low-latency-stream'
Invoke-Adb @('shell',"mkdir -p $remoteRoot") | Out-Null
Invoke-Adb @('push',$archive,"$remoteRoot/source.tar.gz") | Out-Null
$command="cd $remoteRoot && echo '$digest  source.tar.gz' | sha256sum -c - && tar xzf source.tar.gz && bash ema3588/build_board.sh"
$output=Invoke-Adb @('shell',$command) | Out-String
Write-Host $output
if ($output -notmatch '__EMA3588_BUILD_OK__') { throw 'Remote build or tests failed.' }
Write-Host "Ready: $remoteRoot"
