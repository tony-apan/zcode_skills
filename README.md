# ZCode 专用：tony-agents-pack

[![ZCode](https://img.shields.io/badge/ZCode-%3E%3D%203.10.2-111827)](https://github.com/tony-apan/zcode_skills) [![Agents](https://img.shields.io/badge/agents-17-0f766e)](https://github.com/tony-apan/zcode_skills/tree/v1.0.3/agents) [![Version](https://img.shields.io/badge/version-v1.0.3-b45309)](https://github.com/tony-apan/zcode_skills/releases/tag/v1.0.3)

把仓库链接发给 ZCode 里的 AI，它会根据本地已配置的模型自动分配并安全安装 17 个智能体。

> **使用前提：必须先安装 ZCode。** 这不是独立软件，也不能直接在 ChatGPT 或 Claude 网页中使用。请从 ZCode 官方渠道安装，并完成首次启动；本文不提供未经可靠确认的官方 URL。

## 安装前提

- ZCode >= 3.10.2，并且至少配置一个可用模型/provider
- Git
- Python >= 3.9
- Windows 另需 PowerShell 5.1+

AI 会先确认 ZCode、操作系统、Git、Python 和目标 tag。Git 或 Python 不满足时会停止并报告，不会继续安装。

## 3 步自动安装

1. 在 ZCode 中打开一个新会话。
2. 复制下面**唯一主提示词**，完整发送，无需修改任何变量。
3. 等 AI 汇报完成后，再新建一个会话，让 17 个智能体生效。

```text
请在 ZCode 中自动安装这个智能体包。repo=https://github.com/tony-apan/zcode_skills，tag=v1.0.3。严格执行以下要求：
1. 先确认当前客户端是 ZCode 且版本 >=3.10.2，识别 OS，并确认 ZCode 至少有一个可用模型/provider；检查 Git、Python >=3.9，Windows 还要 PowerShell >=5.1。任何前提不满足或无法确认都停止，并原样报告检查结果。
2. 只选“AI 自适配”模式，禁止同时使用插件或手工模式。按固定 tag v1.0.3 将 repo clone 到唯一的新临时目录：Windows 使用 $env:TEMP 下的 GUID 目录，macOS/Linux 使用 mktemp 创建的目录。不得覆盖或删除任何已有目录，不得从 main 等浮动分支安装，不得使用 curl|sh 或其他远程脚本管道。
3. clone 成功并核验当前 HEAD 确属 v1.0.3 后，重新读取 clone 内的 INSTALL-FOR-AI.md，并严格按其阶段执行；不得依赖聊天中转述的协议。
4. 模型适配阶段只能运行 scripts/model_inventory.py 生成脱敏 inventory；不得直接 Read/cat/输出 ZCode config 原文。AI 只能读取 helper 生成的脱敏 JSON，据此为 17 个岗位生成临时 model-map。不得泄露密钥、token、options、Authorization、baseURL 或未知字段。
5. 依次运行 scripts/manage.py validate、带 --model-map 的 install --dry-run。检查 dry-run 的目标、冲突和备份计划后，才运行正式 install。若目标已有 .tony-agents-pack/state.json，不得执行 install：只读取 state 中无 secrets 的 package/version 判断；同版本不重复操作，版本不同时按协议和用户安装意图改走 update。
6. 不手工复制 agents，不按字符串位置改 frontmatter，不无备份覆盖。任一步失败立即停止，保留现场并原样报告失败命令和错误，不自行绕过。
7. 完成后汇报：AI 自适配模式、OS、目标目录、17 个岗位各自模型、所有降级项、冲突或 incoming/restore 候选、state 路径、最近 snapshot 路径，并提醒我新建会话生效。不得汇报 inventory 白名单以外的配置值。
```

自动安装是唯一推荐入口。完整安全协议见 [INSTALL-FOR-AI.md](INSTALL-FOR-AI.md)。

## 17 个岗位

| 流水线 | 智能体 | 用途 |
|---|---|---|
| 工程 | `coder`、`coder-gpt`、`coder-ds`、`coder-kimi`、`frontend` | 并行实现、方案对比、前端交付 |
| 内容 | `writer`、`writer-pro` | 内容生产与增强稿 |
| SEO / 外贸 | `seoer`、`huoke`、`outreach`、`jiankong` | 选题、获客、开发信、内容监控 |
| 核查 | `shencha`、`shencha-content`、`shencha-ui`、`verifier`、`shencha-final`、`tijian` | 静态审查、运行验证、终审、网站体检 |

## 开始使用

代码任务：

```text
请让 coder 实现这个修复，再让 shencha 做静态工程审查、verifier 做运行验证，最后交 shencha-final 终审收口。
```

SEO 内容：

```text
请让 seoer 基于搜索证据产出选题 brief，交 writer 成稿，再由 shencha-content 验收事实、搜索意图和转化路径。
```

外贸开发：

```text
请让 huoke 筛选并核验目标客户，交 outreach 生成适合目标市场的开发触达内容，并明确证据等级与合规限制。
```

## 更新

在 ZCode 新会话中发送：

```text
请安全更新这个 ZCode 智能体包。repo=https://github.com/tony-apan/zcode_skills，tag=v1.0.3。先确认 ZCode/OS/Git/Python 前提；检查目标 .tony-agents-pack/state.json 中无 secrets 的 package/version，同为 1.0.3 时不要重复更新并直接报告。否则把固定 tag v1.0.3 clone 到唯一新临时目录，核验 tag 后读取其中 INSTALL-FOR-AI.md 的“更新流程”。依次运行 validate、update --dry-run，说明本地修改、备份计划和 incoming 风险，确认 dry-run 无异常后再正式 update。必须保护本地修改，冲突时保留原文件并报告 incoming；不得从浮动分支覆盖，不得直接读取 ZCode config，不得手工复制 agents。只有我明确要求“重新分配模型”时才运行脱敏 inventory 并生成新的 model-map。完成后报告版本、目标、冲突、state、snapshot 和新会话生效；失败立即停止并原样报告。
```

## 卸载

在 ZCode 新会话中发送：

```text
请安全卸载这个 ZCode 智能体包。使用 repo=https://github.com/tony-apan/zcode_skills 的固定 tag v1.0.3 仓库；如本地没有已核验为该 tag 的副本，就 clone 到唯一新临时目录，禁止覆盖已有目录。读取 clone 内 INSTALL-FOR-AI.md 的“卸载流程”，确认目标 state 属于 tony-agents-pack 后，先运行 uninstall --dry-run，向我解释将删除、恢复、保留的文件和快照计划；检查无误后正式 uninstall。不得删除用户修改，必须报告保留项、*.tony-agents-pack.restore 候选和 snapshot 路径。任一步失败立即停止并原样报告。
```

<details>
<summary><strong>其他安装模式（高级；与 AI 自适配互斥）</strong></summary>

三种模式只能选一种，禁止混装：

| 模式 | 模型配置 | 更新方式 |
|---|---|---|
| AI 自适配（推荐） | AI 读取脱敏 inventory 后分配 | `manage.py update` |
| 插件模式 | 跟随默认模型，部分岗位可能降级 | ZCode 插件管理 |
| 手工脚本模式 | 用户明确提供 model-map，或不绑定模型 | `manage.py update` |

插件模式可在 ZCode 插件管理中添加：

```text
https://github.com/tony-apan/zcode_skills
```

手工脚本模式必须先自行准备 model-map，再固定版本操作。macOS / Linux：

```sh
git clone --branch v1.0.3 --single-branch --depth 1 https://github.com/tony-apan/zcode_skills.git
cd zcode_skills
python3 scripts/manage.py validate
python3 scripts/manage.py install --dry-run --model-map /tmp/tony-agents-model-map.json
python3 scripts/manage.py install --model-map /tmp/tony-agents-model-map.json
```

Windows PowerShell 5.1+：

```powershell
git clone --branch v1.0.3 --single-branch --depth 1 https://github.com/tony-apan/zcode_skills.git
Set-Location zcode_skills
py -3 scripts/manage.py validate
$ModelMap = Join-Path $env:TEMP 'tony-agents-model-map.json'
py -3 scripts/manage.py install --dry-run --model-map $ModelMap
py -3 scripts/manage.py install --model-map $ModelMap
```

Windows 没有 Python Launcher 时，把 `py -3` 替换为 `python`。项目级安装可在每条 install/update/uninstall/rollback 命令后传相同的 `--target-dir .zcode/agents`。

</details>

<details>
<summary><strong>兼容矩阵</strong></summary>

| 项目 | 状态 | 说明 |
|---|---|---|
| ZCode >= 3.10.2 | 最低要求 | 不声明兼容其他 AI 客户端 |
| macOS | 已实际验证 | Python 管理器、agent 配置、shell wrapper |
| Windows | GitHub Actions 已验证 | `windows-latest`、Python 3.9、PowerShell 安装器 dry-run 已通过；每个新 tag 仍以对应 Actions 结果为准 |
| Linux | 脚本与 CI 已验证 | Ubuntu 上 Python 3.9 测试与 shell wrapper 已通过；不声明 ZCode 桌面客户端已实际验证 |

</details>

<details>
<summary><strong>安全、备份与回滚</strong></summary>

- 安装器只使用 Python 3 标准库，写入使用同目录临时文件与原子替换。
- `scripts/model_inventory.py` 在本机读取 ZCode 配置，但只输出 provider ID/enabled、模型名、context、输入模态、reasoning variants。
- AI 不得直接读取配置原文，只能读取 `/tmp/tony-agents-model-inventory.json` 或 Windows `%TEMP%` 中对应的脱敏 JSON。
- 不读取、输出或备份 `options`、API key、token、secret、Authorization、baseURL 或未知字段。
- 正式 install/update/uninstall 前创建 snapshot；同名文件初装前另有 backup；本地修改冲突不会被覆盖。
- 仓库不提供 `curl | sh`，也不允许手工字符串复制覆盖 agent。

状态数据位于目标目录的 `.tony-agents-pack/`，包括 `state.json`、`backups/`、`bases/` 和 `snapshots/`。预览并回滚最近一次快照：

```sh
python3 scripts/manage.py rollback latest --dry-run
python3 scripts/manage.py rollback latest
```

Windows 将 `python3` 换为 `py -3`。按 ID 回滚时，应读取 snapshots 下真实目录名，不猜测 ID。

</details>

<details>
<summary><strong>故障排查</strong></summary>

**安装后看不到智能体**：智能体通常在新会话加载。新建会话；仍未出现时运行当前 tag 的 `scripts/manage.py validate`，并核对目标目录。

**`package state already exists`**：已经由脚本管理，不要重复 install。检查 state 的 package/version；同版本停止，其他版本走 update。

**出现 `.tony-agents-pack.incoming`**：本地修改与新版冲突，或 Git 无法执行三方合并。原文件仍保留；对比候选后人工处理，不要删除 state。

**操作中途失败**：正式操作会先创建 snapshot，并尝试自动回滚。若自动回滚也失败，保留现场，先执行 `rollback latest --dry-run` 预览。

</details>

<details>
<summary><strong>版本与发布流程</strong></summary>

本包遵循 SemVer。MAJOR 表示破坏性契约变更，MINOR 表示向后兼容地新增 agent 或能力，PATCH 表示不改 agent 契约的修正。变更见 [CHANGELOG.md](CHANGELOG.md)。

发布者在 macOS/Linux 可运行 `./scripts/release.sh`。Windows 发布前运行：

```powershell
py -3 scripts/manage.py validate
py -3 -m unittest discover -s tests -p 'test_*.py' -v
py -3 -m py_compile scripts/manage.py scripts/model_inventory.py tests/test_manage.py tests/test_model_inventory.py
```

随后确认工作区干净、changelog 包含插件版本、目标 tag 不存在，再创建 annotated tag。脚本不会自动 push；每个发布版本都以 Ubuntu、macOS、Windows 对应的 GitHub Actions 结果为准。

</details>

## License

MIT License，Copyright (c) 2026 Tony (GitHub: tony-apan)。见 [LICENSE](LICENSE)。
