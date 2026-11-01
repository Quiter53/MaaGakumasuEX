# 2.2.0 第二轮修复进度（工作记录）

来源：用户 2026-11-01 的《UI 问题审计》与第二轮目标模式要求（被审计包源码 `ee2a48c`）。本文件供续接使用；全量迁移矩阵见 `ui-2.2.0-matrix.md`（建立中）。

## 状态约定

每项按“触发条件 → 已证明的原因 → 修改位置 → 实际验证 → 剩余限制”记录。审计中的推测需复现证实或排除后再写结论。

## 基线

- 开始时远端 `pre` = `1d55f9c`，`main` = `d08174d`，`v2.2.0` 标签 = `bb54b15`（当前 Release 资产来自 `bb54b15` 的 Windows 构建，已发布）。
- 第二轮期间暂不替换 Release 资产，直到 P0 修复并通过 Windows 验证。

## 进度

| 编号 | 问题 | 状态 |
| --- | --- | --- |
| UI-01 | SegmentedControl `Invalid unit: E-05px` | 已修：根因经测试复现（打包内 Avalonia 解析 `translate(±1E-05px, …)` 抛 FormatException）；SegmentedControl、LiquidBoard.Flip、侧栏指示条改用 `LiquidTransforms.Translate`（TransformOperations.Builder）；滑块更新合并为一次排队并在脱离可视树后丢弃。LiquidBoardTests 新增 9 项（解析器行为、±微小值、快速切换/改样式/重新挂载、滑块落点） |
| QA-01 | 验证器失败仍可能绿灯 | 已修：失败写 `result: fail` 并设 `Environment.ExitCode = 1`（桌面入口为 void Main，来自基线，不能改用 Shutdown 返回码）；捕获绑定/布局错误、Dispatcher/AppDomain/未观察任务异常；页面、设置分区、设置行、下拉展开、截图、背景模糊均为断言；报告写入本身失败也判失败；导航到渲染完成的耗时与固定等待分开。工作流每次用 `--ui-verify-inject-failure` 自测失败路径（须退出码 1 且 result=fail）。无头运行已发现并排除“背景图片”行误报（显式隐藏的透明度滑条） |
| QA-03 | `--rules-only/` 误提交（31 个文件） | 已修：CardRulesUi 区分位置参数与开关，未知开关/缺输出目录直接失败；已删除误提交目录（向量文件由运行输出目录经 `CARD_RULE_VECTORS` 读取，Python 等价性 13 项通过） |
| UI-02 | 实例配置保存 IOException | 根因：`App` 与 `RootView` 构造函数在新实例启动时各调用一次 `LoadInstanceConfig` + `StartLazyLoadingAsync`（v2.1.5 已存在），两个懒加载循环并发写同一实例文件，对应日志中两次“所有实例加载完成”。已修：去掉 RootView 的重复调用，两个入口幂等；`JsonHelper` 按文件串行读写、同目录临时文件原子替换、按序号丢弃过期快照、外部短暂占用有限次退避，仍失败则返回 false 并记错误。ConfigSaveTests：1000 次并发保存与并发读取无错误；Windows 上另测外部占用（CI） |
| LOG-01 | 空目标/限流 | 已修：创建控制器前校验 ADB 序列号/路径、窗口句柄、PlayCover 地址，失败给出可操作原因（Toast 显示）；更新检查合并在途请求；GitHub 限流按 `X-RateLimit-Remaining=0`/`Retry-After` 判定（原判断依赖 ReasonPhrase 含 “403”，实际为 “Forbidden”，从未生效），重置前不再请求。CardRulesUi 新增 6 项。OpenProcess error 5 为 MaaFW 扫描系统进程，非选中目标权限问题，不改 |
| UI-04 | 下拉空白/透明 | Windows 实机复现：验证器展开 6 类下拉，打开状态、不透明度、位置均通过，但每个下拉只有选中项 1 个容器可见（例：稀有度 items=6 visible=1）；屏幕截图（BitBlt，含弹出窗口）中列表不可见，与用户“只看见全部来源”一致。第一个假设（InheritsTransform）经 CI 证伪（关闭后结果相同）。诊断显示弹层宿主为独立 PopupRoot 窗口，VirtualizingStackPanel 与视口均为完整尺寸却只实现了选中项（锚点）。修复：ComboBox 改用非虚拟化 StackPanel（任务中最大的选项列表为 27 项）；弹层/菜单/提示使用近不透明的 `LgPopupSurface`（独立窗口无法模糊）。待 Windows CI 确认 |
| UI-08 | 圆角按钮外直角底板 | 已证实：Avalonia 11 的 `TemplatedControl.ClipToBounds` 默认值为 true（探针打印各类型默认值），模板内的外阴影/焦点环被裁成控件矩形，矩形内的阴影残留成直角色块（Windows 实机与无头截图均可见）。修复：对绘制外阴影的按钮/下拉/输入/开关/分段/面板等统一设 `ClipToBounds=False`，需要裁切的内容在模板内部自行裁切。3 倍放大探针确认阴影为圆角柔光 |
| UI-09 | 弹窗残影、闪线、方块 | 已证实：连续帧中弹窗打开 16～140ms 出现从标题栏实例状态点（6×6 椭圆）出发的竖线/横线，打开后该处残留清晰方块。原因：BackdropBlur 采样当前渲染表面，局部（脏矩形）重绘时表面部分为上一帧已模糊的内容，模糊结果反馈叠加。修复：BackdropBlur 改为每窗口捕获一次页面画面（渲染窗口时临时隐藏覆盖层/对话框/Toast/教程层，不采样自身），各模糊面绘制该捕获的对应区域；窗口尺寸、主题变化或新浮层打开时重新捕获。修复后同序列各帧无线条/方块 |
| UI-07 | 卡牌名单真实拖动失效 | 已证实：卡片改为 Button 后，Button 在 OnPointerPressed 中标记事件已处理并捕获指针，冒泡的按下处理器收不到起点，拖动从未开始（点击仍正常）。修复：在卡库 ItemsControl 上以 handledEventsToo 监听按下/移动；超过阈值后释放按钮捕获，松手不再触发 Click 重复添加；DataContextChanged 改为释放旧 ViewModel 的订阅（原代码取消的是新 ViewModel）。CardListUi 经无头真实输入管线测试：按下可达、阈值拖动清除按压态、拖后松手不添加、普通点击添加、投放到黑名单、拒绝未知数据。系统级鼠标拖动待 Windows 交互验证 |
| K | 独立窗口按钮气泡 | 已实现：最小化/最大化/关闭移入标题气泡右侧独立玻璃胶囊（同高、同圆角、宽度按按钮、间距 12）；关闭悬停平滑变红、按下收缩；置顶保留在标题气泡右端；胶囊空白处可拖动窗口、双击最大化。标题统一为 MaaGakumasuEX。悬停/按下需 Windows 真实指针验证 |
| L | 玻璃层级 | 新增 `LgPopupSurface`（下拉、菜单、浮出、提示）；页面玻璃透明度略增（浅 62%→55%，深 64%→58%），不降低控件整体 Opacity |
| UI-04（确认） | 下拉 | Windows CI（71c4f54 起）两次启动 199 项全部通过，含每个下拉逐项可见 |
| UI-03 / F | 任务设置按原型 | 已实现：培育偶像头像网格（日文/汉化定义均识别，按选项内容而非键名）；数字输入步进器（步长取默认值精度，只写入验证正则接受的值，0～5 秒、两位小数、默认 1.5 由正则与默认值决定）；带图片的多选（使用道具）为物品卡片；共享 `CreateCaseSelector` 与下拉同一语义；窄面板名称上下排列；恢复选择不再滚动面板；选中项不再被主题实色覆盖。NativeShots `taskopts` 真实输入管线 10 项通过 |
| UI-05 / G | 设置页 | 分组改为 界面设置/配置管理/定时任务/性能设置/运行设置/连接设置/启动设置/通知设置/快捷键/版本更新/关于，描述写实际作用；主题文案 浅色/深色。Windows 截图逐分区检查：各分区控件均显示（审计中“只见说明”未在当前版本复现）。产品名统一 MaaGakumasuEX（资源值、关于、更新、窗口标题、通知）。旧背景样式/动画/过渡迁移到 背景光晕/光晕流动（新增）/减少动态效果，只迁移显式设置，测试 3 项 |
| UI-06 / H | 字号与开关 | Suki 主题把所有 ToggleSwitch 放大 1.1 倍（真实弹窗中探针确认），已复位；新增字号 token，设置页 116 处固定字号改用 token；步进器 −/+ 为矢量图标、无内边距居中 |
| QA-01（续） | 真实输入 | `--ui-verify-interactive`：Win32 SendInput 驱动卡牌拖入黑名单、点击添加、下拉选择、搜索输入、关闭按钮悬停、面板拖动/Esc 取消/恢复布局、曲线节点拖动、弹窗快速重开连续帧；由 Windows 工作流执行首次交互验证和复用配置的第二次启动，检查失败阻止产物发布 |
| QA-01（收尾） | 验证器屏外误点、向上弹层误报 | Windows 日志证明：窗口原位置 `(208,208)` 扩大到 `1600×1000` 后，卡牌中心落在 `y=1149`，超出 `1920×1080` 屏幕，拖动和点击均未命中；不是新的拖动实现故障证据。验证器每次改尺寸后按实际窗口边框居中于工作区，卡牌与投放目标须通过中心点命中检查；虚拟化缓冲区的卡牌不能当作可点击目标。下拉定位同时接受向下展开和屏幕底部向上展开，保留不透明、条目可见和名单变更断言，并记录坐标、焦点和拖动后截图 |

## 交付核查

1. 在 [Windows 构建与验证](https://github.com/Quiter53/MaaGakumasuEX/actions/workflows/windows-package.yml) 中选择 `pre` 当前提交：编译、两次真实窗口启动和原生测试应全部通过；工作流自测注入失败须返回 `result=fail`、退出码 1，才能继续正常验证。
2. [v2.2.0 Pre-release](https://github.com/Quiter53/MaaGakumasuEX/releases/tag/v2.2.0) 的 ZIP、SHA-256、说明与源码标签同步更新；包内 `PORTABLE_BUILD.json.sourceCommit` 应与标签解析出的提交及 Windows 构建提交一致。默认仍为稳定更新渠道，正式版 Latest 和 main 仍保留 v2.1.5。
3. 下载包后按同名 `.sha256` 核对摘要；验证范围与未验证项见 `ui-2.2.0-matrix.md`，125% 及以上 DPI、中文输入法、自动更新升级和完整培育不列为已验证。
