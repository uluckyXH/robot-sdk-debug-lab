# T1 SDK 完整接口覆盖清单

范围基准：厂家 `primebot/t1-sdk` 固定版本 `fe2e186c867391ec78b301accdc91f0260ad0ff3` 的全部 `.srv` 文件及《接口说明.md》中的接口元数据。对照后共 **18 个服务、10 个话题、3 条媒体通道**，本项目均已有代码入口与页面操作入口。55 个消息／服务文件已编译并做 SHA-256 静态核对。

**接入完成不代表真机验收完成。** 用户要求本轮不做机器人测试，因此本轮未发出服务试调用、关节／速度发布或音频试播，也未运行功能测试脚本。只进行了源码覆盖核对、Python 静态检查、TypeScript 检查、前端构建及原生消息包构建。界面保持可操作，未通过“未测试”标记锁定按钮。

## 接口清单

所有 ROS 服务均可通过 `T1Client.call_interface(name, payload)` 和 `POST /api/sdk/call` 调用；页面「SDK 调用台」提供原始字段模板、类型、枚举和回执。话题支持 `topic()`、`messages()` 订阅及 `publish_interface()` 有限时长发布。

| 分类 | 功能 | 接口／协议 | 接入入口 |
| --- | --- | --- | --- |
| 运动 | 运动模式查询 | `GetMcAction` · `/aimdk_5Fmsgs/srv/GetMcAction` | SDK 调用台 · 服务参数 |
| 运动 | 运动模式设置 | `SetMcAction` · `/aimdk_5Fmsgs/srv/SetMcAction` | SDK 调用台 · 服务参数 |
| 运动 | 全身动作与模型 | `SetMcMotion` · `/aimdk_5Fmsgs/srv/SetMcMotion` | SDK 调用台 · 服务参数 |
| 运动 | 预设动作 | `SetMcPresetMotion` · `/aimdk_5Fmsgs/srv/SetMcPresetMotion` | SDK 调用台 · 服务参数 |
| 运动 | 控制输入源设置 | `SetMcInputSource` · `/aimdk_5Fmsgs/srv/SetMcInputSource` | SDK 调用台 · 服务参数 |
| 运动 | 控制输入源查询 | `GetCurrentInputSource` · `/aimdk_5Fmsgs/srv/GetCurrentInputSource` | SDK 调用台 · 服务参数 |
| 关节 | 形态舵机 | `SetServo` · `/aimdk_5Fmsgs/srv/SetServo` | SDK 调用台 · 服务参数；舵机与关节专用页面 |
| 音频 | 语音播报 | `PlayTts` · `/aimdk_5Fmsgs/srv/PlayTts` | SDK 调用台 · 服务参数 |
| 音频 | 查询音量 | `GetVolume` · `/aimdk_5Fmsgs/srv/GetVolume` | SDK 调用台 · 服务参数 |
| 音频 | 设置音量 | `SetVolume` · `/aimdk_5Fmsgs/srv/SetVolume` | SDK 调用台 · 服务参数 |
| 音频 | 查询静音 | `GetMute` · `/aimdk_5Fmsgs/srv/GetMute` | SDK 调用台 · 服务参数 |
| 音频 | 设置静音 | `SetMute` · `/aimdk_5Fmsgs/srv/SetMute` | SDK 调用台 · 服务参数 |
| 音频 | 机器人本地音频文件 | `PlayAudioFile` · `/aimdk_5Fmsgs/srv/PlayAudioFile` | SDK 调用台 · 服务参数 |
| 屏幕 | 表情与屏幕视频 | `PlayEmotion` · `/aimdk_5Fmsgs/srv/PlayEmotion` | SDK 调用台 · 服务参数 |
| 系统 | 机器人关机 | `ShutdownTask` · `/aimdk_5Fmsgs/srv/ShutdownTask` | SDK 调用台 · 服务参数 |
| 视觉 | 相机单帧 | `CaptureJpegImage` · `/aima/hal/camera/CaptureJpegImage` | SDK 调用台 · 服务参数 |
| 灯光 | 灯带样式与颜色 | `LedStripCommand` · `/aimdk_5Fmsgs/srv/LedStripCommand` | SDK 调用台 · 服务参数 |
| 灯光 | 颈部灯光 | `SetNeckLight` · `/aimdk_5Fmsgs/srv/SetNeckLight` | SDK 调用台 · 服务参数 |
| 关节 | 全身底层关节 | `JointCommandArray` · `/aima/hal/joint/command` | SDK 调用台 · 话题发布；舵机与关节专用页面 |
| 关节 | 自定义上肢关节 | `McCustomJointCommand` · `/aima/mc/custom/joint/command` | SDK 调用台 · 话题发布；舵机与关节专用页面 |
| 关节 | 关节状态反馈 | `JointStateArray` · `/aima/hal/joint/state` | SDK 调用台 · 话题订阅 |
| 运动 | 原始速度指令 | `McLocomotionVelocity` · `/aima/mc/locomotion/velocity` | SDK 调用台 · 话题发布 |
| 音频 | ROS 音频采集 | `AudioCapture` · `/aima/hal/audio/capture` | SDK 调用台 · 话题订阅 |
| 音频 | ROS 音频回放 | `AudioPlayback` · `/aima/hal/audio/playback` | SDK 调用台 · 话题发布 |
| 音频 | 音频设备列表 | `AudioDeviceInfoList` · `/aima/hal/audio/audio_device_list` | SDK 调用台 · 话题订阅 |
| 传感器 | 触摸状态 | `TouchState` · `/aima/hal/touch/state` | SDK 调用台 · 话题订阅 |
| 传感器 | 电池状态 | `Bms` · `/aima/hal/bms/state` | SDK 调用台 · 话题订阅 |
| 灯光 | 颈部灯光状态 | `NeckLightState` · `/aima/hal/neck_light/state` | SDK 调用台 · 话题订阅 |
| 视觉 | 双目视频 | `RTSP` · `rtsp://<robot>:2554/live_<camera_id>` | 工作台 · 视频／音频 |
| 音频 | TCP 麦克风监听 | `PulseCapture` · `tcp://<robot>:4713` | 工作台 · 视频／音频 |
| 音频 | 上传音频到扬声器 | `TcpAudioPlayback` · `tcp://<robot>:6001` | SDK 调用台 · 上传音频到扬声器 |

