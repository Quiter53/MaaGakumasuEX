完整、可构建的 v2.0.0 原生客户端源码位于 `native-ui/`，历史补丁仅供追溯。

# 卡牌名单界面补丁

适用于当前 MFAAvalonia v2.12.2 定制客户端，构建目标为 `MFAAvalonia.Core.dll`。三个源文件保留在 `MFAAvalonia/` 对应目录；将它们覆盖至匹配的客户端源码后编译 Core 组件。

界面沿用 SukiUI 控件与现有主题。预设依次为好调、集中、干劲、好印象、强气、全力，分别保存黑名单、白名单、替换名单及启用状态。旧配置迁移至“好调”，原“感性”预设自动改名并保留全部内容；新增预设初始为空。

`config/card_lists.json` 使用 `activePreset` 与 `presets` 保存所有预设，根级 `blacklist`、`whitelist`、`replacementList` 保存当前预设快照。`debugDiagnosticsEnabled` 为全局开关，不随预设切换。

换出及商店删除顺序：N → 黑名单 → R → 已启用替换名单，替换名单内 SR 优先于 SSR。白名单通常受保护，明确加入替换名单时允许换出或删除。普通奖励选取与商店买卡沿用黑白名单策略。

本仓库不包含完整 MFA 源码；`tests/CardListUi` 引用本地 `build-mfa-ui/MFAAvalonia/MFAAvalonia.csproj`，在该源码应用补丁并配置 .NET 10 SDK 后运行。测试使用独立配置目录，不改写用户配置。

部署仅更新 Core 组件和 Agent 动作文件；不复制构建目录里的框架运行库，保持便携版 MaaFramework 5.13.0。

# 饮料策略页面

新增文件为 `DrinkSettingsView.axaml`、代码后置和 `DrinkSettingsViewModel.cs`。
执行 `tools/apply_drink_ui.ps1` 将它们复制到本地客户端源码，并幂等注册侧栏页面。
共享数据为 `agent/custom/drink_catalog.json`；用户设置为 `config/drink_policy.json`。
不要用测试配置覆盖用户设置。客户端编译后只部署 `MFAAvalonia.Core.dll`，不替换框架或模型。
`tests/DrinkUi` 使用独立配置和现有卡库验证保存、过滤、图片与明暗主题。

# 出牌规则页面（原生 Avalonia）

`tools/apply_card_rules_ui.ps1` 将 `CardRulesViewModel.cs`、`CardRulesView.cs`、
`CardRulesDialogs.cs`、`CardRuleCurve.cs` 应用到匹配的客户端源码并注册侧栏。
全部是现有 Avalonia 控件、原生绘图与页面内弹窗，不使用 HTML/WebView2，也不增加客户端运行依赖。
HTML 仅作为视觉设计参考，不能部署到客户端。

配置为 `config/card_rules.json`（version 2）。内置六个职业预设；v2.0.0 按要求移除新建/管理按钮，仍可切换已有预设并保存规则。
保存时校验全部预设并原子替换，保留 `.json.bak`，检测多窗口写入冲突。
未保存修改在切换页面时留在内存；关闭客户端前须保存。生效对象为保存文件中 `active` 指定的预设，不自动推测职业。
缺失配置时使用 `agent/custom/card_rules.defaults.json`：干劲预设包含 `届いて！` 的 1–2 回合和 `せのびの魔法` 的 1–7 回合限制。
其他内置预设初始为空。删除或停用规则后不再施加该卡的使用限制、升档和曲线。

顺序为使用时机、再动、有效稀有度、曲线分数、手牌原位置。普通与强化共用规则。
剩余回合包含追加回合，线性与阶梯均支持，节点外沿用最近值。实际选牌每次检查配置更新时间；
已选中的卡在提交前再次检查时机。卡名未识别且同档同分时，保留原来的已确认安全牌优先保护，
未知牌仍通过选中后的核名流程处理。未确认身份不会触发指定牌前饮料。
已经移除临时开局牌/出分牌后置/末段 SR 出分优先策略，不能重新引入 `endgame_priority.py`。

构建后只复制 Core DLL、对应 Agent 改动与默认规则 JSON，不复制构建目录的 MaaFramework 或其他 DLL。
本轮 UI 基线为 `diagnostics/memory-multi-target-20261013/ui`，其旧 Core 哈希与现用运行包一致；
不要使用缺少后续记忆卡等修复的更早客户端源码覆盖现用 UI。

`tests/CardRulesUi` 使用独立卡库/配置测试实际按钮事件、原生指针拖动、保存重载、筛选与明暗主题渲染，
并导出 `native-policy-vectors.json`。将 `CARD_RULE_VECTORS` 指向该文件后运行 `tests/test_card_rules.py`，
会将 3,600 组原生计算结果与 Python 比较。另保留 `test_card_rule_*` 战斗保护回归。
这些检查不等同于实机无人值守长流程验证。
