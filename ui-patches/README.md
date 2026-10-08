# 卡牌名单界面补丁

适用于当前 MFAAvalonia v2.12.2 定制客户端，构建目标为 `MFAAvalonia.Core.dll`。三个源文件保留在 `MFAAvalonia/` 对应目录；将它们覆盖至匹配的客户端源码后编译 Core 组件。

界面沿用 SukiUI 控件与现有主题。预设依次为好调、集中、干劲、好印象、强气、全力，分别保存黑名单、白名单、替换名单及启用状态。旧配置迁移至“好调”，原“感性”预设自动改名并保留全部内容；新增预设初始为空。

`config/card_lists.json` 使用 `activePreset` 与 `presets` 保存所有预设，根级 `blacklist`、`whitelist`、`replacementList` 保存当前预设快照。`debugDiagnosticsEnabled` 为全局开关，不随预设切换。

换出及商店删除顺序：N → 黑名单 → R → 已启用替换名单，替换名单内 SR 优先于 SSR。白名单通常受保护，明确加入替换名单时允许换出或删除。普通奖励选取与商店买卡沿用黑白名单策略。

本仓库不包含完整 MFA 源码；`tests/CardListUi` 引用本地 `build-mfa-ui/MFAAvalonia/MFAAvalonia.csproj`，在该源码应用补丁并配置 .NET 10 SDK 后运行。测试使用独立配置目录，不改写用户配置。

部署仅更新 Core 组件和 Agent 动作文件；不复制构建目录里的框架运行库，保持便携版 MaaFramework 5.13.0。
