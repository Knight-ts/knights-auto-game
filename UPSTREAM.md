# 上游与来源说明（UPSTREAM）

本项目 **Knights Auto Game**（仓库名 `Knights-web-windows`）是一个非官方衍生项目，不是 MAAGC 的官方仓库，也不隶属于 MaaXYZ 或 KhazixW2。

## 来源

- 初始代码与资源来自 **MAAGC v1.6.1** 的 Windows x86_64 发行包（目录 `maagc-win-x86_64-v1.6.1-MFAA`），
  导入日期：2026-10-20。
- 导入方式：手动复制源码与资源后重新初始化 Git，**与上游没有共享的 Git 历史**，因此无法用 `git log` 追溯上游提交。
- 本项目在原项目基础上做了网页化重写：新增 `webapp/`（Python 直接驱动 MaaFramework、浏览器控制台），
  移除了对 MFAAvalonia 桌面 UI 的依赖，并修复了控制器配置、依赖版本等问题。

## 上游仓库

| 项目 | 地址 | 许可证 | 用途 |
|---|---|---|---|
| MAAGC | <https://github.com/KhazixW2/MAAGC> | MIT | 原始项目：Agent、Pipeline、resource/base、table 数据 |
| MaaFramework | <https://github.com/MaaXYZ/MaaFramework> | **LGPL-3.0**（Copyright (c) MaaXYZ） | 自动化框架，以动态库方式调用、未修改源码；`docs/maafw_doc/` 来自其文档 |
| Android platform-tools | <https://developer.android.com/tools/releases/platform-tools> | Apache-2.0 | `tools/adb/` 下的 adb 与 DLL |

## 同步方式

- 本仓库 **不自动跟随上游**。上游更新时，需要手动对比 `resource/base/pipeline`、`agent/`、`table/` 的差异后合并。
- 版本号：`interface.json` 的 `version` 字段自 `v0.1.0` 起由本项目独立编号；导入时的上游版本为 MAAGC v1.6.1。

## 第三方素材说明

- `resource/base/image/` 为游戏《诸神皇冠 / 百年骑士团》的 UI 截图模板，`table/*.json` 为游戏数据整理；
  相关美术与数据版权归游戏权利方所有，本项目仅在私有仓库中用于个人自动化，**公开前需重新评估**。
- `tools/adb/` 内的 adb 为 Android platform-tools（Apache-2.0），随仓库分发以固定调用路径。
- 完整的第三方许可证清单与全文见 `NOTICE` 与 `licenses/` 目录。

## 与上游的关系（一句话）

**Knights Auto Game 是 MAAGC（MIT）的网页版衍生重写，使用 MaaFramework（LGPL-3.0）驱动，与上游无 Git 历史关系、非官方。**
