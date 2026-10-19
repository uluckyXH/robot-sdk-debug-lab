# T1 Studio · Robot SDK Lab

一个独立的 T1 机器人接入脚手架：Python SDK、FastAPI 服务和 React / TypeScript 工作台。支持双目视频、SDK 单帧与视频截图、机器人实时收音、设备遥测、姿态／预设动作／短时移动／语音／表情控制。无需启动 RoboWeave、大模型或语音识别服务。

页面控制默认开启（`read_only: false`），由用户主动操作；启动不会自动执行动作。已接入固定版本厂家 SDK 的 **18 个服务、10 个话题和 3 条媒体通道**，包含形态舵机、HAL 单关节／全身关节、MC 自定义上肢、灯光、音频文件播放、屏幕视频和关机。完整清单及厂家限制见 [接口覆盖文档](docs/interface-coverage.md)。

此前已验证视频、截图、收音、主要遥测及一次 TTS 受理。本轮完整接口扩展按用户要求**不做机器人功能测试**，只核对源码、编译消息包与页面；不会因为接口未做真机测试而锁定页面。

## 在当前机器上启动

独立环境、T1 消息包、本地连接配置和网页均已准备好：

```bash
cd /home/yoga/robot-sdk-debug-lab
./start.sh
```

打开 **http://127.0.0.1:8090**；交互式接口文档位于 `/docs`。启动脚本会在桌面环境自动打开浏览器；重复运行会复用已启动服务。加 `--no-browser` 可只启动服务。`Ctrl+C` 退出本次启动的服务。

`start.sh` 可从任意工作目录运行；使用本项目 Python 环境、T1 SDK 和本地配置，页面构建缺失时会自动执行前端构建。新机器仍需先完成下方一次性环境准备。原来的 `python3 scripts/run.py` 入口继续可用。

主页面可切换左右目、暂停预览、截图、下载图片、开启机器人声音监听。点击左侧“设备”查看完整遥测，“相册”查看已保存截图，“调试”查看 SDK 接口和回执。

TTS 入口：工作台下方 **机器人控制 → 语音与表情 → 机器人播报**，输入文本后点击“发送播报”。这里通过 SDK 调用机器人扬声器；右侧“开启声音监听”用于在浏览器听机器人麦克风，两者独立。

**舵机与关节**：左侧“关节”打开独立页面。形态舵机可填角度、速度；单关节可填位置、速度、力矩、Kp/Kd、发布频率和时长。HAL 需要厂家低层开发模式与关节反馈；MC 上肢需要 `BIPED_CUSTOM_UPPER`。页面提供前置条件说明、模式切换和发布结束按钮。

**全部 SDK 接口**：左侧“调试”按分类搜索接口，编辑完整请求 JSON，查阅字段类型／枚举，调用后查看厂家原始回执；支持话题实时监听和有限时长发布。上传电脑音频到机器人选择“上传音频到扬声器”。

**修改机器人 IP**：左侧“连接”或右上角“T1 Robot”打开连接设置，填写当前机器人 IPv4 地址，点击“保存并重新连接”。通过 `start.sh` 启动时，本地后端会自动重启并重新建立 SDK、视频和音频连接，页面自动恢复。配置保存在本机，控制开关、端口和拾音通道不变。先结束正在运行的动作或发布任务再切换。

电脑连接到机器人热点或同一局域网后，也可直接在终端切换：

```bash
# 地址仅为示例，请替换为机器人实际 IP
./start.sh --robot-ip 192.168.4.1

# 仅在机器人使用不同 ROS domain 时一并指定
./start.sh --robot-ip 192.168.4.1 --ros-domain-id 0
```

启动器会更新配置并复用现有进程管理器，无需另开第二个后端。直接编辑 `config/local.json` 也会触发重连。使用 `scripts/run.py` 或自行嵌入 API 时不监视配置，保存后需手动重启。更新程序后应先退出旧启动器，再执行新的 `start.sh`。

浏览器播放需要点击“开启声音监听”。监听音量滑块只调整本机播放，不改变机器人音量。离开页面、切到后台或断开连接后会结束拾音。截图仅保存在本机 `data/snapshots/`，自动保留最近 60 张；麦克风声音不落盘。

