# 任务包（tasks/）

`interface.json` 里的每个任务，在这里都对应一个文件夹；文件夹是“源”，`interface.json` 由它们重建出来。

## 目录

```
tasks/
  index.json            任务顺序（改这里可以调整列表顺序）
  _options.json         所有选项定义（和 interface.json 的 option 同构）
  <entry>/task.json     单个任务（name / entry / option / description / doc）
  <entry>/run.py        单独运行这个任务的脚本（已存在时不会被覆盖）
```

`<entry>` 就是任务的 `entry`，比如 `Auto_YearlyTask`、`CastleMarryTest`。任务里只写选项名，选项定义统一放在 `_options.json`，避免多个任务各存一份。

## 三种用法

### 1. 改任务

1. 编辑 `tasks/<entry>/task.json`（名字、说明、勾选项）或 `tasks/_options.json`（选项和它的 pipeline_override）；
2. 运行 `python tools/taskpack.py build`（或网页版「任务 → 任务包 → 重建 interface.json」）；
3. 重启 Knights Auto Game，新任务/新选项生效。

> `build` 会先校验：文件夹和 entry 是否一致、选项是否存在、pipeline_override 里的节点在 `resource/base/pipeline` 里找不找得到；有错就不写文件。

### 2. 单独测试

```bat
python tasks\CastleMarryTest\run.py
python tasks\CastleMarryTest\run.py --set 联姻方式=联姻
python tools\taskpack.py run Auto_DailyTask --set DailyTaskSubTaskSwitch=购买市场折扣物品
```

`run.py` 通过本机网页版（默认 `http://127.0.0.1:8790`）运行任务，需要先启动网页版；加 `--no-wait` 只启动不等待。

### 3. 导出 / 导入 / 打包

| 操作 | 命令 | 说明 |
|---|---|---|
| 全量导出 | `python tools/taskpack.py export --force` | 用 interface.json 覆盖同名任务文件夹 |
| 单个导出 | `python tools/taskpack.py export CastleMarryTest --force` | 只更新一个任务 |
| 重建 | `python tools/taskpack.py build` | tasks/ → interface.json（旧文件留 `.bak`） |
| 检查 | `python tools/taskpack.py check` | 任务/选项/pipeline 引用 |
| 打包 | `python tools/taskpack.py pack CastleMarryTest` | 生成 `dist/tasks/CastleMarryTest.zip` |
| 导入 | `python tools/taskpack.py unpack 路径.zip --build` | 合并到 tasks/ 并重建 |

也可以双击 `tools\任务包.bat`，用菜单完成这些操作。网页版「任务 → 任务包」提供同样的按钮（导入需要填 zip 路径）。

任务包 zip 里有 `taskpack.json`（元信息）、`task.json`、它用到的 `options.json`、`README.md` 和 `run.py`，可以直接发给别人导入。

## 注意

- `interface.json` 是 build 的产物；如果直接在 interface.json 里改了东西，用 `export --force` 同步回 tasks/（会覆盖 tasks/ 里的同名任务）。
- 改完 tasks/ 一定要 build；只改 tasks/ 而不 build，Knights Auto Game 和网页版看到的还是旧任务。
- `run.py` 不会被覆盖，可以放心在里面加自己的调试代码。
