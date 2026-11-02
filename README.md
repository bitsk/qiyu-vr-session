# qiyu-vr-session

实际控制器/头显会话的启动停止、数据转发与清理。


构建入口和依赖见下方；工程 Wiki/Blog 与跨仓库规划集中在 [qiyu-docs](https://github.com/bitsk/qiyu-docs)。

## 打包

`python3 tools/package.py --output /new/package`

src/ 导自已发布的 controller-production-runtime 会话包，integration/ 包含后续 present-offset 入口。需要另行提供 kernel、设备/跟踪/VR 组件和 vendor 运行库。打包命令不启动服务、不写设备；安装与候选选择仍按原有设备窗口和回退流程执行。
