# 发布渠道

版本号使用 `2.0.3`，Git 标签使用 `v2.0.3`。预发布状态由 GitHub Release 的 **Pre-release** 标记表达，版本号和 ZIP 名称不添加 `pre` 后缀。

| 渠道 | Git 分支 | 本机运行目录 | 更新来源 |
| --- | --- | --- | --- |
| 正式版 | `main` | `D:\Codex\MaaGakumasuEX-latest` | GitHub Latest，稳定渠道 |
| 预发布 | `pre` | `D:\Codex\MaaGakumasuEX-pre` | GitHub Pre-release，公测渠道 |

两个目录各自保存 `config/` 和 `appsettings.json`，不能使用共享配置链接。开发、修复与验证在 pre 中进行；预发布期间不覆盖 latest，不推送 main，不改变 GitHub Latest。

## 发布预发布版

1. 在 `pre` 分支提交源码、版本与发布说明，执行适用检查。
2. 使用 `tools/build_ex_release.py --prerelease` 构建到全新的临时目录，生成公开默认配置、ZIP、SHA-256 和逐文件清单。依赖基线使用 pre；目标不能是现存的 latest 或 pre。
3. 校验公开 ZIP 后，将构建结果用于本机 pre，迁移原 pre 的个人配置并保留新版公共卡库、pip 版本元数据。清理已替换的临时目录与旧预发布 ZIP。
4. 仅推送 `pre` 和版本标签到 `ex`（Quiter53/MaaGakumasuEX）。创建草稿 Release，设置 `prerelease=true`、`make_latest=false`，上传 ZIP 和 SHA-256，核对远端摘要后公开。
5. 确认 GitHub Latest、远端 main 和本机 latest 均仍为原正式版。

## 提升为正式版

只有用户明确要求转正式版时执行。确认候选源码和验证结果，更新 main，将对应 Release 改为正式版并设为 Latest；以同一份已验证代码构建稳定渠道安装包，更新发布资产及校验值。最后覆盖本机 latest 的程序与公共资源，保留 latest 自己的实例、规则、名单、饮料及界面配置，并维持稳定更新渠道。

源码历史使用 Git 管理。本机仅长期保留 latest、pre 以及各自对应发行 ZIP 和校验文件，不再积累整目录备份。

## Portable 内容

发行包只复制明确的运行依赖、agent、资源、数据与默认配置。开发文档、修复记录、README、诊断、测试、构建脚本、提示词、个人任务实例和日志不进入 ZIP；用户界面实际使用的公告和许可证保留。逐文件校验清单作为打包旁产物，留在构建诊断目录。

原生 Core 放在 `libs/MFAAvalonia.Core.dll`，语言卫星程序集放在 `libs/locales/<语言>/`，沿用 NetBeauty 加载布局，不在根目录保留重复 DLL。任务翻译仍由 `interface.json` 指向 `lang/`。Python 包中的空 `__init__.py`、类型标记等按依赖原样保留，不按文件大小删除。

2026-10-17 用户授权将当前 pre 的卡牌名单、饮料策略、卡牌规则（含各职业预设）作为发行默认值，已同步到 `assets/config/`。这里只发布这三份策略配置，诊断开关默认关闭；控制器、通知地址、账号、回忆进度与任务实例不作为默认值发布。
