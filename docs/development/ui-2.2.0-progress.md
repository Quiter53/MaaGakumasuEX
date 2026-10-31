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

5. 开关已修；Suki 颜色键映射到令牌。
6. 窗口框架（SukiWindow 模板 + RootViewContent）：标题栏、实例页签胶囊（InstanceTabBar 分为 StripOnly/ContentOnly）、侧栏、页面常驻切换、LiquidNavigation。
7. 工作台：工具栏、LiquidBoard + BoardLayout（拖动预览、分隔条、键盘、最大化、持久化 UI.Liquid.BoardLayout、旧网格布局迁移）。
8. 任务选项：分组卡片、短选项分段控件、标签字重。
9. 设置页：分组列表 + 区块标题；外观页重写（主题分段、强调色、光晕、减少动态、背景图、语言、字体、托盘、通知）。
10. 修复：后台线程加载任务时视图在非 UI 线程更新（TaskQueueView 两处入口切回 UI 线程）。
11. 出牌规则页重写（CardRulesView.cs / .Detail.cs / CardRulesDialogs.cs）：预设分段 + 新建/重命名/删除、保存、规则列表、生效回合、档位覆盖（按真实规则限制可选档）、两组开关、国民配合目标与警告、曲线（总回合数 totalTurns、平滑/阶梯、模板、拖动按引用、点击加点、双击/右键删点、精确编辑）、手牌模拟（滑条不重建、回合外、移除、加卡优先当前预设）。
12. 共享组件：LiquidModal（弹窗、Esc、焦点循环与恢复、进出场动画）、LiquidCardPicker（8 项筛选、分页、实例复用）、BackdropBlur（Skia 截取背后画面模糊，弹窗与遮罩已使用）。

分支：`ui-2.2.0`（本地），尚未推送。

## 已知问题

- 减少动态效果：模板内 Transitions 优先级高于样式，需要在 LiquidTheme 中遍历模板去除。
- Toast、SukiDialog、教程浮层尚为旧样式。
- 教程第 13 步以后在设置页定位旧 SettingsLayout/GlassCard，需要按新分组适配。

## 下一步（按顺序）

4. 回忆/卡组/自定义目标编辑器风格化（改用 LiquidModal / LiquidCardPicker）。
5. tests/CardRulesUi 按新界面更新（旧测试依赖 Button 文本“保存规则”、Width==265 的选卡按钮等）。
6. 饮料策略、卡牌名单、多开监控、画面捕获、设置页、弹窗/Toast。
7. 测试、迁移清单文档、Windows 构建与截图工作流、打包、v2.2.0 Pre-release。
