# 2.2.0 原生界面重构进度（工作记录）

本文件供续接工作使用。功能迁移清单见 `ui-2.2.0-migration.md`。

## 基准

- 主仓库 `pre` 起点：`d08174d`（= v2.1.5 正式版）。工作分支 `ui-2.2.0`（已推送 origin）。
- 原型：MaaGakumasuEX-Lab `a0a7681`，`ui-prototypes/modern-preview/`。
- 本机环境：Linux 云端，`~/.dotnet` 为 .NET 10 SDK；无 Windows、无 X 服务器。用户未授权 gh `workflow` 权限，不能推送 `.github/workflows`；Windows 工作流示例放在 `docs/development/ci/windows-package.yml.example`。
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
7. 文档：迁移清单完成；发布说明待写。
8. 打包与发布：待做（Linux 交叉编译 win-x64；发布前须征得用户同意，因为 Windows 实机未验证）。

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

未验证：Windows 实机窗口、100/125/150/200% DPI、输入法实机组字、PrintWindow 截图、发行 ZIP 在 Windows 上的启动与依赖装载。`--ui-verify <dir> --ui-verify-exit` 可在 Windows 上自动巡览并输出 `report.json`。

## 下一步

1. 编写 `docs/releases/v2.2.0.md` 并更新 `assets/resource/announcement/01_更新公告.md`。
2. 在 Linux 上交叉编译 win-x64，用 `tools/build_ex_release.py --prerelease` 打包并核验 ZIP 内容。
3. 征得用户同意后：合并到 `pre`，创建标签 `v2.2.0`，发布 Pre-release（prerelease=true，make_latest=false），核对 main 与 Latest 未变。
