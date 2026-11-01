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
| UI-04 | 下拉空白/透明 | Windows 实机复现：验证器展开 6 类下拉，打开状态与不透明度、位置均通过，但每个下拉只有选中项 1 个容器可见（例：稀有度 items=6 visible=1），屏幕截图（BitBlt，含弹出窗口）中弹层完全不可见；无头 Linux（弹层画在主窗口覆盖层）正常。修复：去掉液态 ComboBox 弹层的 `InheritsTransform`（v2.1.5 的 Suki 模板没有；Windows 上弹层是独立窗口，继承根部字体缩放 LayoutTransform 导致虚拟化面板视口为空）。验证器记录弹层宿主、面板、视口诊断，待 Windows CI 确认 |
| UI-08 | 圆角按钮外直角底板 | 已证实：Avalonia 11 的 `TemplatedControl.ClipToBounds` 默认值为 true（探针打印各类型默认值），模板内的外阴影/焦点环被裁成控件矩形，矩形内的阴影残留成直角色块（Windows 实机与无头截图均可见）。修复：对绘制外阴影的按钮/下拉/输入/开关/分段/面板等统一设 `ClipToBounds=False`，需要裁切的内容在模板内部自行裁切。3 倍放大探针确认阴影为圆角柔光 |
| UI-09 | 弹窗残影、闪线、方块 | 已证实：连续帧中弹窗打开 16～140ms 出现从标题栏实例状态点（6×6 椭圆）出发的竖线/横线，打开后该处残留清晰方块。原因：BackdropBlur 采样当前渲染表面，局部（脏矩形）重绘时表面部分为上一帧已模糊的内容，模糊结果反馈叠加。修复：BackdropBlur 改为每窗口捕获一次页面画面（渲染窗口时临时隐藏覆盖层/对话框/Toast/教程层，不采样自身），各模糊面绘制该捕获的对应区域；窗口尺寸、主题变化或新浮层打开时重新捕获。修复后同序列各帧无线条/方块 |

## 下一步

1. Windows CI 确认 UI-04 下拉修复；运行一次 `inject_failure=true` 演示失败阻断。
2. 新建预设弹窗中“复制当前预设”开关行左侧越出内容边距（排版统一时处理）。
3. 迁移矩阵 `ui-2.2.0-matrix.md`；任务设置专用呈现（F）。
