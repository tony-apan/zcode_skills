# Changelog

本包遵循语义化版本：主版本（MAJOR）用于删除或改名 agent、改变必填输入/输出契约、扩大工具权限等破坏性变更；次版本（MINOR）用于向后兼容地新增 agent 或能力；修订号（PATCH）用于不改契约的措辞/事实修正和安装器 bug 修复。

## [3.0.0] — 2026-09-07

### Breaking changes
- `github` 智能体的 `RELEASE_GATE` 必填输入/输出契约升级：审计新增 `changed_files`、`changed_agents`、`breaking_impact`，并对 Scope、Evidence、Findings、Agent Links、Improvements、Migration 与 Hand-off 做语义校验。
- 维护者 push 流程改为强制安装 hook 并为每次发布 payload 获取新 PASS 审计；旧格式审计不能复用。普通用户的 agent 安装/update 仍兼容，安装 state schema 不变。

### 新增
- 每次维护者 push、PR 和 tag 都必须具有 `github` 智能体 `MODE=RELEASE_GATE` 的真实 PASS 审计；pre-push hook、三平台 CI 与 `release.sh` 共同执行硬门禁，普通安装用户不受影响。
- 新增确定性 package fingerprint。Git 仓库中只纳入 tracked 与非 ignored untracked 发布路径；仅版本审计报告 `release-audits/v*.md`、Python cache、根目录 checksum 和明确安装运行产物排除，audit README 等治理文件参与。每项同时哈希路径、类型、内容或 symlink target 以及 executable bit。
- `github` 新增 `REPO_REVIEW`、`README_POLISH`、`RELEASE_GATE`、`RELEASE_NOTES` 四种模式，保持无 Bash/Write/Edit 的硬只读边界。
- package fingerprint 的 tracked 路径、类型、executable marker 与内容全部绑定 Git index mode/blob，并通过 `git cat-file blob` 读取原始字节，避免 checkout 换行转换和 filesystem mode 造成跨平台差异；PASS 审计前要求暂存所有发布文件，Git 识别为真实内容差异的 unstaged 发布路径会阻断。
- GitHub Actions 的 checkout、Python setup 和 artifact upload 全部固定到由 GitHub refs API 解析的官方完整 commit SHA，同时保持最小 `contents: read` 权限。

### 用户价值
- 发布前自动阻止测试失败、版本漂移、敏感信息、缺失智能体链接或缺少改进说明的版本进入 GitHub，用户看到的每个版本都能追溯到固定源码快照和审查报告。
- 发布交付统一包含版本、智能体链接、用户价值改进、兼容影响、验证结果与审计链接；README 提供无需改变量的完整 ZCode 审查提示词和渐进式优化路线图。

### 兼容性与迁移
- 普通用户向后兼容 `v2.0.0` 的 20 个 agent 安装、更新、回滚和卸载路径，按 README 更新到 v3 即可，安装 state schema 不变。
- 维护者必须运行 `./scripts/setup-hooks.sh`，并按 v3 schema 生成每次 push 对应的真实审计；旧审计会被 gate 拒绝。

## [2.0.0] — 2026-09-07

### Breaking changes
- 20 岗逐个完成红队强化，输入、输出与交接契约均有变更。已安装用户应按 README 的更新提示词升级；安装器继续通过三方合并保护本地模型绑定和其他本地修改，冲突须人工处理。
- 五个验收岗统一采用 `PASS | BLOCK | INCONCLUSIVE` 公共协议；`INCONCLUSIVE` 表示核心证据不足、不得交付，不代表已发现缺陷。`shencha-content` 拆分为 seo、conversion、social、email、microcopy 五个 profile。

### 工程
- coder 四岗按日常、攻坚、并行分流和长上下文差异化路由；并行实现要求独立 worktree、固定基线和候选隔离，并补充永久高危命令禁令与可复跑证据。
- frontend 明确实现/原型模式、无障碍与截图证据协议，并删除本地私有 skill 元数据。
- mermaid 固定单一代码块、输入净化、集合对账并保留 `graph TD`，禁止 `click` 等可执行或外联语法。

