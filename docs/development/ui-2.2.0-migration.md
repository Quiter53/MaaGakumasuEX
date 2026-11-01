# 2.2.0 原生界面迁移清单

2.2.0 将 `native-ui` 客户端整体改为 Lab 原型（`MaaGakumasuEX-Lab` `a0a7681`，`ui-prototypes/modern-preview/MaaGakumasu-UI-Modern-Preview.html`）的液态玻璃风格。界面全部使用 Avalonia 原生控件，不加载 HTML，不使用截图，也不使用原型 JS 中的演示状态。

基准为 v2.1.5（`d08174d`）。本清单对照原功能入口、新入口、视图模型、命令和配置字段，并记录原型与原程序冲突时的处理方式，以及未实现的视觉差异。

## 1. 架构

| 项目 | v2.1.5 | 2.2.0 |
| --- | --- | --- |
| 窗口框架 | SukiWindow + SukiSideMenu | SukiWindow 新模板（`LiquidControls.axaml`）+ `RootViewContent` 自绘标题栏与侧栏；页面常驻，切换时不重建 |
| 导航 | SukiSideMenuItem | `LiquidNavigation.Navigate(page)` / `NavigateSettings(section)`；侧栏折叠状态保存在 `UI.Liquid.SidebarExpanded` |
| 主题 | Suki 颜色主题 + 深浅模式 | 沿用原 `BaseTheme` 与颜色主题配置。默认 Blue 主题映射为原型强调色 #007AFF / #0A84FF，其他颜色主题仍然生效 |
| 控件样式 | Suki ControlTheme | `LiquidTokens.axaml`（深浅两套令牌）+ `LiquidControls.axaml`（全局样式，放在 SukiTheme 之后） |
| 磨砂 | 无 | `BackdropBlur`：截取已渲染的背后画面，经 Skia 模糊与饱和处理后绘制，并裁剪为圆角。不是窗口级 Acrylic/Mica |
| 弹窗 | SukiDialog、独立 Window | SukiDialog 套用新模板；共用 `LiquidModal`（窗口内浮层：Esc 关闭、焦点循环与还原、进出场动画） |
| 文案 | `LangKeys` + `ExTranslations.json` | 原有键不变；新增中文原文键 207 条（共 650 条），提供英、日、繁中翻译。XAML 中用 `{lq:T 中文}` |

## 2. 页面与功能对照

### 2.1 标题栏与侧栏

| 原功能 | 新入口 | 实现 |
| --- | --- | --- |
| 多开实例页签（新建、关闭、重命名、拖动排序、右键菜单） | 标题栏中部的胶囊页签（仅工作台与画面捕获页显示，与原型一致） | `InstanceTabBar StripOnly`，内容区为 `InstanceContainerView ContentOnly`；VM 与命令不变 |
| 深色、浅色切换按钮（`SwitchToDarkTheme` / `SwitchToLightTheme`） | 侧栏底部的主题按钮（单键切换） | 写入同一个 `GuiSettingsUserControlModel.BaseTheme` |
| 置顶、最小化、最大化、关闭 | 标题栏右侧窗口按钮 | 原窗口命令 |
| 首次使用教程（21 步） | 不变 | 设置相关步骤改为指向新侧栏的“偏好设置”按钮与分组设置页（启动设置、关于）。提示框改为玻璃样式。NativeShots `tutorial` 模式逐步检查，确认每一步都找到了目标 |
| 隐藏的“资源选项”页（`IsVisible=False`） | 未加入导航 | 原本即为隐藏页 |

### 2.2 任务工作台（`TaskQueueView`）

