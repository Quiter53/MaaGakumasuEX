<!-- markdownlint-disable MD033 MD041 -->

# MaaGakumasu EX

《学園アイドルマスター》的自动化助手增强版，由 [Quiter53/MaaGakumasuEX](https://github.com/Quiter53/MaaGakumasuEX) 维护，基于 [MaaGakumasu](https://github.com/SuperWaterGod/MaaGakumasu)、[MaaFramework](https://github.com/MaaXYZ/MaaFramework) 和 [MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia)。保留日常与自动培育功能，加入 HIF 策略、逐卡出牌规则、职业预设、饮料管理和多目标回忆收集。

当前正式版为 **v2.0.2**，提供 **Windows x64** 便携包。界面使用原生 Avalonia，MaaFramework 固定为 **5.13.0**，运行包自带 Python 环境。EX 从 `2.0.0` 开始独立编号。

[下载最新正式版](https://github.com/Quiter53/MaaGakumasuEX/releases/latest) · [版本记录](https://github.com/Quiter53/MaaGakumasuEX/releases) · [问题反馈](https://github.com/Quiter53/MaaGakumasuEX/issues)

## 下载与使用

从本仓库 Releases 下载 `MaaGakumasuEX-v2.0.2-win-x64-portable.zip`，解压到独立目录，运行 `MaaGakumasu.exe`。发布说明附有安装包的 SHA-256；请下载完整 ZIP，GitHub 自动生成的 `Source code` 为源码包。

首次使用选择控制器并连接游戏，再检查任务列表中的培育难度、偶像、培育次数、体力药和道具选项。发布包包含公共卡牌与策略配置，设备连接、个人实例、日志和回忆收集进度由本地保存。

模拟器推荐使用 MuMu 12，竖屏游戏画面以 `720 × 1280`、`240 DPI` 为配置参考。DMM 使用 PrintWindow 截图；游戏以管理员身份运行时，助手也应使用相同权限。培育前建议在游戏设置中开启跳过对话与常见弹窗的选项。

当前 EX 发行包与维护重点为 Windows x64。其他分辨率、汉化组合及设备的完整流程覆盖仍有限；手机独立运行、macOS、Linux 和 Windows ARM 暂无 EX 正式包。

## 自动培育与日常

EX 保留日常任务，可领取活动费、邮箱与任务奖励、每周免费礼包，安排工作，执行竞赛、社团请求和商店兑换。偶像之路保留自动编队、战斗与失败重试功能。

培育任务包含初 `REGULAR / PRO / MASTER`、NIA `PRO / MASTER`，以及 HIF 的选拔和本战入口。HIF 本战可设置上课属性、幕间回血目标和试镜手动接管，配合卡牌名单与饮料策略处理商店、删换牌和战斗。

中断后，可在确认当前游戏场景与任务配置一致的情况下，使用“跳过准备阶段”继续。首次使用某个职业时，先检查卡牌名单、出牌规则和饮料设置，再运行一轮确认效果。

完整培育已有实机跑通记录。v2.0.0 调整了删牌后商店气泡的处理顺序，并改进回忆列表到底判断；这两项修复已通过离线回放与逻辑回归，尚未完成新一轮实机长跑。自动培育仍在持续完善，具体适配情况以当前任务选项及 [版本说明](docs/releases/v2.0.0.md) 为准。

## 出牌规则

左侧“出牌规则”页面提供 **好调、集中、干劲、好印象、强气、全力** 六种职业预设。每个预设分别保存规则和预览手牌；选择预设、修改卡片后，点击“保存规则”应用。实际出牌使用当前保存的预设，开始培育前请手动选好对应职业。

每张卡可以设置允许使用的剩余回合、排序档位，以及随剩余回合变化的优先级曲线。普通与强化版本共用同一张卡的规则；选牌窗口支持分类筛选和搜索，卡名使用游戏内原名。

例如，可以把 `届いて！` 设置为仅在剩余 1～2 回合使用，把 `せのびの魔法` 设置为仅在剩余 1～7 回合使用。这些是可自行调整的配置示例，实际限制以当前预设保存的内容为准。

出牌顺序依次比较：

1. **使用时机**：当前剩余回合是否处于允许范围。
2. **再动**：优先处理带再动的卡牌。
3. **排序档位**：默认采用卡牌稀有度，也可用滑块将 R、SR 卡提高到 SR 或 SSR 档位。
4. **同档优先级曲线**：再动状态和排序档位相同时，曲线分数高的卡先出。
5. **手牌位置**：完全同分时，按手牌槽位顺序出牌。

曲线支持线性过渡和阶梯变化，可拖动节点，也可输入“剩余回合 → 优先级”。分数范围为 `0～100`，未配置卡牌默认为 `50`。档位提升只影响助手排序，卡牌在游戏中的稀有度保持原样。

页面下方可配置 1～5 张预览手牌，调整剩余回合查看排序结果。“添加手牌”中提供已有规则卡牌的快捷入口，方便检查同一组牌在不同回合的先后顺序。

## 卡牌名单与饮料

“卡牌黑白名单”页面按预设管理黑名单、白名单和替换名单：黑名单用于规避获取，白名单用于优先获取，替换名单用于指定变卡时优先换出的牌。黑白名单互斥，替换名单独立；从卡牌库拖入名单后，当前预设的修改立即保存。

卡牌名单与出牌规则分别设置。前者决定获取、替换时的选择，后者决定战斗中已有手牌的使用时机和顺序；切换职业时请分别检查两个页面的当前预设。

饮料页面可指定目标饮料，并为每种饮料设置使用时机：**开局使用、指定卡牌出牌前使用、最后若干回合内使用**。获取、满包处理和战斗使用会读取对应设置；使用前仍会确认当前饮料与弹窗状态。

## 回忆收集

“培育模式”提供常规培育、刷取卡牌回忆和刷取卡组。选择收集模式后，通过目标选择界面设置所需卡牌及条件，可同时配置多个目标。

卡牌回忆按目标卡及强化、定制要求匹配；卡组模式还区分卡牌来源和组合要求。收集流程结合回忆详情与钻石词条识别记录进度，已有进度保存在本地，重新启动时继续读取。

回忆识别会先检查当前内容，再决定是否滚动。主卡标题已经出现但名称暂时读不清时，在原位置重试；词条扫描结合文字和位置判断列表是否到底，减少动态背景造成的重复下滑。

## 设置与更新

个人配置主要保存在运行目录的 `config/`，界面设置同时使用 `appsettings.json`。

| 文件或目录 | 内容 |
| --- | --- |
| `config/card_rules.json` | 出牌规则、当前预设与预览手牌 |
| `config/card_lists.json` | 卡牌黑白名单、替换名单及名单预设 |
| `config/drink_policy.json` | 目标饮料与使用时机 |
| `config/memory_farming_progress.json` | 回忆收集进度 |
| `config/instances/` | 任务实例及连接等实例配置 |
| `appsettings.json` | 客户端设置 |

设置 → 更新设置提供“检查更新”“更新完整包”和“重新下载完整包”，并可设置启动检查与自动更新。更新来源为 **Quiter53/MaaGakumasuEX 的 GitHub Releases**，界面、策略和资源随完整包更新，EX 使用自己的发行渠道。

升级前建议停止任务、完全退出助手，并备份 `config/` 和 `appsettings.json`。手动升级可先解压到新目录，再复制自己的配置；`config/card_library.json` 是随包更新的公共卡牌库，保留新版文件即可。内置更新会保留已存在的个人配置，公共卡牌库随新版更新。

检查更新出现 `403 / rate limit exceeded` 时，表示 GitHub API 请求受到限流，可稍后重试，或直接从 [Releases](https://github.com/Quiter53/MaaGakumasuEX/releases) 下载完整包。

“调试诊断”开关控制识别诊断日志与异常截图，默认关闭。排查漏认、误点或卡住时可临时开启，复现后保存相关时间段的日志和截图。

## 本地构建

需要 Git、Python 3.12+、.NET SDK 10，以及一份已验证的 EX Windows x64 运行包作为依赖基线。原生界面源码位于 `native-ui/`，Python 扩展位于 `agent/`，任务、图片和公共配置位于 `assets/`。

在仓库根目录构建原生界面：

```powershell
dotnet build native-ui/MFAAvalonia/MFAAvalonia.csproj -c Release -r win-x64
```

将已有 EX 包解压到 `D:\EX-runtime` 后，可用下面的命令生成新运行目录与 ZIP。`--output` 指定的目录及其同名 ZIP 应尚未存在。

```powershell
python tools/build_ex_release.py `
  --runtime "D:\EX-runtime" `
  --ui-core "native-ui/MFAAvalonia/bin/Release/net10.0/win-x64/MFAAvalonia.Core.dll" `
  --output "dist/MaaGakumasuEX-v2.0.2-win-x64-portable"
```

打包工具从依赖基线复制运行环境，装入当前仓库的界面、脚本和资源，生成公开默认配置，校验两端 MaaFramework DLL，并检查 ZIP 的 CRC 与逐文件哈希。该流程依赖已有运行包；源码构建与发布包仍需保持 MaaFramework **5.13.0** 一致。

发布记录与验证范围见 [v2.0.2 说明](docs/releases/v2.0.2.md) 与 [v2.0.0 说明](docs/releases/v2.0.0.md)。[原功能说明](docs/zh_cn/功能说明.md) 和 [开发相关](docs/zh_cn/开发相关.md) 保留上游背景；EX 的规则页面、更新方式及 HIF 状态以本页和当前源码为准。

## 问题反馈

请在 [本仓库 Issues](https://github.com/Quiter53/MaaGakumasuEX/issues) 提交问题，并附上 EX 版本、Windows 与模拟器或 DMM 环境、游戏语言、分辨率、任务配置，以及问题发生时间附近的日志和截图。卡牌或饮料识别问题请尽量保留完整游戏画面，方便核对识别区域。

日志通常位于 `logs/` 和 `debug/`。分享前请检查并隐去账号信息、连接地址及其他个人内容。详细记录方式可参考 [提问与反馈指南](docs/zh_cn/提问与反馈指南.md)，EX 问题统一在本仓库反馈。

## 免责声明

本项目仅用于学习交流，请勿用于商业用途。本项目仅提供自动化脚本，不提供任何游戏资源。

本软件开源、免费，仅供学习交流使用。若您遇到商家使用本软件进行代练并收费，可能是分发、设备或时间等费用，产生的费用、问题及后果与本软件无关。

**在使用过程中，MaaGakumasu 可能存在任何意想不到的问题，因 MaaGakumasu 自身漏洞、文本理解有歧义、异常操作导致的账号问题等开发组不承担任何责任，请在确保在阅读完用户手册、自行尝试运行效果后谨慎使用！**

## 许可证与鸣谢

本仓库保留上游的 [AGPL-3.0 许可证](LICENSE)；原生客户端与其他依赖的许可证随对应源码保留。

感谢 [SuperWaterGod/MaaGakumasu](https://github.com/SuperWaterGod/MaaGakumasu) 原作者与贡献者提供游戏自动化基础，感谢 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 提供识别与控制框架，感谢 [MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia) 提供原生客户端基础。

[![上游贡献者](https://contrib.rocks/image?repo=SuperWaterGod/MaaGakumasu&max=1000)](https://github.com/SuperWaterGod/MaaGakumasu/graphs/contributors)
