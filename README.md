# tony-agents-pack：ZCode 专用智能体包

这是仅面向 ZCode 的 17 个流水线子智能体包，覆盖工程生产、内容与获客、专项审查、运行验证和终审收口。发布版不绑定模型，可作为 ZCode 插件使用，也可通过跨平台安装器安全注入本地模型映射；不声明兼容其他 AI 客户端。

仓库：https://github.com/tony-apan/zcode_skills

## 兼容性

| 项目 | 支持状态 | 说明 |
|---|---|---|
| ZCode 3.10.2 | 已验证 | 最低支持版本；不声明其他客户端兼容性 |
| macOS | 本机实际验证 | Python 安装器、agent 配置和 shell wrapper 已在本机验证 |
| Windows | 设计兼容，待首次 CI 证明 | Python 核心安装器支持 Windows，提供 PowerShell wrapper；CI 配置覆盖 Windows，发布 tag 前以 GitHub Actions 结果为准 |
| Linux | 脚本与 CI 兼容 | Python 测试和 CI 可运行；不声明 ZCode 桌面客户端已验证 |

运行要求：Python >= 3.9。Windows wrapper 要求 PowerShell 5.1+。安装和无冲突更新不依赖 Git；只有三方合并本地修改、处理更新冲突以及获取版本时需要 Git。更新遇到本地修改且 Git 不可用时，安装器保留原文件并生成 `*.tony-agents-pack.incoming`。

## 三种安装模式

三种模式互斥，只选一种。不要把插件模式与脚本/AI 模式混装到同一环境。

| 模式 | 适用场景 | 模型配置 | 更新方式 |
|---|---|---|---|
| 插件模式 | 最快启用 | 零配置，跟随默认模型；能力可能降级 | ZCode 插件管理 |
| AI 自适配 | 推荐，模型较多 | AI 按能力白名单生成 model-map | `manage.py update` |
| 脚本模式 | 明确知道模型 ID | 手写 model-map，或不绑定模型 | `manage.py update` |

## 安装

macOS / Linux（sh）：

```sh
git clone --branch v1.0.0 --depth 1 https://github.com/tony-apan/zcode_skills.git
cd zcode_skills
python3 scripts/manage.py validate
./scripts/install.sh --dry-run
./scripts/install.sh
```

Windows（PowerShell 5.1+）：

```powershell
git clone --branch v1.0.0 --depth 1 https://github.com/tony-apan/zcode_skills.git
Set-Location zcode_skills
py -3 scripts/manage.py validate
./scripts/install.ps1 --dry-run
./scripts/install.ps1
```

Windows 没有 Python Launcher（`py`）时，将文档中的 `py -3` 替换为 `python`；`install.ps1` 也会自动回退到 `python`。默认目标目录由安装器按当前系统处理，无需在 PowerShell 中使用 `~`。

项目级或测试安装：

```sh
# macOS / Linux
python3 scripts/manage.py install --dry-run --target-dir .zcode/agents
python3 scripts/manage.py install --target-dir .zcode/agents
```

```powershell
# Windows
py -3 scripts/manage.py install --dry-run --target-dir .zcode/agents
py -3 scripts/manage.py install --target-dir .zcode/agents
```

## AI 自适配

把仓库交给 ZCode 会话并说：

```text
严格按 INSTALL-FOR-AI.md 生成模型映射并安装；三种模式只选 AI 自适配，不混装，并且只执行当前操作系统对应的命令分支。
```

AI 先调用本地脱敏 helper，只读取生成的脱敏 JSON，再生成 model-map。macOS / Linux（sh）：

```sh
python3 scripts/model_inventory.py > /tmp/tony-agents-model-inventory.json
```

Windows（PowerShell）：

```powershell
$Inventory = Join-Path $env:TEMP 'tony-agents-model-inventory.json'
py -3 scripts/model_inventory.py > $Inventory
```

完整的平台命令和安全边界见 [INSTALL-FOR-AI.md](INSTALL-FOR-AI.md)。model-map 格式：

```json
{
  "coder": {
    "model": "custom:provider-id:model-name",
    "thoughtLevel": "high"
  },
  "writer": {
    "model": "custom:provider-id:model-name"
  }
}
```

`thoughtLevel` 可省略。安装器会将字段作为 frontmatter 顶层键写在 closing `---` 前，并在写入后重新解析验证。

## 插件模式

在 ZCode 的插件管理中添加：

```text
https://github.com/tony-apan/zcode_skills
```

插件模式零配置，但没有逐 agent 模型映射。依赖图像、长上下文或强推理的岗位可能随默认模型能力降级；需要确定性能力时使用 AI 自适配模式，不要在插件安装之上再脚本安装。

## 调用示例

在 ZCode 中按任务点名 agent，例如：

```text
请让 coder 实现这个修复，再让 shencha 做静态审查、verifier 复跑测试，最后交 shencha-final 收口。
```

```text
请让 seoer 先产出选题 brief，再交 writer 成稿，最后由 shencha-content 验收。
```

## 更新

始终固定到明确 tag，不从浮动分支直接覆盖本地安装。macOS / Linux（sh）：

```sh
VERSION=v1.1.0
git fetch --tags origin
git checkout --detach "$VERSION"
python3 scripts/manage.py validate
python3 scripts/manage.py update --dry-run
python3 scripts/manage.py update
```

Windows（PowerShell）：

```powershell
$Version = 'v1.1.0'
git fetch --tags origin
git checkout --detach $Version
py -3 scripts/manage.py validate
py -3 scripts/manage.py update --dry-run
py -3 scripts/manage.py update
```

带模型覆盖的更新：