| 原功能 | 新入口 | 说明 |
| --- | --- | --- |
| 资源切换、控制器类型、设备选择、连接、刷新、开始/停止 | 顶部工具栏 | 绑定原 `TaskQueueViewModel` 的属性与命令。资源说明在下拉项上以提示显示 |
| 任务列表：勾选、全选、反选、添加、重置、拖动排序、右键、单独运行 | 左上“任务列表”面板 | 原 ListBox 与拖放逻辑；副标题显示“已启用/总数”，切换语言时同步刷新 |
| 任务说明（Markdown） | 左下“任务说明”面板 | 原内容 |
| 任务设置 | 中间“任务设置”面板 | `TaskOptionGenerator` 生成。分组卡片；选项较短时使用分段控件；子选项之间加分隔线；下拉框宽度固定为 200/270 |
| 运行日志、导出日志、清空 | 右侧“运行日志”面板 | 原日志集合与命令 |
| 实时画面 | “实时画面”面板（默认关闭，可从工具栏开关） | `EnableLiveView` / `IsLiveViewExpanded` |
| 旧网格布局 `UI.DashboardCardGrid.Layout.TaskQueueView` | 新布局 `UI.Liquid.BoardLayout`（JSON） | 首次读取时由 `BoardSolver.FromLegacy` 迁移旧键，旧键保留不删 |
| — | 拖动面板标题：上下投放为同列叠放，左右投放为分列；拖动中实时让位并预览尺寸；分隔条调整列宽与高度；最小尺寸；最大化与还原；恢复默认；键盘移动（焦点在面板拖动柄上时按方向键）；取消、窗口外释放、失焦、PointerCaptureLost 和窗口缩放的处理 | `LiquidBoard` + `BoardLayout`。拖动时只移动现有控件，不重建，输入、焦点与滚动状态保留。`tests/LiquidBoardTests` 共 7645 项检查 |

### 2.3 出牌规则（`CardRulesView`）

| 原功能 | 新入口 |
| --- | --- |
| 六个内置预设与自定义预设；复制、重命名、删除；培育类型 | 顶部预设分段控件 +“新建预设”“管理预设” |
| 保存规则（旧按钮“保存规则”） | 顶部蓝色“保存”，调用原 `Save` 写入 `card_rules.json`；未保存时显示状态 |
| 规则搜索、添加、更换、删除、启用 | 左侧规则列表；添加和更换使用共享选卡器 `LiquidCardPicker` |
| 生效回合范围、稀有度覆盖、最多保留一张、自定义再动 | 规则详情的“回合与档位”区块；档位只提供真实策略允许的选项（SSR 可降为 R/SR，低稀有度只能升到 SSR，LR 不变） |
| 国民配合目标 | 选择与清除，并带警告提示 |
| 权重曲线 | 节点点击添加、拖动、双击或右键删除，精确编辑；折线/阶梯模式；模板；唯一的“默认权重”。新增可选字段 `totalTurns`（3..30，且不小于最大节点回合；缺省时沿用旧行为，旧文件无需改动） |
| 手牌模拟 | 回合范围跟随总回合数；连续拖动不重建滑条；加卡时优先列出当前预设的卡，并标出已在手牌中的牌；可移除；回合外的牌保留并标明 |

模拟排序直接调用 `PlayCardPolicy`（排序键为 Allowed、Extra、Tier、Priority、Slot），与实际出牌使用同一实现。`tests/CardRulesUi` 共 614 项检查和 3600 个策略向量；`pytest` 中的 `CARD_RULE_VECTORS` 用来核对 Python 侧的一致性。

### 2.4 饮料策略、卡牌名单、目标编辑器

| 原功能 | 新入口 | 说明 |
| --- | --- | --- |
| 饮料策略（时机、目标、卡牌选择） | 饮料策略页 | 原页面内嵌的选卡面板（Plans/Categories/Origins/Rarities/Conversions/Levels 筛选）改为共享选卡器，共 8 项筛选。VM 新增 `LoadCards`、`TitleFor` |
| 卡牌名单（黑名单、白名单、替换名单，六个预设，三个独立开关） | 卡牌名单页 | 卡库按行虚拟化；移除按钮统一为 `RemoveChip_Click`，按所在名单分派到原 `RemoveFromBlacklist/Whitelist/Replacement` |
| 回忆、卡组、自定义目标编辑器 | 由独立窗口改为窗口内 `LiquidModal` 弹窗 | `MemoryTargetDialog`、`CustomizationTargetDialog` 改为 UserControl；读写的选项字符串格式不变 |

