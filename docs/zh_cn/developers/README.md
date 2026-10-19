# MaaGC 开发者文档

> 本目录是 MaaGC（基于 MaaFramework 的「诸神皇冠」自动化助手）的开发者参考。聚焦本仓库**当前真实存在**的节点、自定义动作与测试流程。

## 命名约定

- 所有节点坐标、ROI、图片均基于 **1280×720（720p）**。
- Pipeline JSON 使用 `.prettierrc` 规定的 4 空格缩进，数组元素换行。
- 节点命名 PascalCase，业务前缀（如 `BattleXxx`、`MarryXxx`）；内部节点以 `__` 开头。

## 文档列表

| 文档 | 用途 |
|------|------|
| [common-buttons.md](./common-buttons.md) | `main_ui.json` 中 8 个核心可复用节点（返回、点击中心、弹窗等） |
| [custom.md](./custom.md) | 本仓库已实现的 8 个 Custom Action 清单 + `LaunchShopping` 待补全 |
| [node-testing.md](./node-testing.md) | 用 maa-mcp 逐个测试 Pipeline 节点的标准流程 |

## 上游引用

本目录 4 个文档被 [`.claude/skills/pipeline-guide/SKILL.md`](../../../.claude/skills/pipeline-guide/SKILL.md) 第 284-287 行的"参考"小节引用。

协议细节见 [`docs/maafw_doc/zh_cn/3.1-任务流水线协议.md`](../../maafw_doc/zh_cn/3.1-任务流水线协议.md)。
