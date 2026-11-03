# EVM3588 / IMX415：C++17 低延迟硬件 RTSP

源码位于 `av_dev/Low latency stream/`，板端部署在 `/userdata/low-latency-stream/`。
本项目源码仓库：[guozhe010702-boop/C-17-RTSP](https://github.com/guozhe010702-boop/C-17-RTSP)。
基于 [xxgqiu/rk3588-low-latency-stream](https://github.com/xxgqiu/rk3588-low-latency-stream)，固定提交 `15138da285e62f6de2f8a78ce7dc6a3c1cf96e9c`，保留 MIT 许可与来源。原文档中的 OV8858 性能不是本板成绩；本板结果见 [实测报告](docs/EMA3588_VALIDATION.md)。

## 结构与边界

```text
IMX415/con7 → RKISP /dev/video44 → V4L2 MMAP + EXPBUF
 → DmabufFrameLease → 有界最新帧队列
 → RGA 同步布局拷贝 → 立即 QBUF → MPP H.264
 → 有界压缩包队列 → libavformat RTSP/TCP → MediaMTX
 → HDMI ffplay / Windows ffplay
```

默认请求1920×1080、NV12、60fps、6个相机缓冲；H.264 CBR 8Mbit/s、GOP60；帧队列1、包队列8。修改入口为 `ema3588/config.json`。本节点不支持 G_PARM/S_PARM，所以请求60fps不表示驱动已经确认60fps；程序分开报告请求值、驱动报告和实际计数。未知报告帧率输出0，不拿请求值冒充。

当前 NV12 使用 MPLANE API，但只有一个连续内存平面。源stride1920、高1080，UV起点2073600；MPP对齐高度1088，UV起点2088960。RGA 在两个不同的 DMA-BUF 之间做一次硬件布局拷贝，**不是相机直接共享 MPP 缓冲，也不是纯零拷贝**。压缩包仍复制到自有 vector 和 AVPacket。

CPU基线保留两次原始图像拷贝，使用相同硬编参数。它支持 NV12/NV12M 的stride、bytesused和data_offset；DMA路径目前仅接受已验证的连续单内存平面NV12，对NV12M或非零偏移明确拒绝，不静默切换路径。

采集、RGA、MPP和RTSP封装均由C++17实现；Python只管理进程和实验。本项目使用 **VPU，不使用NPU**，输出是原始实拍视频，不运行YOLO。FFmpeg库只封装已编码的H.264，不代替MPP编码器。

## 启停命令：开发板 Linux 终端

以下命令在 `root@ema:~#` 这样的**板端终端**执行，也可以是电脑通过SSH/ADB打开的板端终端。不要直接粘贴到Windows PowerShell。

启动推荐路径：

```bash
python3 /userdata/low-latency-stream/ema3588/control.py start --mode dma --take-camera
```

`start` 启动独立MediaMTX和C++程序；`--mode dma` 选择DMA-BUF+RGA；`--take-camera` 允许通过已知EdgeVision控制接口交接摄像头，保存原模型和HDMI状态。占用者不是已确认的EdgeVision时拒绝终止。默认持续运行，SSH断开后仍运行，不注册开机服务。

查看状态与实际FPS：

```bash
python3 /userdata/low-latency-stream/ema3588/control.py status
```

`status` 返回进程、模式、配置、日志、计数差分FPS、指标年龄、过期标记及结束汇总。`running` 仅表示进程存活，还要看 `rates`、`metrics_stale` 和 `metric_age_seconds`；停止后最后一组FPS标为过期。

板端HDMI全屏播放，无需板端键盘：

```bash
python3 /userdata/low-latency-stream/ema3588/preview_hdmi.py --fullscreen
```

`--fullscreen` 启动或切换本项目ffplay窗口为全屏，只读RTSP，不再次打开摄像头。脚本以ema桌面用户运行，并定位到HDMI，避免落到DSI屏幕区域。此固件的ffplay默认选择h264_rkmpp会触发RGA转换错误，因此本客户端显式指定原生h264软件解码与2个解码线程；C++编码仍为MPP硬编，不宣称此客户端已经实现C++硬解显示。

退出全屏：

```bash
python3 /userdata/low-latency-stream/ema3588/preview_hdmi.py --windowed
```

`--windowed` 恢复1280×720窗口，原始编码分辨率不变。

仅关闭HDMI播放器：

```bash
python3 /userdata/low-latency-stream/ema3588/preview_hdmi.py --stop
```

`--stop` 关闭本项目播放器，C++推流继续。

停止新链路并恢复原EdgeVision：

```bash
python3 /userdata/low-latency-stream/ema3588/control.py stop --restore-previous
```

`stop` 顺序关闭本项目播放器、编码程序和RTSP服务；`--restore-previous` 恢复交接前模型及HDMI全屏/窗口状态。不带该参数只停止新链路，原应用保留暂停状态，之后仍可执行带恢复参数的停止命令。不会终止无关摄像头占用者。

选择CPU拷贝基线时，先停止新链路，再执行：

```bash
python3 /userdata/low-latency-stream/ema3588/control.py start --mode cpu
```

`--mode cpu` 只改变原始帧搬运方式，仍使用MPP硬编。运行中更换模式或配置会被拒绝，应先停止、修改JSON、再启动。

## 电脑观看：Windows PowerShell

在项目 `Low latency stream` 目录打开**Windows PowerShell**：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\ema3588\receive_windows.ps1 -Address 192.168.2.221
```

`powershell.exe` 启动解释器；`-NoProfile` 不加载个人配置；`-ExecutionPolicy Bypass` 仅对此解释器进程允许脚本；`-File` 指接收脚本；`-Address` 指板子IP。脚本调用已安装的ffplay，通过TCP播放，窗口1280×720并启用低缓存/追帧。端口默认8556，可用 `-Port` 修改。

在同一命令末尾加 `-Fullscreen` 可电脑全屏；按电脑键盘 `q` 只关闭电脑播放器。完整地址是 `rtsp://192.168.2.221:8556/imx415`，普通浏览器不能直接播放RTSP。本项目没有另做网页。

## 构建与部署

本板构建读取现有 `/userdata/av_dev/ffmpeg_pipeline/runtime/` 私有SDK，使用匹配的头文件、pkg-config和静态FFmpeg库。MPP/RGA动态库与MediaMTX复制到本项目runtime，不替换系统库，不安装系统包，不执行ldconfig，不改内核。g++9.4 / C++17，最多4个并行任务。

在**板端终端**重新编译：

```bash
bash /userdata/low-latency-stream/ema3588/build_board.sh
```

脚本配置CMake、编译、运行离线测试并安装到本项目runtime。缺硬件依赖时明确失败，不将仅生成capture_probe视作完整构建；拒绝覆盖本项目正在运行的程序。

从电脑首次部署，在**Windows PowerShell / 项目根目录**执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\ema3588\deploy_ema3588.ps1 -Serial 07c7cd5e6c097097
```

`-Serial` 明确指定ADB设备。脚本核对真实设备树、检查目标runtime未运行、打包源码并校验SHA-256，再原生编译。ADB路径不同可加 `-AdbPath`。ADB显示Nexus4来自厂商属性，不能代替实际设备树判断。

另一套SDK可在板端构建命令前设置 `SDK_PREFIX=/你的SDK目录`，但仍需匹配include/lib/pkgconfig/bin。运行中的C++程序不依赖旧项目的可执行程序；验收解码工具和旧应用交接接口属于本板的实验环境。

## 程序导航

| 文件 | 功能 |
| --- | --- |
| `src/v4l2_capture.cpp`、对应头文件 | CPU采集、格式读回、MMAP、DQBUF/QBUF、归还与清理 |
| `include/rkstream/nv12_payload.h` | NV12/NV12M有效区、独立stride、非零offset校验与归一化 |
| `src/v4l2_dmabuf_capture.cpp`、对应头文件 | EXPBUF、slot状态、互斥归还；头文件定义move-only `DmabufFrameLease` |
| `include/rkstream/bounded_latest_queue.h` | 固定容量、drop-oldest、close唤醒、drain；lease析构归还丢弃/拒绝的slot |
| `src/rga_nv12_aligner.cpp` | 初始化时导入DMA-BUF，逐帧同步布局拷贝，完成后即可QBUF |
| `src/mpp_h264_encoder.cpp` | H.264、CBR/GOP/level、对齐输入、put_frame/get_packet、取包超时和IDR头 |
| `src/rtsp_publisher.cpp` | RTSP/TCP封装，微秒PTS转90kHz，网络I/O中断期限 |
| `apps/rtsp_stream.cpp` | CPU基线三线程流水线 |
| `apps/dmabuf_rtsp_stream.cpp` | DMA推荐流水线，RGA后立即归还源缓冲，再MPP编码 |
| `apps/capture_probe.cpp` | 纯采集基线；可输出序号/时间戳CSV，不保存图像 |
| `tests/` | 队列、NAL/IDR重同步、布局、lease移动/拒绝/丢弃/异常展开契约 |
| `ema3588/control.py`、`config.json` | 参数、PID校验、已知应用交接、启停、状态、恢复 |
| `ema3588/validate_board.py` | ABBA对照、解码、NV12M、纯采集、过载、重连、故障和恢复 |
| `ema3588/preview_hdmi.py`、`x11_preview.py` | 板端预览与无键盘全屏 |
| `ema3588/receive_windows.ps1` | Windows ffplay入口 |
| `ema3588/package_source.py`、`deploy_ema3588.ps1`、`build_board.sh` | 源码清单、校验、传输与构建 |
| `ema3588/package_release.py` | 核对最终源码与已编译版本，生成含源代码、ARM64程序、私有依赖、许可和重链接对象的本地交付包 |
| `docs/ARCHITECTURE.md`、`PERFORMANCE.md` | 上游架构与OV8858实验参考 |
| `docs/EMA3588_VALIDATION.md` | 本板实测和限制 |

主线程＋采集/编码/网络三个工作线程，共4个应用线程；MPP内部线程和独立MediaMTX另计。编码前丢旧帧保证新鲜度；编码包丢弃后等待下一IDR避免损坏参考链。正常退出先停止生产，唤醒并清理下游，归还lease，再释放硬件资源。

上游双码流、文件编码和Qt客户端源码保留用于学习；本轮验收是单路实时RTSP，不宣称全部上游扩展已经在EVM3588验证。

## 对照实验与口径

在**板端终端**运行：

```bash
python3 /userdata/low-latency-stream/ema3588/validate_board.py --seconds 20
```

`--seconds 20` 为每轮CPU/DMA时长，顺序CPU→DMA→DMA→CPU，分辨率、码率、GOP、队列和解码客户端相同。随后做纯采集CSV、真实NV12M、80ms背压、客户端重连、服务中断、反复启停及SIGTERM。测试暂时交接EdgeVision，finally恢复原状态，不应与其它控制命令并行操作。

结果在 `local/acceptance-日期时间/`：报告JSON、源日志、解码进度和纯采集CSV。厂商ISP/3A配置保持不变，读出曝光/增益作记录，但不声称锁定曝光。驱动缺口和主动队列丢弃分别保留，不能将它们互相替代。

CPU以一个逻辑核为100%；RSS是C++进程用户态峰值，不含完整DMA物理内存、MediaMTX或播放器。内部耗时是DQBUF到RTSP write返回，不包含曝光、ISP、网络、解码和显示；**不能称为镜头到屏幕延迟**。`PASS` 表示本次运行与资源路径完成，不代表无驱动缺口或长期稳定性通过。

## 运行产物与许可

`runtime/bin/` 是ARM64程序和MediaMTX；`runtime/lib/` 是私有MPP/RGA；`runtime/build-info.json`、`SHA256SUMS` 记录版本；`local/` 放会话、日志和私有素材；`.cache/` 放源码包；`releases/` 放本地交付包。缓存、私拍和运行日志不入源码。

上游代码保留MIT。当前FFmpeg静态库为LGPL3-or-later，MPP/RGA/MediaMTX各自许可分别保留，不把所有依赖统称为MIT。构建来源、SDK清单及重建材料随交付保存。GitHub仅同步源码、配置、文档与脱敏指标；私拍素材、会话、日志、缓存和ARM64交付包保留本地。
