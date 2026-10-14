# 饮料页面布局恢复

2026-10-11 后续范围：按用户截图删除页首右上角保存说明及保底条右侧徽标文字，保留主要保底说明和四列表格；不调整设置语义。复用下述 UI 工作流入口和既定布局。定向 UX 检索未返回相关文字删除建议，按用户明确范围执行。

范围已由用户确认：恢复 `D:/Codex/drink-ui-preview/template.html` 和用户图二的四列表格、独立保底提示、目标筛选、搜索和紧凑分隔行。沿用 Avalonia/SukiUI 和现有配置；不修改战斗逻辑、模型、饮料设置文件或发布包。此为已定布局修复，不另开设计访谈。

采用 ui-workflow 与 ui-ux-pro-max。入口：`C:/Users/SIMO/.agents/skills/ui-ux-pro-max/SKILL.md`，SHA256 `8b827fe03b349aeea99a7d6cf4ccd892e6065b672859fdb11e13a1919453eddc`。查询 `responsive grid columns --stack avalonia` 返回 Avalonia 12 DataGrid 建议；本项目为 Avalonia 11.3.12，当前是仅 28 行的配置表单，不新增 DataGrid 依赖。使用同列宽 Grid 保持表头/行对齐、主题资源和原生输入控件。

验收计划：800/1120/1600 宽度，明暗主题，三种使用时机，筛选/数量/搜索/空结果，保存和重新打开，选择卡牌弹层。构建完成后部署仅 UI 程序集，先备份，再核对用户配置哈希。尚未执行的检查不算通过。

## 验证与部署

- Avalonia 原生无头测试通过：`DRINK_UI_TESTS_PASSED`。覆盖 28 种饮料、六种默认目标、图片、卡牌筛选选择、即时保存/重开、回合设置、目标筛选和计数、编号搜索、空结果、损坏配置保护。
- 渲染 800/1120/1600 宽度与明暗主题；人工检查 1120 明色、800 暗色、1600 明色的四列对齐和三种触发条件。截图在 `tests/DrinkUi/bin/Release/net10.0/drinks-*.png`，测试使用隔离配置，不是用户正在运行的窗口。
- 构建成功，0 错误、9 条现有警告；`git diff --check` 通过。
- 仅部署 `MFAAvalonia.Core.dll` 到本地 v1.5.2 便携目录。旧文件备份：`D:/Codex/backups/20261011-drink-table-layout-015339/MFAAvalonia.Core.dll`。
- 部署程序集 SHA256：`C9ABFE5CC9C696017986AF38B19AA717921090D7C1101A942D3960F7ECD72A83`。源与目标哈希一致，7 个用户配置文件哈希未改变。
- 未生成 ZIP、未上传 GitHub、未部署战斗识别代码；重开程序后加载新 UI。
