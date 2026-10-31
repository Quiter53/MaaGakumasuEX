# 2.2.0 原生界面重构进度（工作记录）

本文件供续接工作使用，记录当前阶段、已完成项与下一步。功能迁移清单见 `ui-2.2.0-migration.md`（待建）。

## 基准

- 主仓库 `pre` 起点：`d08174d`（= v2.1.5 正式版）。
- 原型：MaaGakumasuEX-Lab `a0a7681`，`ui-prototypes/modern-preview/`。
- 本机环境：Linux 云端，`~/.dotnet` 为 .NET 10 SDK；无 Windows、无 X 服务器。
- 运行依赖基线：`~/work/runtime-v2.1.5`（v2.1.5 Release ZIP 解压，SHA-256 已核对）。
- 原型截图：`~/work/proto-shots/`（Playwright，`LD_LIBRARY_PATH=~/.cache/sentinel-pw-libs/root/usr/lib/x86_64-linux-gnu node ~/tools/tour.mjs <out>`）。
- 原生截图：`tests/NativeShots`（无头 Skia 渲染真实 App）。构建后在 `tests/NativeShots/bin/Release/net10.0` 内执行 `dotnet NativeShots.dll . <out>`（该目录已链接 assets 与 config）。

## 已完成

1. 设计令牌 `Assets/Style/Liquid/LiquidTokens.axaml`（深浅主题，取值来自原型 CSS）。
2. 全局控件样式 `Assets/Style/Liquid/LiquidControls.axaml`：按钮、开关、复选、单选、输入框、步进器、下拉框、滑条、滚动条、列表、页签、菜单、提示。
3. 控件：`Controls/Liquid/` 下 LiquidSheen（指针高光）、GlassPanel、SegmentedControl、LiquidTheme（强调色/减少动态）、LiquidBackground（光晕背景）、LiquidIcon（原型图标）。
4. App.axaml 已接入令牌和样式；App.Initialize 调用 `LiquidTheme.Initialize`。

## 已知问题

- ToggleSwitch 新模板渲染为绿色实心条，看不到圆钮（待修）。
- 减少动态效果：模板内 Transitions 优先级高于样式，需要在 LiquidTheme 中遍历模板去除。

## 下一步（按顺序）

1. 修开关；覆盖 Suki 颜色键（LightTheme/DarkTheme.axaml）。
2. SukiWindow 新模板 + RootViewContent 重写：标题栏（Logo、版本徽标、实例页签、窗口按钮、置顶）、侧栏（滑动指示、折叠、主题切换、设置）、页面切换动画、教程浮层导航适配。
3. 工作台：顶部工具栏（资源/控制器分段、设备下拉、实时画面、恢复布局、状态、开始）；新布局模型 LiquidBoard（列模型、拖动让位、分隔条、持久化、旧布局迁移、键盘移动）；面板标题样式。
4. 任务设置选项（TaskOptionGenerator）风格化；回忆/卡组/自定义目标编辑器。
5. 出牌规则页（新增可选字段 totalTurns，仅编辑器横轴）、选卡器、手牌模拟。
6. 饮料策略、卡牌名单、多开监控、画面捕获、设置页、弹窗/Toast。
7. 测试、迁移清单文档、Windows 构建与截图工作流、打包、v2.2.0 Pre-release。