## 舵机与单关节

- `SetServo` 是形态舵机：位置 0–90°，速度 100–1000 步/秒（每步 0.225°）。页面有独立位置、速度输入与发送按钮。
- HAL `JointCommandArray` 控制 14 个关节的位置、速度、力矩、刚度和阻尼。支持单个关节输入；其余关节从新鲜反馈补全并保持当前目标（Kp 20；HAL 的 Kd 2，上肢 Kd 3，沿用厂家示例默认值）。也可在完整调用台显式填写全部关节参数。
- MC `McCustomJointCommand` 控制 6 个上肢关节，使用独立消息和话题，不与 HAL 混用。页面提供显式切换到 `BIPED_CUSTOM_UPPER` 的按钮。
- HAL 按厂家要求使用 500–1000 Hz，MC 上肢使用 100 Hz；由后端独立线程发布，页面仅负责续租。一次流最长 10 秒，500 ms 续租，租约 1.5 秒。位置先从当前反馈插值，单帧增量不超过 0.2 rad。
- HAL 正常结束、用户结束、租约到期与服务退出时，已开始的流发送完整阻尼帧（Kp 0 / Kd 5）。回执记录包数、最大间隔和阻尼帧是否发送。不把“本地发布结束”当成“机器人已物理停止”。
- 关节状态未知时不补零。反馈不完整、类型／来源冲突或目标超过厂家限位时返回原因，按钮不会因为缺少真机测试而被全局禁用。

## 厂家范围与实际前置条件

1. **低层开发模式**：HAL 需要机器人进入厂家低层运控开发模式并关闭高层 MC；厂家入口是机器人终端的 `yamo mode edit`，不属于这 18 个 ROS 服务。本项目不自动通过 SSH 修改设备模式。MC 上肢要求原生 MC 运行并进入 `BIPED_CUSTOM_UPPER`。
2. **实时性**：Python 线程、桌面操作系统与网络不保证硬实时 500–1000 Hz。时间戳来自 ROS 节点时钟，需满足机器人时钟／网络部署要求。本轮没有测量真机频率、抖动或动作结果。
3. **两份缺失消息**：厂家文档描述 `AudioCapture` 与 `AudioPlayback`，但固定源码没附 `.msg`。本项目从同版本文档的协议块提取到 `vendor_additions/aimdk_msgs/msg/` 并编译；类型和来源摘要已记录。固件有无这两个话题仍需由用户现场运行确定。
4. **颈灯话题差异**：厂家索引使用 `/aima/hal/neck_light/state`，详情写 `/aimdk_5Fmsgs/msg/NeckLightState`。订阅同时兼容这两个路径，并返回实际收到消息的路径。亮度字段仅为预留，目前厂家只实现开关。
5. **TCP 扬声器**：上传文件转换为 48 kHz / mono / s16le，发送到已配置的机器人 6001 端口。该端口须已启用；本项目不自动 SSH 加载 PulseAudio 模块。网络发送完成不证明播放完成。
6. **模型、视频与音频路径**：SetMcMotion / PlayEmotion / PlayAudioFile 使用机器人上已有资源，SDK 不提供资源上传管理服务；电脑音频可使用新增 TCP 上传入口。
7. **SDK 未公开的功能**：此版本未提供导航／地图／任务规划服务，厂家指南说明原生 REST/gRPC 开发方式尚未支持。它们不在本次接口清单中；本项目的 HTTP API 是本地 ROS 桥接 API。也未包含 Q1 适配。

## 本地静态证据

`logs/full-sdk-static.json`：全部服务名称与厂家源码一致，厂家文档的所有 ROS 接口路径都有覆盖（含颈灯别名），55 个安装接口摘要匹配；该检查未导入 ROS、未初始化 DDS、设备请求数为 0。

`logs/sdk-complete-build.txt`、`logs/full-sdk-frontend-build.txt`：原生消息包和 React 生产构建记录。常规历史测试结果不作为本轮扩展的真机验证。
