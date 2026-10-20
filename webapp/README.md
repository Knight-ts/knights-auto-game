# MaaGC 网页版（webapp）

用 Python 直接驱动 MaaFramework 的网页控制台，**不再需要 MFAAvalonia**。

## 启动

双击根目录的 `启动网页版.bat`，或：

```bat
python\python.exe webapp\server.py
```

默认打开 <http://127.0.0.1:8790/>（被占用时顺延到 8791–8799）。
启动后会自动连接上次使用的模拟器并拉起 Agent；也可以先在页面上点「重新搜索 / 连接设备」。

## 页面

| 页面 | 内容 |
|---|---|
| 主页 | 设备选择与连接、资源/Agent/任务状态、最近日志 |
| 任务 | 读取 interface.json 的任务与选项（switch/checkbox/select/input），组合 pipeline_override 后执行 |
| 推年计划 | 任务组合：选模块、逐步改选项、排序、循环次数、简介、自动处理规则，命名保存（table/task_combos.json）并运行 |
| 截图 | 设备实时画面（2 秒刷新、可下载） |
| 日志 | 调度日志 + Agent stdout |
| 好娃提醒 | 组合列表（点「详情」展开）+ 按种族分类的完整特性表，保存时同步条件表 / 检测节点 / 界面选项 |
| 孩子记录 | 读取 table/child_records.xlsx 对应的记录，可标记已招募 |

## 结构

```
webapp/
  runtime.py         连接、资源、Tasker、Agent、任务执行
  server.py          HTTP 服务与 JSON API（127.0.0.1）
  static/index.html  页面骨架
  static/app.js      前端逻辑
  static/style.css   样式
```

好娃提醒的界面不在这四个文件里：`agent/action/zshg/web_alerts.js`、`web_alerts.css` 由本服务和网页控制台共用（`/alerts.js`、`/alerts.css`），数据来自 `child_alert_store.py` + `child_feature_table.py`。

## 与旧版的关系

- `resource/base`、`agent/`、`table/`、`interface.json` 全部复用；
- 旧的任务「网页控制台」（MFAAvalonia 里）可以不用了；
- Agent 由 `runtime.py` 拉起（`python -u agent/main.py <identifier>`），通过 `AgentClient` 连接；
- 启动 Agent 时会设置 `MAAGC_DISABLE_CONSOLE=1`，避免它再抢 8790 端口。

## API 摘要

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/state` | 设备/资源/Agent/任务状态、日志、设备列表 |
| GET | `/api/tasks` | interface.json 的任务与选项 |
| GET | `/api/screenshot` | PNG 截图 |
| GET | `/api/logs?limit=` | 日志 |
| POST | `/api/connect` | `{adb_path, address}` |
| POST | `/api/agent/start` / `/api/agent/stop` | Agent 控制 |
| POST | `/api/task/run` | `{entry, name, selection}` |
| POST | `/api/task/stop` | 停止当前任务 |
| GET/POST | `/api/alerts`、`/api/alerts/save` | 好娃提醒配置（含 `featureGroups` 特性表分类） |
| GET/POST | `/api/records`、`/api/records/recruited` | 孩子记录与招募标记 |
| GET/POST | `/api/notify`、`/api/notify/save`、`/api/notify/test` | 平台推送配置与测试发送 |
| POST | `/api/logs/clear` | 清空内存日志 |
| GET | `/api/taskpack` | 任务包状态（tasks/ 与 interface.json 是否一致） |
| POST | `/api/taskpack/export` / `build` / `check` | 导出任务包 / 重建 interface.json / 检查 |
| POST | `/api/taskpack/pack` | `{entry}` 打包任务为 `dist/tasks/<entry>.zip` |
| POST | `/api/taskpack/unpack` | `{path, force?, build?}` 导入任务包 |
| GET | `/api/combos` | 组合列表 + 可用任务模块 + 已保存的选项 + 运行状态 |
| GET | `/api/combos/status` | 只查运行状态（页面轮询用） |
| POST | `/api/combos/save` | `{combos}` 保存全部组合（table/task_combos.json） |
| POST | `/api/combos/run` | `{combo}` 或 `{id}` 运行组合 |
| POST | `/api/combos/stop` | 停止组合 |

## 页面补充

- 任务页会记住上次选的任务与各选项（`config/webapp.json`），并显示最近任务记录；
- 任务页底部有「任务包」卡片：导出 tasks/、重建 interface.json、打包当前任务、导入 zip，改完不用命令行也能用；
- 「推年计划」页是任务组合编辑器：每个步骤单独存选项（相亲爵位、刷取任务国家等），组合可写简介、配自动处理规则（失败重试/跳过/停止、好娃提醒、离线重连、每轮等待），存在 `table/task_combos.json`；
- 通知页可启用推送、选择飞书/通用 JSON、发送测试消息；webhook 留空时自动复用 `config/config.json` 的飞书地址。
