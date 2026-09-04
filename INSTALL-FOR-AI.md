# ZCode 专用 AI 自适配 Bootstrap 安装协议

本协议供 ZCode 中的 AI 执行。用户可以只提供仓库 URL，本地无需预先存在仓库。目标版本固定为 `v1.0.3`，仓库固定为 `https://github.com/tony-apan/zcode_skills`。

> 三种模式互斥：插件模式、AI 自适配模式、脚本手工模式只能选一种。本协议只执行 **AI 自适配**；禁止同时安装插件，禁止直接复制或覆盖 `agents/*.md`。

## 全程安全边界

- 不使用 `curl | sh`、`wget | sh` 或任何下载后直接交给 shell 的远程脚本。
- 不覆盖、删除或复用未经核验的目录；不从 `main` 或其他浮动分支安装。
- 不直接使用 Read、cat、编辑器或其他工具读取 ZCode config 原文，也不要求用户粘贴配置。
- 不输出、复制、备份或写入 provider name、`options`、API key、token、secret、Authorization、baseURL 或未知字段。
- 不手工按字符串位置插入 frontmatter；所有 agent 写入都交给 `scripts/manage.py`。
- 任何检查或命令失败时立即停止，保留现场，原样报告命令、退出状态和 stderr；不得绕过失败继续。

## 阶段 0：环境预检

在写入前逐项确认并记录：

1. 当前客户端确为 ZCode，版本 >= 3.10.2。
2. ZCode 至少配置一个可用模型/provider；此处只确认“存在”，不得读取配置原文。
3. 当前 OS 是 macOS、Linux 或 Windows，并且后续只执行对应分支。
4. Git 可执行。
5. Python >= 3.9；Windows 另需 PowerShell >= 5.1。
6. 请求 tag 是固定的 `v1.0.3`，不是分支或其他版本。

Git、Python 或其他前提不满足时停止并报告。可用命令如下。

macOS / Linux：

```sh
git --version
python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3]))); raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'
```

Windows PowerShell：

```powershell
git --version
$PSVersionTable.PSVersion
py -3 -c "import sys; print('.'.join(map(str, sys.version_info[:3]))); raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
```

若 Windows 没有 `py` launcher，可尝试 `python` 执行同一 Python 检查；两者都不可用则停止。

## 阶段 1：获取并核验固定版本

如果已有仓库副本，只有同时满足以下条件才可复用：remote URL 与本协议仓库一致、工作树没有影响本次协议的未提交修改、当前 HEAD 精确位于 `v1.0.3`。任一条件不满足，就 clone 到**唯一的新临时目录**；不得 checkout、覆盖或删除原目录。

macOS / Linux：

```sh
WORKTREE=$(mktemp -d "${TMPDIR:-/tmp}/tony-agents-pack-v1.0.3.XXXXXX")
git clone --branch v1.0.3 --single-branch --depth 1 https://github.com/tony-apan/zcode_skills "$WORKTREE"
git -C "$WORKTREE" describe --tags --exact-match HEAD
git -C "$WORKTREE" status --short
```

Windows PowerShell 5.1+：

```powershell
$Worktree = Join-Path $env:TEMP ("tony-agents-pack-v1.0.3-" + [guid]::NewGuid().ToString("N"))
git clone --branch v1.0.3 --single-branch --depth 1 https://github.com/tony-apan/zcode_skills $Worktree
git -C $Worktree describe --tags --exact-match HEAD
git -C $Worktree status --short
```

核验输出必须精确包含 `v1.0.3`，且新 clone 应为干净工作树。clone 后把工作目录切换到该仓库根目录，**重新读取此 `INSTALL-FOR-AI.md`**，以 clone 内协议为唯一后续依据。

## 阶段 2：生成脱敏模型映射

本阶段只能通过 `scripts/model_inventory.py` 接触模型配置。严禁直接 Read/cat ZCode config；AI 只能读取下面 helper 生成的脱敏 JSON。helper 失败时停止，不得改用直接读取配置的方式。

macOS / Linux：

```sh
python3 scripts/model_inventory.py > /tmp/tony-agents-model-inventory.json
```

Windows PowerShell：

```powershell
$Inventory = Join-Path $env:TEMP 'tony-agents-model-inventory.json'
py -3 scripts/model_inventory.py > $Inventory
```

helper 的白名单仅包括 provider ID、provider `enabled`、model name、`limit.context`、`modalities.input` 和 `reasoning.variants`。读取脱敏 inventory 和每个 `agents/*.md` 顶部的 `# 模型需求：`，按以下顺序分配：

1. 硬能力：图像岗位只能选支持 image 输入的模型；长上下文岗位比较 `limit.context`；provider disabled 时不可选。
2. 任务适配：编码、写作、分析推理、轻量归纳按模型已知能力匹配。
3. 成本：满足前两项后再考虑资源开销。
4. 只有模型的 `reasoning.variants` 明确包含目标值时才写 `thoughtLevel`，优先 `high`。
5. 无满足硬能力的模型时选择可用降级方案，并记录原因供最终报告。

