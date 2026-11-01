# 2.2.0 原生界面重构进度（工作记录）

本文件供续接工作使用。功能迁移清单见 `ui-2.2.0-migration.md`。

## 基准

- 主仓库 `pre` 起点：`d08174d`（= v2.1.5 正式版）。工作分支 `ui-2.2.0`（已推送 origin）。
- 原型：MaaGakumasuEX-Lab `a0a7681`，`ui-prototypes/modern-preview/`。
- 本机环境：Linux 云端，`~/.dotnet` 为 .NET 10 SDK；无 Windows、无 X 服务器。Windows 验证用 `.github/workflows/windows-package.yml`（GitHub windows-latest）：手动运行时填 `release_tag` 即验证已发布的 Release 资产，否则从当前提交构建打包；推送到 `pre` 且涉及客户端、测试或打包脚本时自动运行。它不发布、不改标签、不碰 main。
- 运行依赖基线：`tools/prepare_runtime_baseline.py`（v2.1.5 Release ZIP，SHA-256 固定）。本地解压副本：`~/work/runtime-v2.1.5`。
- 原生截图：`tests/NativeShots`（无头 Skia 渲染真实 App）。构建后在 `tests/NativeShots/bin/Release/net10.0` 内执行 `dotnet NativeShots.dll . <out> [basic|all|sizes|lang:xx|tutorial]`。
- 隔离 UI 测试的夹具目录：`~/work/fixture`。

## 阶段

1. 设计令牌、全局样式、Liquid 控件：完成。
2. 窗口框架、导航、实例页签：完成。
3. 工作台（LiquidBoard）：完成。
4. 出牌规则、饮料、卡牌名单、目标编辑器、监控、画面捕获、设置：完成。
5. 弹窗、Toast、教程浮层：完成。
6. 减少动态效果：完成。以动画优先级覆盖模板内的 Transitions，启动时即生效。
7. 文档：迁移清单、v2.2.0 发布说明与更新公告完成。
8. 打包与发布：v2.2.0 Pre-release 已发布。当前资产由 Windows 工作流从 `bb54b15` 之后的提交打包并在同一运行中验证。

## 验证记录（Linux）

| 检查 | 结果 |
| --- | --- |
| `tests/CardRulesUi`（`--rules-only`） | 614 项检查，3600 个策略向量，通过 |
| `tests/DrinkUi` | 通过 |
| `tests/CardListUi`（含减少动态效果 3 项） | 通过 |
| `tests/LiquidBoardTests` | 7645 项检查，通过 |
| `tests/native_release_policy` | 25 项，通过 |
| Watchdog | 1036 项，通过 |
| `pytest` | 1261 项通过，跳过 1 项（等价检查已用 `CARD_RULE_VECTORS` 单独补跑 13 项，通过） |
| NativeShots 深浅主题、1280×800 / 1600×1000、英文界面 | 已逐页对照原型 |
| NativeShots `tutorial` | 21 步在深浅主题下均找到目标 |
| 发行 ZIP 内容 | 与 v2.1.5 ZIP 文件清单相同（无增删）。内容不同的只有 8 个文件：Core 及其 3 个语言资源、`interface.json`、更新公告、`PORTABLE_BUILD.json`、`pip_config.json`；其余差异仅为 CRLF/LF 换行。依赖包版本与基线 `deps.json` 逐项一致 |
| 发行 ZIP 私有数据 | `config/` 只有默认文件；不含日志、调试目录、实例配置、截图。更新渠道默认稳定版（`EnablePrereleaseUpdates=false`） |
| 多实例与窗口缩放（发行包默认三实例配置） | 实例页签关闭按钮正常；任务列表标题与计数在 1280/1600 下完整显示；1600→1280→1000→1600 缩放后滚动条按需出现并能消失（NativeShots `probe`） |
| 发行包资源无头运行 | 用 ZIP 中的 Core 与资源运行 NativeShots：中、日界面加载正常，配置保存正常；旧版网格布局迁移正确，升级后原有配置键无丢失、无改动 |

## 验证记录（Windows，GitHub windows-latest，Windows 10.0.26100）

| 检查 | 结果 |
| --- | --- |
| 发行 ZIP | SHA-256 核对、解压、`PORTABLE_BUILD.json` 与构建提交一致 |
| 真实窗口启动（两次，第二次沿用第一次的配置） | `--ui-verify` 完成，无 fatal；每次 33 张 PrintWindow 截图；配置保存且重启后保留；默认不检测测试版 |
| 窗口尺寸 | 屏幕设为 1920×1080，1280×800 与 1600×1000 均为实际尺寸（工作流在窗口被缩小时判失败） |
| 背景模糊 | 深浅主题、两种尺寸下弹窗的 BackdropBlur 均在真实合成器上生效 |
| 性能 | 页面切换 ≤ 约 1.4 s（含巡览固定等待 1.2 s）；工作集约 570–600 MB（含弹窗） |
| 原生 UI 测试（在解压包副本上） | CardRulesUi、DrinkUi、CardListUi、LiquidBoardTests、ReleasePolicy 全部通过 |
| 实机发现并修复 | 窄任务设置面板中选项名称压在控件上；规则页回合区间被挤压；卡牌名单筛选行溢出；1024×768 默认屏幕导致窗口被缩小（工作流已设分辨率） |

未验证：125/150/200% DPI（构建机固定 100%）、输入法实机组字、从旧版经自动更新升级。

## 下一步

1. 之后若有修复：更新同一 v2.2.0 测试包、说明、标签和构建提交，保持一致（见 AGENTS.md）。
