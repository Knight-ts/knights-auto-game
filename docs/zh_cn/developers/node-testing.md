# 节点测试（Node Testing）

> 用 maa-mcp 逐个测试 Pipeline JSON 中的节点，验证识别和操作是否正常工作。本文档是 [`.claude/skills/pipeline-testing/SKILL.md`](../../../.claude/skills/pipeline-testing/SKILL.md) 的纸质版归档（skill 提供 `/test-pipeline` 命令）。

## 调用方式

```
/test-pipeline
```

或在 Claude Code 会话中直接按下方 6 步手动执行（适用于 CI 与脚本化场景）。

## 6 步测试流程

### Step 1：连接设备

```python
find_adb_device_list()        # ADB 模拟器/真机
# 或
find_window_list()            # Win32 窗口
```

选目标后连接：

```python
connect_adb_device(device_name="设备名")
# 或
connect_window(window_name="窗口名")
```

保存返回的 `controller_id`，后续所有调用都要用到。

### Step 2：读取 Pipeline 文件

```python
load_pipeline(pipeline_path="<pipeline_json_path>")
```

从返回内容中提取：

- 所有节点名称
- 每个节点的 `recognition` / `action` / `next` 结构
- 节点之间的流程关系

### Step 3：确定测试参数

```python
PIPELINE_PATH = "<pipeline_json_path>"      # 如 assets/resource/base/pipeline/marry.json
RESOURCE_PATH = "<resource_base_path>"      # 如 assets/resource/base
CONTROLLER_ID = "<controller_id>"           # Step 1 返回值
```

### Step 4：逐个测试节点

```python
run_pipeline(
    controller_id=CONTROLLER_ID,
    pipeline_path=PIPELINE_PATH,
    entry="NodeName",            # 直接用节点名，不加 "Entry" 前缀
    resource_path=RESOURCE_PATH
)
```

### Step 5：分析返回结果

```python
{
    "status": "succeeded" | "failed" | "running",
    "node_count": 1,
    "nodes": [{
        "name": "node名称",
        "recognition": {
            "all_results": [
                {"box": [x, y, width, height], "score": 0.999}
            ]
        }
    }]
}
```

判断标准：

- `status == "succeeded"` 且 `all_results` 有内容 → **识别成功**
- `status == "failed"` 或 `all_results` 为空 → **识别失败**
- `score > 0.9` 通常视为可靠匹配

### Step 6：记录测试结果

```markdown
## <文件名> 测试记录

日期: 2026-06-04
设备: xxx
controller_id: xxx

### ✅ 通过的 Node

| Node | 功能 | 状态 |
|------|------|------|
| node_name | 描述 | ✅ succeeded |

### ❌ 失败的 Node

| Node | 功能 | 原因 |
|------|------|------|
| node_name | 描述 | 原因xxx |
```

## API 来源

所有 API 来自 **MaaFramework Python 绑定 `maa` 库** + **`maa-mcp` MCP 服务**（非项目内）。具体调用对象在文档中以函数名形式出现，对应 maa-mcp 工具。

## 资源保护 ⚠️

以下场景会消耗游戏内资源，测试时**绝对不要点确定**：

- 升级建筑、神殿升级
- 供奉、祭拜先祖
- 购买物品、商城购买
- 确认战斗开始
- 任何有资源消耗的确认按钮

如果误进入这些界面：

1. 尝试按 **ESC** 或点击"取消/返回"
2. 调用 `BackButton_500ms` 强制返回
3. 切换到其他 tab 再切回来刷新状态

## 回退策略优先级

| 优先级 | 节点 | 识别方式 | 用途 |
|--------|------|----------|------|
| 1 | `BackButton_500ms` | TemplateMatch `return.png` | **最可靠**，默认首选 |
| 2 | `BackButton_DirectHit` | DirectHit 固定坐标 | `BackButton_500ms` 不匹配时的兜底 |
| 3 | `ClickCenter_500ms` | DirectHit 点击屏幕中心 | 关闭任意弹窗/遮罩的最后兜底 |

详见 [common-buttons.md](./common-buttons.md#1-backbutton_500ms返回)。

## 界面状态管理

- 每个节点都假设界面处于某种起始状态。
- 如果节点 A 的 `next` 是 B，测试 B 前需要先让界面处于 B 的起始状态。
- 按流程顺序测试，逐步深入。
- 每完成一个子流程，用 `BackButton_500ms` 返回。

## 快速测试策略

1. 先测试通用、底层的节点（如 `BackButton_500ms`、`ClickCenter_500ms`）
2. 再测试业务节点
3. 按流程顺序测试
4. 遇到弹窗先关闭再继续

## 常见问题处理

### OCR 识别失败

- 检查 `roi` 范围是否正确覆盖目标文字区域。
- 检查 `expected` 文字是否与界面文字**完全匹配**（包括空格、标点）。
- 文字可能有微小变形，尝试用正则或部分匹配（加 `// @i18n-skip` 跳过 i18n）。

### 对话框卡住

- "角色已死亡，无法 XXX" 等对话框直接点"确定"关闭。
- 切换到其他 tab 再切回来刷新状态。
- 使用 `BackButton_500ms` 返回主界面。

### 识别到了但点击位置不对

- `box` 坐标格式是 `[x, y, width, height]`。
- 实际点击取**中心点**：`x + width/2, y + height/2`。

## 节点生命周期

```
进入节点 → pre_wait_freezes → pre_delay → action → post_wait_freezes → post_delay → 截图识别 next
```

## 常用配置默认值

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `timeout` | next 循环识别超时时间 (ms) | 20000 |
| `rate_limit` | 识别速率限制 (ms) | 1000 |
| `pre_delay` | 识别到动作前的延迟 (ms) | 200 |
| `post_delay` | 动作后到识别 next 的延迟 (ms) | 200 |
| `pre_wait_freezes` | 等待画面静止（识别到动作前） | 0 |
| `post_wait_freezes` | 等待画面静止（动作后） | 0 |

> 通用节点应显式声明 `rate_limit: 0` / `pre_delay: 0` / `post_delay: 0`，避免协议默认值引入隐式等待。详见 [common-buttons.md](./common-buttons.md#lint-提示待落实)。