```sh
# macOS / Linux
python3 scripts/manage.py update --dry-run --model-map /tmp/tony-agents-model-map.json
python3 scripts/manage.py update --model-map /tmp/tony-agents-model-map.json
```

```powershell
# Windows
$ModelMap = Join-Path $env:TEMP 'tony-agents-model-map.json'
py -3 scripts/manage.py update --dry-run --model-map $ModelMap
py -3 scripts/manage.py update --model-map $ModelMap
```

目标文件与上次安装 SHA 一致时直接更新。检测到用户修改时，安装器用上一版 base、当前 local、新版 remote 执行三方合并。agent 不在 model-map 时保留当前有效的 `model`/`thoughtLevel`；agent 在 model-map 时使用映射中的 `model`，只有映射显式提供 `thoughtLevel` 才写入，否则删除旧值。无冲突才写回；有冲突或 `git merge-file` 不可用时保留原文件，并生成 incoming 候选。

新版新增 agent 时按初装规则安装，同名目标先备份；新版移除 agent 时只恢复或删除未被用户修改的文件，已修改或缺失的文件会保留并继续在 state 中跟踪。所有集合变化都发生在操作前快照之后。

## 备份与回滚

每次正式 install/update/uninstall 前都会创建快照；初装遇同名文件还会先单独备份。状态和数据位于 `TARGET/.tony-agents-pack/` 下的 `state.json`、`backups/`、`bases/` 和 `snapshots/`。

预览并回滚最近快照：

```sh
# macOS / Linux
python3 scripts/manage.py rollback latest --dry-run
python3 scripts/manage.py rollback latest
```

```powershell
# Windows
py -3 scripts/manage.py rollback latest --dry-run
py -3 scripts/manage.py rollback latest
```

如需按 ID 恢复旧快照，从目标目录下 `.tony-agents-pack/snapshots/` 读取实际目录名，作为 `rollback` 的位置参数传入，不要猜测快照 ID。项目级安装的后续命令必须传相同的 `--target-dir`：

```sh
# macOS / Linux
python3 scripts/manage.py rollback latest --target-dir .zcode/agents
```

```powershell
# Windows
py -3 scripts/manage.py rollback latest --target-dir .zcode/agents
```

## 卸载

macOS / Linux（sh）：

```sh
python3 scripts/manage.py uninstall --dry-run
python3 scripts/manage.py uninstall
```

Windows（PowerShell）：

```powershell
py -3 scripts/manage.py uninstall --dry-run
py -3 scripts/manage.py uninstall
```

只有当前 SHA 等于状态中 `installed_sha` 的包文件才会删除或恢复。安装前已存在的同名文件会从备份恢复；当前文件被用户修改时会保留，并生成恢复候选或报告。

## 常见问题

### 安装后看不到智能体

智能体列表通常在新会话加载。关闭当前会话并新建会话；仍未出现时重新运行当前平台对应的 `manage.py validate`，确认目标目录及 `--target-dir` 参数一致。

### 提示 `package state already exists`

这表示本包已经由脚本安装，不要重复执行 `install`；按“更新”章节先 dry-run 再更新。

### 更新生成 `.tony-agents-pack.incoming`

这表示本地修改与新版契约冲突，或系统没有可用的 Git。安装器已保留原文件；对比 incoming 候选并手工合并后，再运行当前平台对应的 `update --dry-run`。不要直接删除状态目录。

### 安装或更新中途失败

正式操作在写入前创建快照；中途异常会自动恢复本次操作前的文件和 state。若同时报告自动回滚失败，保留现场，按“备份与回滚”章节先预览最近快照。

## 安全与隐私

- 安装器只使用 Python 3 标准库，所有写入采用同目录临时文件加 `os.replace`
- 不读取、输出或备份 ZCode 的 `options`、API key、token、secret、Authorization、baseURL
- `scripts/model_inventory.py` 在本机进程内读取 ZCode 配置，stdout 只输出 provider ID/enabled、模型名、context、输入模态和 reasoning variants
- AI 不得直接读取配置原文，只能读取 helper 生成的脱敏 JSON；模型映射必须通过 `--model-map` 显式传入
- 正式覆盖前有快照；同名文件初装前有备份；本地修改更新冲突时不覆盖
- 仓库不提供 `curl | sh` 安装方式

## 版本策略

遵循 SemVer：MAJOR 用于破坏性契约变更，MINOR 用于向后兼容地新增 agent 或能力，PATCH 用于不改契约的修正。变更记录见 [CHANGELOG.md](CHANGELOG.md)。

## 发布者流程

首次发布前，三平台 CI 只能表述为“配置覆盖”；必须以首次 push 后 GitHub Actions 的实际结果为发布依据。

macOS / Linux 发布者可运行 `./scripts/release.sh`。Windows 发布者在提交全部发布改动后使用：

```powershell
py -3 scripts/manage.py validate
py -3 -m unittest discover -s tests -p 'test_*.py' -v
$Version = py -3 -c "import json; print(json.load(open('.zcode-plugin/plugin.json', encoding='utf-8'))['version'])"
git diff --quiet
git diff --cached --quiet
if (git status --porcelain) { throw 'Working tree is not clean' }
git rev-parse -q --verify "refs/tags/v$Version" 2>$null
if ($LASTEXITCODE -eq 0) { throw "Tag already exists: v$Version" }
git tag -a "v$Version" -m "tony-agents-pack v$Version"
```

创建 tag 后不自动 push。发布前确认 changelog 包含该版本，并以 GitHub Actions 三平台结果为准。

## LICENSE

MIT License，Copyright (c) 2026 Tony (GitHub: tony-apan)。见 [LICENSE](LICENSE)。
