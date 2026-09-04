# ZCode 专用 AI 自适配安装协议

本协议仅面向在 ZCode 中执行安装的 AI。目标是只读取模型选择所需的最小字段，生成 model-map，再由 `scripts/manage.py` 完成可审计安装；不适用于其他客户端。插件模式、AI 自适配模式、脚本手工模式互斥，只选一种，不混装。AI 必须识别当前操作系统，并且只执行下文对应的平台分支。

## 安全边界

AI 严禁直接使用 Read、cat、编辑器或其他工具读取 ZCode 配置原文，也不得要求用户将配置原文粘贴到对话。必须先在仓库根目录执行本机脱敏 helper。

macOS / Linux（sh）：

```sh
python3 scripts/model_inventory.py > /tmp/tony-agents-model-inventory.json
```

Windows（PowerShell）：

```powershell
$Inventory = Join-Path $env:TEMP 'tony-agents-model-inventory.json'
py -3 scripts/model_inventory.py > $Inventory
```

Windows 没有 `py` launcher 时将 `py -3` 替换为 `python`。`scripts/model_inventory.py` 在本机 Python 进程内读取配置，但 stdout 只包含 provider ID、provider `enabled`、model name、`limit.context`、`modalities.input` 和 `reasoning.variants`。

AI 在 macOS/Linux 只能读取 `/tmp/tony-agents-model-inventory.json`，在 Windows 只能读取 `$env:TEMP\tony-agents-model-inventory.json`。绝不读取、输出、复制或备份配置原文及 provider name、`options`、API key、token、secret、Authorization、baseURL 或任意未知字段。

不要向回复、日志、model-map 或仓库写入配置文件原文。helper 失败时只报告其 stderr 错误并停止自动适配；不得绕过 helper。`scripts/manage.py` 不读取 ZCode 配置，只接收明确提供的 model-map。

## 模式选择

1. 插件模式：零配置，跟随默认模型，能力可能降级。
2. AI 自适配模式：按本协议生成 model-map，再调用安装器。推荐。
3. 脚本手工模式：用户自行提供 model-map，或不绑定模型。

选择 AI 自适配后，不再安装插件，也不手工复制 `agents/*.md`。

## 生成模型映射

读取当前平台的脱敏 inventory 文件和每个 `agents/*.md` 顶部的 `# 模型需求：` 标签。按硬能力、任务适配、成本顺序选择：

- “必须图像输入”只能选 `modalities.input` 包含 `image` 的模型；没有则报告降级
- “长上下文”依据 `limit.context`；不足则报告降级
- 编码、写作、分析推理、轻量归纳按模型已知能力匹配
- 只有选定模型的 `reasoning.variants` 包含目标值时才写 `thoughtLevel`，优先使用 `high`
- provider 被禁用时不选

model-map 临时路径：macOS/Linux 使用 `/tmp/tony-agents-model-map.json`；Windows 使用 PowerShell `$ModelMap = Join-Path $env:TEMP 'tony-agents-model-map.json'`。格式必须为：

```json
{
  "agent-name": {
    "model": "custom:provider-id:model-name",
    "thoughtLevel": "high"
  }
}
```

`thoughtLevel` 可省略。只写 agent 名、model 和可选 thoughtLevel，不写能力盘点、理由或任何配置字段。需要编码的 provider ID 按当前 ZCode 已验证格式处理；无法确认格式时先向用户确认，不猜测。

## 安装步骤

必须先 validate 和 dry-run。macOS / Linux（sh）：

```sh
python3 scripts/manage.py validate
python3 scripts/manage.py install --dry-run --model-map /tmp/tony-agents-model-map.json
python3 scripts/manage.py install --model-map /tmp/tony-agents-model-map.json
```

Windows（PowerShell）：

```powershell
$ModelMap = Join-Path $env:TEMP 'tony-agents-model-map.json'
py -3 scripts/manage.py validate
py -3 scripts/manage.py install --dry-run --model-map $ModelMap
py -3 scripts/manage.py install --model-map $ModelMap
```

项目级安装时，每条安装命令都增加同一个目标目录：

```sh
# macOS / Linux
python3 scripts/manage.py install --dry-run --model-map /tmp/tony-agents-model-map.json --target-dir .zcode/agents
python3 scripts/manage.py install --model-map /tmp/tony-agents-model-map.json --target-dir .zcode/agents
```

```powershell
# Windows
$ModelMap = Join-Path $env:TEMP 'tony-agents-model-map.json'
py -3 scripts/manage.py install --dry-run --model-map $ModelMap --target-dir .zcode/agents
py -3 scripts/manage.py install --model-map $ModelMap --target-dir .zcode/agents
```

 dry-run 出现同名冲突时，向用户报告目标路径和正式安装会先备份；不要绕过安装器覆盖。禁止手工按字符串位置插入字段，必须让安装器按首尾 `---` 边界解析并复验。

## 更新

固定到用户指定 tag，先 validate 和 dry-run，再更新。macOS / Linux（sh）：

```sh
python3 scripts/manage.py validate
python3 scripts/manage.py update --dry-run --model-map /tmp/tony-agents-model-map.json
python3 scripts/manage.py update --model-map /tmp/tony-agents-model-map.json
```

Windows（PowerShell）：

```powershell
$ModelMap = Join-Path $env:TEMP 'tony-agents-model-map.json'
py -3 scripts/manage.py validate
py -3 scripts/manage.py update --dry-run --model-map $ModelMap
py -3 scripts/manage.py update --model-map $ModelMap
```

若用户没有要求重新适配，省略 `--model-map`，但仍执行当前平台的 validate、update dry-run、update 顺序。

agent 不在 model-map 时保留当前有效的 `model` 和 `thoughtLevel`；agent 在 model-map 时使用映射明确给出的 `model`，`thoughtLevel` 省略就删除旧值。目标有用户修改时进行三方合并；冲突或 Git 不可用时不覆盖，生成 `.tony-agents-pack.incoming` 并报告。Git 仅在此类三方合并和版本获取时需要。

新版新增 agent 时按初装规则安装并备份同名目标；新版移除 agent 时只恢复或删除 SHA 未变化的文件。用户修改或缺失的已移除 agent 会保留并继续在 state 中跟踪；正式更新前会创建覆盖新旧集合的快照。

## 完成报告

报告安装模式、操作系统、目标目录、17 个 agent 的绑定模型、降级项、冲突/候选文件、状态路径和最近快照路径。不要报告任何不在安全白名单内的配置字段或值。提醒用户新会话生效。
