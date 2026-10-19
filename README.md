# Knights-web-windows

基于 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 的《诸神皇冠 / 百年骑士团》网页版自动化助手。

> 本项目是 **MAAGC 的网页版衍生重写**（非官方）：后端用 Python 直接驱动 MaaFramework，浏览器作为控制台，不包含 MFAAvalonia 等桌面 UI。来源、上游与第三方素材说明见 [UPSTREAM.md](UPSTREAM.md)。

## 功能

- **主页**：模拟器搜索 / 连接、资源与 Agent 状态、最近日志
- **任务**：读取 interface.json 的任务与选项（开关 / 多选 / 下拉 / 文本），组合 pipeline_override 执行与停止，记录最近任务
- **截图**：设备实时画面（2 秒刷新、可下载）
- **日志**：调度日志 + Agent 输出，可清空
- **好娃提醒**：增删改提醒类型，保存时同步条件表 / 检测节点 / 界面选项
- **孩子记录**：查看 table/child_records.xlsx 对应记录，可标记已招募
- **佣兵记录**：智能接受佣兵（按名字白/黑名单）、记录佣兵名字与游戏年月
- **通知**：飞书 / 通用 JSON 推送配置与测试发送

## 环境

- Windows 10/11（x64）
- Python 3.12
- MuMu 模拟器 12（或任何可 adb connect 的设备）
- 依赖见 requirements.txt（maafw 5.12.3、opencv-python、numpy、loguru、jsonschema）

## 快速开始

1. 安装依赖：双击 安装依赖.bat（会在 .venv 里安装），或手动：

   ```bat
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

2. 启动：双击 启动网页版.bat
3. 浏览器打开 <http://127.0.0.1:8790/>（端口占用会顺延）

启动后会自动连接上次使用的模拟器并拉起 Agent；也可以在主页手动「重新搜索 → 连接设备 → 启动 Agent」。

## adb 说明

- tools/adb/adb.exe 随仓库分发（Android platform-tools，Apache-2.0），脚本、诊断与旧控制台直接调用它；
- MaaFramework 的 ADB 控制器仍使用模拟器自带的 adb（如 MuMu 的 nx_main/adb.exe）：MuMu extras 只认官方安装路径，换成项目内 adb 会让截图从约 13 ms 退化到约 250 ms。

## 目录结构

```
webapp/          网页版后端与前端（runtime / server / static）
agent/           MaaFramework Agent：自定义识别与动作（推年、相亲、战斗、好娃提醒等）
resource/base/   Pipeline、图片模板、OCR/ONNX 模型
table/           好娃提醒条件、姓名库、特性表、通知配置等
tools/           好娃提醒窗口脚本、项目自带的 adb
docs/            项目与 MaaFramework 文档
interface.json   任务与选项定义
UPSTREAM.md      来源、上游与第三方说明
```

## 配置

- interface.json：任务列表与选项；网页版任务页直接读取
- table/child_alert_conditions.json：好娃提醒条件（也可在网页“好娃提醒”页编辑）
- table/notify_config.json：平台推送；默认关闭，webhook 留空时复用 config/config.json 的飞书地址
- config/webapp.json：上次设备 / 任务 / 选项（自动生成，不要提交）

## 孩子命名速度（可调）

网页「好娃提醒 → 孩子命名速度」或 `table/child_name_config.json`：

| 档位 | 特性滑动上限 | 步骤等待 | 适用 |
|---|---|---|---|
| fast | 2 | 200 ms | 最快，可能漏掉需要滑动的特性 |
| normal | 5 | 500 ms | 默认 |
| slow | 8 | 800 ms | 模拟器卡顿/识别不稳时 |

日志会打印每步耗时：`角色信息识别耗时 X.Xs`、`孩子命名流程耗时 X.Xs`。

## 佣兵记录与智能接受

- 任务选项「开启自动接受佣兵」选 **智能** 后，佣兵加入时按名字规则决定接受/拒绝；
- 规则在网页「佣兵记录」页或 `table/mercenary_rules.json`（mode=whitelist/blacklist，names=关键字，name_roi=名字 OCR 区域）；
- 每次加入都会写入 `table/mercenary_records.csv`（时间、游戏年、游戏月、名字、结果、规则）；
- 当前年份/月份由大地图 `C.E.xxxx` 与月份 OCR 更新到 `table/game_state.json`，孩子记录也会带游戏年月。

## 二次开发

- webapp/runtime.py：ADB 控制器、资源、Tasker、Agent 启动与任务执行
- webapp/server.py：本机 HTTP API（只监听 127.0.0.1）
- webapp/static/：页面（原生 HTML/CSS/JS，无前端构建）
- Agent 自定义动作在 agent/action/ 下，Pipeline 在 resource/base/pipeline/

## 已知限制

- 仅支持 Windows（依赖 MaaFramework 的 Windows 运行时与 adb）
- 同一时间只跑一个任务；没有多实例 / 定时任务
- 好娃提醒触发时会弹窗并停止任务，需要手动处理后再启动

## 许可证

MIT © 2026 Knights。部分实现参考了 MaaFramework（MIT，© MaaXYZ）与 MAAGC（MIT，KhazixW2），详见 [UPSTREAM.md](UPSTREAM.md) 与 [LICENSE](LICENSE)。
