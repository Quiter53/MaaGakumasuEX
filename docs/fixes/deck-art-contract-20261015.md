# v2.0.2 本地卡图接口修复

旧 `deck_art.improve_identities` 写入 `deck_art_score`、`deck_art_margin` 和 `deck_art_trusted`，但 `scan_identity_known` 没有读取这些字段，导致可信结果不能用于扫描排序。修复为在未尝试新版方形卡图匹配时，接收满足旧匹配器既有分数、候选差距和边框稀有度要求的结果。

当前删换牌使用 `square_identity` 的路由保持原样；新版匹配拒绝后，旧结果仍不能覆盖拒绝。失败探测、身份冲突和旧帧证据清理继续生效。卡图排序结果不会设置 `identity_verified` 或 `detail_selected`，提交删换牌前仍须核对选中卡名。

## 验证

- 修复前复现两处同根因失败：`test_matching_frame_allows_ranking_not_submission` 和 `test_previous_frame_trust_does_not_survive_failed_match`。
- 修复后 `test_deck_art`、`test_deck_art_audit`、`test_deck_scan`、`test_verified_removal`、`test_delete_fast_recognition` 共 87 项测试、54 项子测试通过。
- 更新两条仍模拟旧识别入口的集成测试，验证当前方形卡图路由和选中详情处理；没有恢复旧路由。
- Python 编译与 diff 空白检查通过。
- 扩展检查发现 `test_removal_modes.py` 仍导入已不存在的 `strict_removal_mode`，无法收集。它不属于本次接口修复，因此未声称全套测试通过。

本修复没有训练模型、调整识别门槛或修改个人规则。没有进行游戏现场识别准确率验证或新一轮培育长跑。

独立本地修复目录和 ZIP 名称带 `v2.0.2-deck-fix`。原 v2.0.2 目录、ZIP 与 GitHub Release 保留原状。


## 后续清理

本页记录的旧接口补丁已被后续清理取代。旧匹配器及兼容分支已移除，诊断与测试转向当前方形卡图流程。见 `legacy-cleanup-20261015.md`。旧修复包仅留作回退记录。