服务默认仅监听本机。远程工作时可用 `ssh -L 8090:127.0.0.1:8090 <开发机>` 转发后通过本机 localhost 打开；这样也满足浏览器 AudioWorklet 的安全上下文要求。

## 从零准备

目标环境是 Ubuntu 22.04 / ROS 2 Humble / CPython 3.10，Node.js 22.12+ 或 24。原生 SDK 与本机 ROS ABI 必须一致；本项目不把 Q1 和 T1 的同名消息包混载。

```bash
python3.10 -m venv .venv
.venv/bin/pip install -e '.[dev]'
npm --prefix web ci
npm --prefix web run build
```

系统需已有 `ffmpeg`、`pactl`、`parec`，后两者来自 `pulseaudio-utils`。原生接口编译需 ROS Humble 的 `ros-base`、`rosidl-default-generators`、`std-msgs`、`geometry-msgs`、`sensor-msgs`、`nav-msgs`，以及 `python3-colcon-common-extensions`、`python3-dev`、`python3-numpy`、`python3-empy`、CMake 和 C++ 编译器。启动不会安装系统包或修改机器人。

有 RoboWeave 安装时，一次性复制 T1 环境：

```bash
python3 scripts/prepare_local.py --from-roboweave /path/to/RoboWeave
```

此入口复制 T1 消息包、已配置的音源和连接参数，生成新的 DDS 配置，控制默认开启。如果源消息包缺少完整接口，会从源项目固定厂家源码构建补全后再复制；不会连接机器人。编译链接会被解引用，复制后运行无需原目录。已有本地配置时不会覆盖。