### 2.5 多开监控、画面捕获

绑定与命令不变（对比脚本确认旧 XAML 中的绑定全部仍在），只更换卡片与工具栏样式。监控卡片的“打开实例”改用 `LiquidNavigation` 跳转。

### 2.6 设置

旧版设置页把 11 个设置控件纵向堆叠，现改为左侧分组列表加右侧区块，各设置控件本身保持不变。

| 分组键 | 设置控件 |
| --- | --- |
| gui | GuiSettingsUserControl（重写为外观页） |
| config | ConfigurationMgrUserControl |
| timer | TimerSettingsUserControl |
| perf | PerformanceUserControl |
| game | GameSettingsUserControl |
| connect | ConnectSettingsUserControl |
| start | StartSettingsUserControl |
| notify | ExternalNotificationSettingsUserControl |
| hotkey | HotKeySettingsUserControl |
| update | VersionUpdateSettingsUserControl（更新渠道下拉框不变，默认仍为“稳定版”） |
| about | AboutUserControl |

外观页提供：主题分段、强调色、背景光晕（`UI.Liquid.Ambient`）、减少动态效果（`UI.Liquid.ReduceMotion`）、背景图、语言、字体缩放、最小化到托盘、通知。

## 3. 配置兼容

- 不改名、不删除任何原有配置键。新增的键均为 `UI.Liquid.*`，缺省值即原型默认值。
- `card_rules.json`：只新增可选字段 `totalTurns`（值为 null 时不写入）。旧文件读入后原样保存，不会多出字段。
- 旧工作台布局：首次启动时迁移，旧键保留，回退到 v2.1.5 仍能读取。
- `BackgroundStyle`、`BackgroundAnimations`、`BackgroundTransitions` 三项原 Suki 背景设置不再出现在界面上，由“背景光晕”和“减少动态效果”代替。旧值保留在配置中，不删除。

## 4. 原型与原程序的冲突及处理

| 冲突 | 处理 |
| --- | --- |
| 原型的演示状态、模拟日志、假保存 | 不采用；全部接入真实 VM 与配置 |
| 原型规则页的“保存”只改内存 | 调用原 `Save`，写入文件并报告冲突；隔离测试中验证保存、重读和并发冲突 |
| 原型预设分段在加载时默认选中第一项 | 去掉 `AlwaysSelected`。否则页面加载会把已保存的预设切回“好调”（实际缺陷，已修复并加回归检查） |
| 手牌模拟的手牌数 | 沿用原校验 1..5 张，回合 1..30，节点 2..30 个 |
| 原型新增“新建预设/管理预设” | 采用，入口接到原有的复制、重命名、删除命令。旧测试中的“removed preset actions absent”改为检查新入口 |
| 原型目标编辑器是页面内弹窗 | 采用。原独立窗口改为窗口内弹窗，数据格式不变 |
| 原型没有教程、多开监控、热键、通知等页面 | 用同一套设计语言补齐 |
| 出牌策略、识别阈值、OCR、ONNX、MaaFramework 5.13.0、PrintWindow、战斗等待、培育流程 | 均未改动 |

## 5. 已知视觉差异

- 背景模糊只模糊同一窗口内已渲染的内容，不透出桌面，因为原型的 CSS backdrop-filter 本身也只作用于页面内容。没有使用窗口级 Acrylic/Mica。
- 原型用 CSS spring 曲线的地方，改用 Avalonia 的 `SplineEasing` 近似。
- 任务设置中的“出牌后额外等待”等输入型选项沿用接口定义的文本输入框，没有改成原型的加减步进器，以免改变原选项的输入格式。
- 特殊任务按钮上的 emoji 在 Linux 无头渲染中显示为方框；Windows 有系统 emoji 字体，未在实机核对。
- 原型 CSS 的多层阴影与 inset 高光改用 Avalonia BoxShadows 近似，模糊半径的计算方式与浏览器略有不同。

## 6. 验证状态

见 `ui-2.2.0-progress.md` 的“验证记录”。
