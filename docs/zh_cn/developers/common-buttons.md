# 通用节点（Common Buttons）

> 本文档列出 [`assets/resource/base/pipeline/main_ui.json`](../../../assets/resource/base/pipeline/main_ui.json) 中**真正可复用、与具体业务无关**的节点。每个节点 1 段说明 + 1 个最简 JSON 示例片段。

## 核心 8 个通用节点

### 1. `BackButton_500ms`（返回）

TemplateMatch 识别左上角"返回"图标 `return.png`，点击后等待 500ms。

```jsonc
"BackButton_500ms": {
    "recognition": "TemplateMatch",
    "template": "return.png",
    "roi": [603, 0, 117, 140],
    "post_delay": 500,
    "timeout": 2000,
    "action": "Click"
}
```

**用途**：测试中退出任何子页面的最可靠方法，**优先使用**。

### 2. `BackButton_DirectHit`（返回 · 兜底）

DirectHit 固定坐标点击 `(655, 11, 52, 43)`，用于 `BackButton_500ms` 模板不匹配时的兜底。

```jsonc
"BackButton_DirectHit": {
    "recognition": "DirectHit",
    "action": "Click",
    "target": [655, 11, 52, 43],
    "post_delay": 500,
    "timeout": 2000
}
```

### 3. `ClickCenter_500ms`（点击屏幕中心）

DirectHit 固定点击屏幕中心 `(360, 640, 10, 10)`，用于关闭任何弹窗/遮罩。

```jsonc
"ClickCenter_500ms": {
    "recognition": "DirectHit",
    "action": "Click",
    "target": [360, 640, 10, 10],
    "post_delay": 500,
    "timeout": 2000
}
```

**用途**：未匹配到具体按钮时，盲点中心作为最后兜底。

### 4. `PopUpWindowTip`（识别"提示"弹窗）

OCR 识别弹窗标题"提示"在 `(107, 451, 490, 92)` 区域，命中后转入 `PopUpWindowsTipConfirm`。

```jsonc
"PopUpWindowTip": {
    "recognition": "OCR",
    "roi": [107, 451, 490, 92],
    "expected": ["提示"],
    "next": ["PopUpWindowsTipConfirm"],
    "timeout": 2000
}
```

### 5. `PopUpWindowsTipConfirm`（提示弹窗的"确定"）

OCR 识别弹窗底部"确定"按钮在 `(175, 646, 436, 148)` 区域，点击后等 500ms。

```jsonc
"PopUpWindowsTipConfirm": {
    "recognition": "OCR",
    "roi": [175, 646, 436, 148],
    "expected": ["确定"],
    "action": "Click",
    "target": true,
    "post_delay": 500,
    "timeout": 2000
}
```

**用途**：与 `PopUpWindowTip` 配套使用，处理所有带"提示"标题的弹窗。

### 6. `PopUpWindowConfirm`（通用"确定"按钮）

OCR 模糊匹配单字"确"在 `(100, 623, 532, 392)` 区域，覆盖弹窗底部任意"确定/确认"按钮。

```jsonc
"PopUpWindowConfirm": {
    "recognition": "OCR",
    "expected": ["确"],
    "roi": [100, 623, 532, 392],
    "action": "Click",
    "target": true,
    "post_delay": 200,
    "timeout": 2000
}
```

### 7. `PopUpWindowCancel`（通用"取消"按钮）

OCR 模糊匹配单字"取"在 `(100, 623, 532, 392)` 区域，覆盖弹窗底部任意"取消"按钮。

```jsonc
"PopUpWindowCancel": {
    "recognition": "OCR",
    "expected": ["取"],
    "roi": [100, 623, 532, 392],
    "action": "Click",
    "target": true,
    "post_delay": 200,
    "timeout": 2000
}
```

### 8. `UI_PopInform`（事件总线弹窗）

监听 `Node.Action.Succeeded` / `Node.Action.Failed` 事件，自动弹出"操作成功/失败"提示。仅在主界面注册生效。

```jsonc
"UI_PopInform": {
    "focus": {
        "Node.Action.Succeeded": {
            "content": "操作成功",
            "display": ["log", "modal"]
        },
        "Node.Action.Failed": {
            "content": "操作失败",
            "display": ["log", "modal"]
        }
    }
}
```

## 使用约定

### [JumpBack] 包裹通用节点

用 `[JumpBack]` 前缀让通用节点处理完弹窗后**自动返回父节点**继续识别 next：

```jsonc
"MyTaskEntry": {
    "next": [
        "MyTaskMainStep",
        "[JumpBack]PopUpWindowConfirm",
        "[JumpBack]ClickCenter_500ms",
        "[JumpBack]BackButton_500ms"
    ]
}
```

### 业务节点的边界

`main_ui.json` 中以下节点是**业务专用**，不属于"通用节点"：

- 底部 Tab 节点（`UI_RoleListPage`、`UI_RoleFomationPage`、`UI_MapSwitch`、`UI_CastlePage`、`UI_TeamPage` 等）
- 大地图跳转（`UI_ReturnBigMap`）
- 业务容器（`UI_TaskPannelPageOpen/Close` 等）

## 新增通用节点的流程

1. **先 grep** `main_ui.json` 确认是否已存在：
   ```bash
   grep "BackButton\|ClickCenter\|PopUpWindow" assets/resource/base/pipeline/main_ui.json
   ```
2. **无则用 [`auto_generateNode` skill](../../../.claude/skills/auto_generateNode/SKILL.md)**（绑定 maa-mcp）——OCR 文字按钮用 `ocr` MCP 工具，图标按钮用 `screencap` + AI 视觉分析，再扩大 ROI（默认 `expand=50`）生成 JSON。
3. **落入 `main_ui.json`** 并在本文件"核心 8 个通用节点"小节同步登记。
4. **更新 [README.md](./README.md) 索引**（如新增类别）。

## Lint 提示（待落实）

仓库当前**不存在** `tools/add_node_defaults.py`。**建议**在 `tools/ci/check_resource.py` 中加入如下校验：

- 通用节点（`BackButton_*` / `ClickCenter_*` / `PopUpWindow*` / `UI_PopInform`）**必须显式声明** `rate_limit: 0` / `pre_delay: 0` / `post_delay: 0` 之一为 0。
- 避免协议默认值（`rate_limit=1000ms`、`pre_delay=post_delay=200ms`）引入隐式等待，干扰状态驱动流程。
