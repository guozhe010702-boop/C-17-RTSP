param(
    [string]$Address = '192.168.2.221',
    [int]$Port = 8556,
    [switch]$Fullscreen
)
$ErrorActionPreference = 'Stop'
$player = Get-Command ffplay -ErrorAction Stop
$url = 'rtsp://{0}:{1}/imx415' -f $Address,$Port
$arguments = @('-hide_banner','-loglevel','warning','-rtsp_transport','tcp',
    '-fflags','nobuffer','-flags','low_delay','-framedrop','-sync','ext',
    '-window_title','EVM3588 IMX415 Low Latency RTSP','-x','1280','-y','720')
if ($Fullscreen) { $arguments += '-fs' }
& $player.Source @arguments $url
exit $LASTEXITCODE