也可从厂家 [T1 SDK](https://atomgit.com/primebot/t1-sdk) 的固定版本独立构建：

```bash
git clone https://atomgit.com/primebot/t1-sdk.git /path/to/t1-sdk
git -C /path/to/t1-sdk checkout fe2e186c867391ec78b301accdc91f0260ad0ff3
python3 scripts/build_sdk.py --source /path/to/t1-sdk
python3 scripts/prepare_local.py \
  --sdk-prefix runtime/sdk-build/install/aimdk_msgs \
  --robot-ip 192.0.2.1 --domain 0
```

把示例地址替换为实际机器人地址。编译器只处理消息包，校验接口 SHA-256；不会启动厂家示例或调用机器人。厂家源码和编译产物留在 `runtime/`，不纳入版本库。

## 配置

配置示例为 [config/example.json](config/example.json)，实际配置在忽略提交的 `config/local.json`。

| 字段 | 含义 |
| --- | --- |
| `robot_ip` / `ros_domain_id` | 机器人地址与真实 ROS domain；通过连接设置或启动参数保存后同步应用到所有绑定的连接 |
| `sdk_prefix` / `dds_profile` | 独立 T1 消息包目录、FastDDS XML 模板；启动时将 `{robot_ip}` 替换为当前地址，生成运行配置 |
| `read_only` | 默认 `false`，页面可操作；用户显式设为 `true` 才进入只读模式 |
| `enabled_commands` | 可选命令白名单：`speak`、`expression`、`set_volume`、`set_mute`；只读模式仍优先拦截 |
| `enabled_interfaces` | 默认 `null` 开放完整接口；可填写接口名列表限制高级写入口，例如 `["SetServo", "PlayTts"]` |
| `playback_port` | 默认 `6001`，上传文件转换为 48 kHz / mono / s16le，经 TCP 发送 |
| `rtsp_port` | 默认 `2554`，左右目使用 `live_head_stereo_left/right` |
| `rtsp_urls` | 可选 `{ "left": "rtsp://…", "right": "rtsp://…" }`；支持 `{robot_ip}` 地址模板，只在服务端使用 |
| `video_fps` / `video_width` | 默认输出 15 FPS、宽度 1280；按原比例缩放 |
| `audio` | 实际机器人 PulseAudio/PipeWire TCP 音源、原始声道数与零基拾音声道 |
| `motion.runtime` | 可选 `version: 2` 参数，默认移动 800 ms、线速度 0.15 m/s、角速度 0.3 rad/s |
| `motion.enabled` | 设为 `false` 禁止动作调用，且不创建运动服务客户端和速度发布器；可与仅启用 `speak` 组合 |

没有已配置的音源时，可先只读查询 `pactl --server=tcp:<机器人IP>:4713 --format=json list sources`。配置结构示例：

```json
{
  "transport": "tcp",
  "server": "tcp:{robot_ip}:4713",
  "auth": "anonymous",
  "device": "实际 source 名称",
  "channel": 4,
  "channels": 8,
  "source_rate": 48000,
  "source_format": "s16le",
  "verification_note": "填写实际音源与声道的核对结果"
}
```

示例声道参数不代表所有设备相同。`anonymous` 必须与机器人已配置的 TCP 服务一致；项目不会通过 SSH 自动更改机器人音频配置。连接后会核对 source 格式，排除扬声器 monitor 回环。

已有配置中与机器人旧 IP 相同的音频、视频地址，在首次使用新启动器或页面保存时转为 `{robot_ip}` 模板，以后跟随 `robot_ip`。显式指向其他服务器的地址保留，连接设置页会列出这些独立地址。自定义 DDS XML 可在机器人 peer 的 `<address>` 中使用 `{robot_ip}`；旧版由本项目生成的单机器人 DDS 配置也会自动使用新地址。实际 XML 位于忽略提交的 `runtime/connections/`。

## 开发与复用 SDK

```bash
# 后端 + 已构建页面
python3 scripts/run.py

# 单独前端热更新，API 代理到 8090
npm --prefix web run dev
```

SDK 可以脱离网页使用；先加载 ROS Humble 和 T1 overlay（`scripts/run.py` 展示了环境构造）。每个 `T1Client` 实例在同一个事件循环中启动、使用、关闭。另开 SDK 程序与服务同时运行时请使用独立 `data_dir`，避免争用同一份命令日志：

```python
import asyncio
from dataclasses import replace
from pathlib import Path
from t1_lab.sdk import T1Client, load_settings

async def main():
    settings = replace(load_settings(), data_dir="data/sdk-example")
    async with T1Client(settings) as robot:
        print(robot.state())
        image, metadata = await robot.frame("left")
        Path("frame.jpg").write_bytes(image)

asyncio.run(main())
```

控制默认可用。若用户显式配置 `read_only: true`，写请求才会返回 `403`。通用 `call_interface()` 接入全部厂家 ROS 服务；`publish_interface()` 接入控制话题；`topic()` / `messages()` 读取话题；`set_servo()` / `control_joints()` / `play_audio()` 提供常用便捷入口。导航、地图规划和设备开发模式设置不属于此固定版本公开 ROS SDK；详见覆盖清单。

```python
# 以下示例会控制机器人，只有主动调用时才执行；项目启动不会运行示例。
await robot.set_servo(position=45, speed=100)
await robot.call_interface("SetNeckLight", {"enable": True, "brightness": 100})
await robot.call_interface("PlayTts", {
    "tts_req": {"text": "你好", "domain": "my_app", "priority_level": {"value": 6}}
})

# 按配置的原生话题读取；慢消费者仅保留最近 4 个样本。
async for sample in robot.messages("JointStateArray"):
    print(sample)
```

关节与原始速度发布使用 `publish_interface(..., rate_hz=..., duration_ms=...)`，或 `control_joints(joints, channel="hal")`。返回 `running` 后，用当前 `command_id` 每 500 ms 调用 `stream_heartbeat()`；用 `stop_stream()` 提前结束。HAL 发布结束会发送完整阻尼帧。Python／桌面系统不提供硬实时保证，回执包含实际包数和最大间隔。

每个写入请求需携带 `/api/state` 返回的当前 `command_epoch` 与唯一后缀，例如 `<epoch>:<uuid>`。同 ID、同参数去重；不同参数冲突；重启后的旧 epoch 被拒绝，不自动重发。运动期间浏览器每 500 ms 续租，断线／租约到期后运行时结束本次速度输出。停止接口只处理本实例的动作，不代表全机硬件急停。`accepted` 只表示受理，不能当成物理执行完成。

## API

| 接口 | 功能 |
| --- | --- |
| `GET /api/health` | 服务与只读模式 |
| `GET /api/connection` | 已保存与当前生效的 IP / domain、配置 revision、是否支持自动重连 |
| `POST /api/connection` | 保存 `robot_ip`、`ros_domain_id` 和 GET 返回的 `revision`；只修改本地连接配置 |
| `GET /api/state` | 连接、遥测、音频与视频状态 |
| `GET /api/capabilities` | 原生接口发现和动作目录 |
| `GET /api/diagnostics` | 原生 SDK 路径、实际服务客户端、是否创建速度发布器 |
| `GET /api/video/stream?camera=left` | 共享 RTSP → MJPEG 预览 |
| `POST /api/snapshots` | `{"camera":"left","source":"sdk"}` 或 `source: "video"` |
| `GET /api/snapshots` | 截图清单与下载 URL |
| `WS /api/audio` | 首帧格式 JSON，后续二进制 PCM；客户端每 2.5 秒发送 `ping` |
| `POST /api/commands` | TTS、音量、静音、表情 |
| `POST /api/motion` | 具名姿态／移动／预设动作 |
| `POST /api/motion/heartbeat`、`/api/motion/stop` | 续租、本实例停止 |
| `GET /api/commands/{id}`、`GET /api/events` | 持久回执与最近调用记录 |
| `GET /api/sdk/catalog` | 完整接口目录、原始字段 schema、枚举、关节限位 |
| `POST /api/sdk/call` | `{"interface":"SetServo","payload":{"position":45,"speed":100},"command_id":"<epoch>:<uuid>"}` |
| `GET /api/sdk/topics/{name}` | 订阅并读取最新原始样本 |
| `WS /api/sdk/topics/{name}/stream` | 实时话题消息；二进制字段以 base64 对象表示 |
| `POST /api/sdk/publish` | `interface`、`payload`、`command_id`、`rate_hz`、`duration_ms` |
| `GET /api/sdk/stream` | 当前发布流状态 |
| `POST /api/sdk/stream/heartbeat`、`/api/sdk/stream/stop` | 发布流续租／结束，参数 `command_id` |
| `POST /api/sdk/audio/play?format=wav&command_id=...` | 请求体为原始文件字节；支持 WAV/MP3/OGG/FLAC/PCM，最大 16 MiB、120 秒 |
| `POST /api/sdk/audio/stop` | 结束本次 TCP 音频发送 |

未知和过期遥测以 `null`／`availability` 表示，不换成零。电池值展示 SDK 原始值及单位，`scale_verified` 保留实际状态。SDK 连接、RTSP 预览、音频采集分别报告状态。

## 检查

本轮仅执行了本地静态检查和构建，未运行下面的功能测试入口。纯本地接口核对可用 `python3 scripts/check_catalog.py`，不初始化 ROS、不发送机器人请求。

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src scripts tests
npm --prefix web run build
npm --prefix web run test:e2e

# 服务启动后：只读真机验收，不调用任何控制接口
.venv/bin/python scripts/check_readonly.py
cd web && node scripts/check-live.mjs
```

浏览器测试需已安装 Chromium；首次可执行 `cd web && npx playwright install chromium`。常规测试只使用假设备；真机验收脚本单独显式执行并要求服务为只读模式。

另提供 `python3 scripts/run.py --check-speech`：**会让机器人播报一次**固定测试句，调用 `T1Client.command("speak", …)`，不自动重发。工作台配置须保持只读；该入口仅在独立进程启用 `speak`，禁用运动、表情、音量与静音写入。它采集播报前后共 16 秒的麦克风电平，仅保存统计和 SDK 回执到 `logs/live-speech.json`，不保存音频。TTS 的 `accepted` 表示入队，不是播放完成事件。

设计与计划见 [docs/design.md](docs/design.md)，交付验证见 [docs/acceptance.md](docs/acceptance.md)，提取来源见 [docs/source-manifest.json](docs/source-manifest.json) 和 [NOTICE.md](NOTICE.md)。