### 内容增长
- writer 增加 A-E 任务模式与每稿成功定义；writer-pro 增加受控双稿盲测协议。
- seoer 增加可计算评分和 SERP 快照；sheyun 增加 S1-S4 模式并固定 `review_profile=social` 审查交接。
- outreach 分离内容与发送状态，默认 `SEND_BLOCKED`；huoke 增加证据类型分级与结构化交接。

### 情报与验收
- jiankong 增加 `pending` 状态机和 SSRF 防线；tijian 区分被动公开检查与需明确授权的 `ACTIVE_SECURITY`，技术 SEO/CWV 无证据时保持未知。
- verifier 在无已证明沙箱时不执行不可信代码；github 从工具元数据硬移除 Bash/Write/Edit，并通过 `disallowedTools` 明确禁止，保持只读体检。

## [1.2.1] — 2026-09-07

### 改进
- sheyun 对抗深化：从“发布技工”升级为“获客操盘手”——新增客户视角内容策略（动笔前四问、痛点选题六大轴、旅程分层 5:3:2、八种软性获客手法与 B2C 禁用清单）
- 每帖新增“获客目标”必备字段（读者/阶段/动作/承接私信草稿）；软帖禁“欢迎联系我们”式收尾
- 真实性边界：故事化=讲真事的方式；无素材三条合法路径与禁句清单；互动获客主动层（选题雷达/温客回访）与反骚扰边界
- 其余 19 个 `agents/*.md` 契约未改动

## [1.2.0] — 2026-09-07

### 新增
- **sheyun**（社媒运营岗）：为 LinkedIn、Facebook、Instagram 等平台产出帖子、内容日历、hashtag 策略与互动模板
- **github**（仓库管家岗）：审核并美化 README、补齐规范文件、执行仓库健康检查并撰写 release notes
- 安装器与文档全链路从 18 个岗位扩展到 20 个岗位

### 改进
- README 专业化改版：重整首屏、章节结构与岗位全景，并新增 Mermaid 流水线示意图

## [1.1.3] — 2026-09-04

### 改进
- 品牌重定位为“外贸 AI 员工团”：README 标题、首屏介绍与插件描述改为面向外贸用户的结果导向表述（找客户/写开发信/做 SEO/盯友商/审质量/验网站）
- 技术名 tony-agents-pack 保持不变，不破坏已装用户的升级路径
- 18 个 `agents/*.md` 岗位定义与契约未改动

## [1.1.2] — 2026-09-04

### 改进
- 仓库 About 与插件描述改为中文，且不再写死智能体数量（岗位持续新增）
- README 开头与徽章同步移除固定数量表述，改为“当前 18 个、持续新增”
- 18 个 `agents/*.md` 岗位定义与契约未改动

## [1.1.1] — 2026-09-04

### 改进
- README 新增“关注新版本”：Watch Releases 通知、Star 收藏、releases RSS 订阅，以及“更新到最新版”的一句话用法
- 18 个 `agents/*.md` 岗位定义与契约未改动

## [1.1.0] — 2026-09-04

### 新增
- **mermaid**（图表岗，第 18 个智能体）：把业务描述/聊天记录转成语法安全的 Mermaid graph TD 流程图——纯英文节点 ID、连线文字无标点、禁内联样式、classDef 统一七色配色，适配严格 Markdown 渲染器
- 安装器与文档全链路从 17 岗位扩展到 18 岗位

## [1.0.5] — 2026-09-04

### 改进
- README 新增“强烈建议：配置多个大模型”：单模型环境的能力折扣说明与最小推荐组合（强推理/便宜快/图像输入/长上下文/强写作）
- INSTALL-FOR-AI 完成报告新增“模型多样性提示”：实际只分到一个模型时必须告知用户并行对比与成本分层不生效，并引导添加 provider 后用更新提示词重新分配
- 17 个 `agents/*.md` 岗位定义与契约未改动

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
