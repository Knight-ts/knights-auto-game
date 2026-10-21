# Knights Auto Game

基于 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 的《诸神皇冠 / 百年骑士团》网页版自动化助手。

> **Knights Auto Game** 是 [MAAGC](https://github.com/KhazixW2/MAAGC)（MIT）的网页版衍生重写（非官方）。
> Agent、Pipeline 与资源等部分代码**借鉴自 MAAGC**，后端改为用 Python 直接驱动 MaaFramework、浏览器作为控制台，
> 不包含 MFAAvalonia 等桌面 UI。来源、上游与第三方素材说明见 [UPSTREAM.md](UPSTREAM.md)、[NOTICE](NOTICE)。

## 功能

- **主页**：模拟器搜索 / 连接、资源与 Agent 状态、最近日志
- **任务**：读取 interface.json 的任务与选项（开关 / 多选 / 下拉 / 文本），组合 pipeline_override 执行与停止，记录最近任务
- **推年计划（任务组合）**：把任务模块按顺序串成组合，设置循环次数，命名保存（如 紫砂盾循环 / 成长试炼循环 / 瓦国任务循环）并一键运行
- **截图**：设备实时画面（2 秒刷新、可下载）
- **日志**：调度日志 + Agent 输出，可清空
- **好娃提醒**：组合列表（一行一个，点「详情」展开填写）+ 按种族分类的完整特性表，保存时同步条件表 / 检测节点 / 界面选项
- **孩子记录**：查看 table/child_records.xlsx 对应记录，可标记已招募
- **佣兵记录**：智能接受佣兵（按名字白/黑名单）、记录佣兵名字与游戏年月
- **通知**：飞书 / 通用 JSON 推送配置与测试发送
- **问题自纠**：报错时自动保存现场截图 + 日志尾巴，用户判断「保留 / 删除」，Harness 读 `debug/errors/REPORT.md` 复盘修改

## 环境

- Windows 10/11（x64）
- Python 3.12
- **MuMu 模拟器 12（目前仅适配 MuMu）**：其他模拟器与真机未适配、未验证
- 依赖见 requirements.txt（maafw 5.12.3、opencv-python、numpy、loguru、jsonschema）

## 快速开始

项目目录内已带 `python\` 运行时（约 380 MB，已在 `.gitignore` 中排除），**双击 `启动网页版.bat` 即可离线运行**。

如果需要自己重建环境（例如换机器/更新依赖）：

1. 安装依赖：双击 安装依赖.bat（会在 .venv 里安装），或手动：

   ```bat
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

2. 启动：双击 启动网页版.bat（优先用 `python\`，其次 `.venv`）
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
tasks/           任务包：每个任务一个文件夹（task.json + 可单独运行的 run.py）
tools/           任务包工具、好娃提醒窗口脚本、项目自带的 adb
docs/            项目与 MaaFramework 文档
interface.json   任务与选项定义（由 tasks/ 重建）
UPSTREAM.md      来源、上游与第三方说明
```

## 任务包（tasks/）

`interface.json` 里的每个任务在 `tasks/<entry>/` 有一个文件夹：

- `task.json`：任务定义（名称、entry、勾选的选项、说明、文档）；
- `run.py`：单独运行这个任务，方便改完立刻测试；
- `index.json` 控制任务顺序，`_options.json` 存放所有选项定义。

```bat
python tools\taskpack.py export --force        :: interface.json -^> tasks\
python tools\taskpack.py build                 :: tasks\ -^> interface.json
python tools\taskpack.py check                 :: 检查任务/选项/pipeline 引用
python tools\taskpack.py pack CastleMarryTest  :: 打成 dist\tasks\CastleMarryTest.zip
python tools\taskpack.py unpack 路径.zip --build
python tasks\CastleMarryTest\run.py --set 联姻方式=联姻
```

也可以双击 `tools\任务包.bat` 用菜单操作，或网页版「任务 → 任务包」点按钮。详细说明见 [tasks/README.md](tasks/README.md)。

## 推年计划（任务组合）

「推年计划」页把任务列表里的模块（启动游戏、每日礼包、月度循环、自动相亲、战斗任务主动……）按顺序串起来，组成一套流程：

- **组合名 + 简介**：例如 紫砂盾循环、成长试炼循环、瓦国任务循环，简介写清这个组合是干什么的；
- **循环次数**：0 = 一直循环到手动停止，1 = 只跑一遍；
- **步骤**：选模块 → 添加，可上移 / 下移 / 删除；点步骤上的「选项」就地展开该模块的参数（例如自动相亲的相亲爵位、月度循环的刷取任务国家 / 成长试炼难度），每个步骤各存各的，互不影响；
- **自动处理规则**：遇到情况做什么——
  - 触发条件：任务失败 / 好娃提醒 / 设备离线 / 每轮结束；
  - 处理动作：重试当前步骤（N 次）/ 跳过当前步骤 / 停止组合 / 继续下一步 / 发送通知 / 重连后重试 / 等待（N 秒）；
  - 没配规则时的默认行为：任务失败停止、好娃提醒停止、设备离线停止；
- 组合保存在 `table/task_combos.json`，可随时保存、改名、删除，重启后仍在；
- 「运行组合」会按顺序执行并显示第几轮 / 第几步，按规则自动处理，可随时停止。

好娃提醒触发时，Agent 会写 `temp/child_alert.json` 标记，组合据此执行「好娃提醒」规则（默认停止并提示手动处理）。原来的整月推年流程保留为任务模块 **月度循环（原推年计划）**，可以直接放进组合里，也可以单独运行。

## 问题自纠（报错截图）

任务失败、任务异常、Agent 抛异常 / 启动失败 / 意外退出、接口 500、页面脚本报错，都会在**报错那一刻**抓一张设备截图，连同任务名、错误文字、最近日志一起存到 `debug/errors/`：

```
debug/errors/
  index.json                 全部记录（状态 / 错误 / 备注 / 截图路径）
  REPORT.md                  自动生成的复盘报告
  snapshots/<id>/            screen.png + meta.json + logs.txt
  trash/<id>/                用户删除的记录（可恢复，按设置自动清理）
```

网页「问题自纠」页：

- 左侧列表看缩略图、来源、时间和错误摘要；右侧看完整截图、错误、日志；
- 每条可以标 **保留**（值得修）/ **待判定** / **删除**（进回收站），也可以写备注说明哪里不对；
- 「立即抓取当前画面」可手动存一张；「设置」里调条数、体积、保留天数、去重窗口；
- 自动规则：默认最多 200 条 / 400 MB，待判定 7 天后进回收站、回收站 3 天后彻底删除；超限时只丢最旧的待判定 / 回收站记录，**永远不动已保留的**；
- 设置存在 `config/webapp.json` 的 `selfheal` 节，可关掉自动截图。

给 DeepSeek Harness 的入口（不需要开网页）：

```bat
python tools\selfheal.py list --status kept --json   :: 只看用户确认有价值的（含 image_path）
python tools\selfheal.py show <id>                   :: 错误 + 日志 + 截图路径
python tools\selfheal.py keep <id> --note "第 3 步识别不到按钮"
python tools\selfheal.py report                      :: 打印 REPORT.md
```

约定：**保留** = 用户确认值得修，优先据此改代码；**待判定** = 还没看；**已删除** = 用户认为没价值，不要据此修复。

## 配置

- interface.json：任务列表与选项（由 `tasks/` 重建，网页版任务页直接读取）
- tasks/：任务的源文件；改完运行 `build` 再重启 Knights Auto Game 生效
- table/child_alert_conditions.json：好娃提醒条件（也可在网页“好娃提醒”页编辑）
- table/notify_config.json：平台推送；默认关闭，webhook 留空时复用 config/config.json 的飞书地址
- config/webapp.json：上次设备 / 任务 / 选项、问题自纠设置（`selfheal` 节，自动生成，不要提交）

## 好娃提醒怎么配组合

网页「好娃提醒」页（或原生窗口）分两块：

1. **完整特性表（按种族分类）**：26 个分类 / 214 项特性。
   - 血统：佩尔弗因王族、高等精灵、黑暗精灵等 15 个血统，带等级、部位、效果、寿命；
   - 平民血：加尔提斯人、古特人、萨尼德人等 7 个种族；
   - 通用：正面 / 中性 / 负面 / 表白怀孕特性。
   左侧点种族，右侧点「＋ 加入」，特性就加到当前组合里（再点一次取消）。搜索框可跨种族按特性名 / 效果筛选。
2. **组合列表**：每行约 2cm，显示组合名、种族标签、特性、属性门槛摘要；点「详情」展开填写界面显示名、内部 ID、条件名、检测节点、说明、**备注**、必需特性、属性门槛和或条件。备注只给自己看，会存进 `table/child_alert_conditions.json`。

「从特性表挑」会把加入目标切换到当前组合并展开特性表；「保存并同步」会写入条件表、检测节点和界面选项，勾选状态按实例保存，需要点「同步勾选到 Knights Auto Game」并重启生效。

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
- 名字只在弹窗里：先点地图横幅把弹窗打开，再取「希望加入佣兵团」与「年龄」两行之间的那一行，所以不会再读到上方的节日提示、“兵团”这类横幅文案；
- 规则在网页「佣兵记录」页或 `table/mercenary_rules.json`（mode=whitelist/blacklist，names=关键字，name_roi=两个锚点都没识别到时的兜底区域）；
- 接受/拒绝只会点对应按钮，不会在“取消”没反应后误点“招募”；
- 每次加入都会写入 `table/mercenary_records.csv`（时间、游戏年、游戏月、名字、结果、规则）；名字没读到时写「(未识别)」，备注保留弹窗 OCR 原文；
- 日志会打印 `佣兵名字取自弹窗第 NN 行：xxx（边界 a~b）`，名字读错时用这一行校准；
- 当前年份/月份由大地图 `C.E.xxxx` 与月份 OCR 更新到 `table/game_state.json`，孩子记录也会带游戏年月。

## 二次开发

- webapp/runtime.py：ADB 控制器、资源、Tasker、Agent 启动与任务执行
- webapp/server.py：本机 HTTP API（只监听 127.0.0.1）
- webapp/static/：页面（原生 HTML/CSS/JS，无前端构建）
- Agent 自定义动作在 agent/action/ 下，Pipeline 在 resource/base/pipeline/

## 已知限制

- **目前只适配 MuMu 模拟器 12**：依赖其 EmulatorExtras 扩展做高速截图与输入；雷电 / 夜神 / 蓝叠 / 逍遥 / 真机未测试，连接后大概率退回通用方式（截图约 250 ms），部分任务会失败
- 仅支持 Windows（依赖 MaaFramework 的 Windows 运行时与 adb）
- 同一时间只跑一个任务；没有多实例 / 定时任务
- 好娃提醒触发时会弹窗并停止任务，需要手动处理后再启动

## 许可证

MIT © 2026 Knights。部分实现源自 MAAGC（MIT，© MaaXYZ / KhazixW2）；运行时依赖 MaaFramework（**LGPL-3.0**，© MaaXYZ）与 Android platform-tools（Apache-2.0）。详见 [UPSTREAM.md](UPSTREAM.md)、[NOTICE](NOTICE) 与 [LICENSE](LICENSE)。