只把 agent 名、`model` 和可选 `thoughtLevel` 写入临时 model-map。格式：

```json
{
  "agent-name": {
    "model": "custom:provider-id:model-name",
    "thoughtLevel": "high"
  }
}
```

macOS/Linux 写入 `/tmp/tony-agents-model-map.json`；Windows 写入 `$env:TEMP\tony-agents-model-map.json`。不得把 inventory、分配理由或其他配置字段写进 map。无法确认模型 ID 格式时停止并向用户确认，不猜测。

## 阶段 3：选择 install 或 update

默认目标目录由管理器决定。项目级目标只有在用户明确要求时使用，并确保每条命令都传相同的 `--target-dir`。

先判断目标是否存在 `.tony-agents-pack/state.json`：

- 不存在 state：按“首次安装”执行。
- 存在 state：**不得执行 install**。只读取 state 中无 secrets 的 `package` 与 `version`。
- `package` 不是 `tony-agents-pack`：停止并报告，不接管该 state。
- `version` 已是 `1.0.3`：同版本不重复安装或更新，停止写入并报告。
- `version` 不是 `1.0.3` 且用户意图是安装/升级：改走“更新流程”。

### 首次安装

必须严格按 validate、install dry-run、检查、正式 install 的顺序。

macOS / Linux：

```sh
python3 scripts/manage.py validate
python3 scripts/manage.py install --dry-run --model-map /tmp/tony-agents-model-map.json
python3 scripts/manage.py install --model-map /tmp/tony-agents-model-map.json
```

Windows PowerShell：

```powershell
$ModelMap = Join-Path $env:TEMP 'tony-agents-model-map.json'
py -3 scripts/manage.py validate
py -3 scripts/manage.py install --dry-run --model-map $ModelMap
py -3 scripts/manage.py install --model-map $ModelMap
```

正式安装前检查 dry-run 报告的目标目录、17 个 agent 和同名冲突。冲突允许继续的前提是管理器明确报告会先备份；不得绕过管理器覆盖。

### 更新流程

更新必须使用阶段 1 已核验的固定 tag `v1.0.3` 仓库。默认保留当前有效 model/thoughtLevel；只有用户明确要求“重新分配模型”时，才执行阶段 2 并在 update 中传 `--model-map`。

不重新分配，macOS / Linux：

```sh
python3 scripts/manage.py validate
python3 scripts/manage.py update --dry-run
python3 scripts/manage.py update
```

不重新分配，Windows PowerShell：

```powershell
py -3 scripts/manage.py validate
py -3 scripts/manage.py update --dry-run
py -3 scripts/manage.py update
```

重新分配时，在当前平台的两条 update 命令后增加 `--model-map` 及阶段 2 对应路径。检查 dry-run 的新增/移除项、本地修改和冲突风险后才正式更新。

管理器会对本地修改进行三方合并。冲突或 Git merge 不可用时保留原文件，并生成 `*.tony-agents-pack.incoming`；不得用 incoming 自动覆盖。新增 agent 的同名目标会先备份；移除 agent 只会恢复或删除 SHA 未变化的文件。正式更新前会创建覆盖新旧集合的 snapshot。

## 阶段 4：完成报告

报告必须包含：

- 模式（AI 自适配）、OS、仓库 tag、目标目录和最终 package version
- 17 个 agent 各自绑定的模型
- 所有能力降级及原因
- dry-run 冲突、本地修改、incoming/restore 候选和保留项
- state 路径与本次最近 snapshot 路径；若同版本未写入，明确说明未创建新 snapshot
- 成功时提醒用户新建 ZCode 会话生效

不得报告 inventory 白名单以外的字段或值。失败时不写“完成”，而是原样报告失败命令、退出状态、stderr 和已执行到的阶段。

## 独立更新入口

用户直接提出更新时，仍执行阶段 0 和阶段 1，然后读取目标 state 的无 secrets `package/version`。同为 `1.0.3` 时不重复；旧版本按阶段 3 的更新流程执行。除非用户明确要求重新分配，否则禁止运行 inventory，且 update 不传 model-map。必须报告 incoming 和本地修改保护结果。

## 卸载流程

卸载也使用阶段 0、阶段 1 核验过的 `v1.0.3` 仓库。确认目标 state 的 `package` 是 `tony-agents-pack` 后，先 dry-run 并解释计划，再正式执行。

macOS / Linux：

```sh
python3 scripts/manage.py uninstall --dry-run
python3 scripts/manage.py uninstall
```

Windows PowerShell：

```powershell
py -3 scripts/manage.py uninstall --dry-run
py -3 scripts/manage.py uninstall
```

卸载只删除 SHA 与 state 一致的包文件；安装前存在的同名文件会从 backup 恢复。用户修改的文件不得删除，管理器会保留并可能生成 `*.tony-agents-pack.restore` 候选。最终报告删除项、恢复项、保留项、restore 候选、剩余 state（如有）和卸载前 snapshot 路径。
