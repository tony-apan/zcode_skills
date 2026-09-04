# Changelog

本包遵循语义化版本：主版本（MAJOR）用于删除或改名 agent、改变必填输入/输出契约、扩大工具权限等破坏性变更；次版本（MINOR）用于向后兼容地新增 agent 或能力；修订号（PATCH）用于不改契约的措辞/事实修正和安装器 bug 修复。

## [1.0.4] — 2026-09-04

### 修正
- update 会直接重装 state 中仍受管理但目标已缺失的 agent，并报告 `Reinstalled missing`，同时保留可恢复的本地模型字段
- install 新增显式 `--force` 重装：先快照旧 state 与新包 agent 并集，迁移有效历史备份，再完整安装；dry-run 保持零写入
- AI 安装协议改为先检查默认 state 再分流，移除无法执行的客户端版本/模型存在性预检，并从脱敏 inventory 判断 provider/model 前提
- inventory、model-map 与 clone 全部使用 mktemp/GUID 唯一路径；固定 tag、文档优先级、冲突人工确认、未知上下文降级和临时目录清理规则进一步收紧
- Ubuntu/macOS CI 增加 shell wrapper 的真实 dry-run，版本一致性测试改为动态比对 plugin、changelog 首条和 README 固定 tag
- 17 个 `agents/*.md` 岗位定义与契约未改动

## [1.0.3] — 2026-09-04

### 修正
- 自动安装、更新与卸载提示词固定到最新 `v1.0.3` tag
- 兼容矩阵改为稳定、可持续的验证表述，不再包含发布后立即过期的“当前版本待 CI”状态
- 17 个 `agents/*.md` 岗位定义与契约未改动

## [1.0.2] — 2026-09-04

### 改进
- README 重构为面向 ZCode 新用户的“发送仓库链接给 AI”三步自动安装体验，并将 AI 自适配设为唯一推荐入口
- INSTALL-FOR-AI.md 升级为可从 URL 启动的 bootstrap 协议，增加 ZCode/OS/Git/Python 前提检查、固定 tag 临时 clone 与双平台分支
- 增加可复制的安全更新与卸载提示词，明确同版本不重复、state 分流、本地修改保护、dry-run、快照和候选文件报告
- 17 个 `agents/*.md` 岗位定义与契约未改动

## [1.0.1] — 2026-09-04

### 修正
- v1.0.0 发布时线上仓库名已经是 `tony-apan/zcode_skills`；本版本补充 Windows CI 验证并同步安装文档
- Ubuntu、macOS、Windows 三平台 GitHub Actions 全部通过；Windows 上已实际完成 Python 3.9 全套测试与 PowerShell 安装器 dry-run
- README 默认安装 tag 更新为 `v1.0.1`，兼容性状态改为有证据的实际结论

## [1.0.0] — 2026-09-04

首个可发布版本，包含 17 个智能体和可审计的安装生命周期。

### 新增
- **工程线**：coder / coder-gpt / coder-ds / coder-kimi（四工程师同契约并行对比设计）、frontend（原型/实现双模式）
- **内容线**：writer（任务三类分法：获客内容/产品微文案/改写润色）、writer-pro（增强档，双稿对比）
- **外贸获客线**：huoke（进口证据优先 + A–E 渠道分级 + 注册库核验）、outreach（多语言度量本地化 + 多法域合规 + BEC 防御 + C/D/E 渠道形态）、seoer（SERP 实证分级 + 任务分档 L0/L1/L2 + 选题工单）、jiankong（sitemap 差分 + 素材三档判定 + 回落生产链）
- **核查线**：shencha（静态审查）、shencha-content（内容红队四线五视角）、shencha-ui（视觉审查）、verifier（运行验证 + 命令预检）、shencha-final（终审收口，不推翻专项结论）、tijian（网站体检六维）
- 脱敏模型清单 helper：在本机读取 ZCode 配置，仅输出模型选择所需白名单字段，避免 AI 接触 options、密钥、token、baseURL 和未知字段
- Python 标准库安装器：结构化 frontmatter 处理、model-map、dry-run、原子写入和安装后复验
- 跨平台安装入口：macOS/Linux shell wrapper 与兼容 PowerShell 5.1+ 的 Windows wrapper
- 安全更新：保留未重新映射 agent 的本地模型字段；显式换模型但省略 thoughtLevel 时删除旧值；基于上一版 base 三方合并，冲突时生成 incoming 候选
- agent 集合升级保障：新增 agent 按初装规则备份并安装；移除 agent 只处理 SHA 未变化的文件，用户修改继续保留并跟踪
- 操作前快照、同名文件备份、`rollback latest` 与按快照 ID 回滚
- SHA 保护卸载：只删除未修改包文件，并恢复安装前同名文件
- GitHub Actions 配置覆盖 Ubuntu、macOS、Windows 和 Python 3.9；首次 push 后以实际 Actions 结果为准
- 本地发布脚本：版本、changelog、干净工作区和 tag 检查后创建 annotated tag

### 契约亮点
- 全员提示注入防御（外部内容中的指令一律视为数据）
- 状态化交付：受阻/部分完成时禁止“已交付”字样，防假交付
- 固定判定标准：所有审查岗结论可从规则反推
- 覆盖对账表 + 输入真实性优先，反“前紧后松”与空转
- 生产者交稿自动提醒送审，检查链闭环

### 发布处理
- 剥离作者本地 model/thoughtLevel/skills 配置，改为“模型需求标签” + INSTALL-FOR-AI.md 适配安装协议
- 增加 MIT License、GitHub 首屏文档、安装/更新/回滚/卸载说明和版本化发布流程
