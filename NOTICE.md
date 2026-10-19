# 来源记录

`src/t1_lab/_vendor/` 按实际依赖提取自用户指定的 RoboWeave 项目，基线提交 `7364da0d07f2200e7a0ac8880ed310cb10f75957`。原始文件的 SHA-256 见 `docs/source-manifest.json`。当前项目只注册 T1，未复制 Q1 包、模型服务、聊天、原应用 HTTP 入口或模拟机器人资源。

提取后的变更包括内部 import 命名空间、T1-only registry/schema、ROS 节点名、原生只读守卫，以及按命令白名单和运动开关创建客户端／发布器。运动适配的协议字段、枚举、服务来源检查、租约、回执和持久去重沿用原实现。部分内部控制源前缀保留 `roboweave_` 以保持协议校验一致；这不依赖原应用。

厂家消息包对应 [primebot/t1-sdk](https://atomgit.com/primebot/t1-sdk) commit `fe2e186c867391ec78b301accdc91f0260ad0ff3`。消息定义、构建产物和厂家使用声明应遵循其原始授权；本项目不为上游代码新增或代替许可证。

`src/t1_lab/sdk/interface_catalog.json` 的字段、枚举与 14 关节限位由同版本厂家源码和《接口说明.md》生成，生成入口为 `scripts/sync_interfaces.py`。`vendor_additions/aimdk_msgs/msg/AudioCapture.msg` 与 `AudioPlayback.msg` 来自该文档的协议块，用于补全源码缺少的两份类型定义。55 个接口文件的摘要保存在 `_vendor/sdk-schema.json`，补充定义来源及摘要同时写入目录 JSON；不宣称这些文档补充接口已在当前固件实测。

界面本地提供 Noto Sans SC 字体，通过 `@fontsource-variable/noto-sans-sc` 依赖构建。字体来自 [Noto CJK](https://github.com/notofonts/noto-cjk)，SIL Open Font License 1.1，完整许可随 npm 包提供，并在网页构建产物中保留一份。页面运行不请求远程字体服务。
