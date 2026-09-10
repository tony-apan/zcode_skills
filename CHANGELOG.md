# Changelog

本包遵循语义化版本：主版本（MAJOR）用于删除或改名 agent、改变必填输入/输出契约、扩大工具权限等破坏性变更；次版本（MINOR）用于向后兼容地新增 agent 或能力；修订号（PATCH）用于不改契约的措辞/事实修正和安装器 bug 修复。

## [4.2.0] — 2026-09-10

### 新增
- 新增 `gonghao` 公众号运营岗：按 G1 单篇、G2 系列、G3 周运营、G4 纯策略产出中文长文与配套运营物料（标题候选、摘要、封面配图说明与 alt、引导关注、单一 CTA），并维护控制卡与选题库。
- 公众号平台特有合规写入契约：禁止诱导分享/关注/点赞（奖励、抽奖、集赞、强制转发）；广告法红线（绝对化用语与无法证实的承诺）；敏感行业资质要求；原创声明、转载授权、留言区互动与自动回复按平台现行规则并记录核验日期。
- 中文原生表达要求：避免翻译腔、排比堆砌与四字词硬凑；标题承诺必须由正文兑现。
- 广告可识别性：含购买方式、优惠、导流链接或软文时须显著标注"广告"，不得伪装成纯分享或以新闻报道形式发布广告。
- 广告审查批准文号：医疗、药品、医疗器械、保健食品等依法需审查的广告须取得文号并规范标注，无文号不得发布。
- 合规核验列为控制卡必填字段（`平台规则来源URL + 发布/访问日期`、`适用法域与行业`、`资质/审查文号缺口`、`是否需标"广告"`、`诱导互动风险自检结论`）；敏感行业缺资质或无法核验时固定降级 `DRAFT_DO_NOT_PUBLISH`。
- `gonghao` G1–G4 每种模式末尾都锚定待确认；其中有成稿的 G1–G3 另输出 `CONTENT_STATUS`，G4 无成稿不输出。交审同时指定 `review_tier` 与 `upstream_agent`（仅 G1–G3）。
- 纯策略模式（`gonghao` G4、`sheyun` S4）对称收口：无成稿故**不输出 `CONTENT_STATUS`**（该字段语义为"存在草稿"），`sheyun` S2/S3 补齐状态与待确认锚定；不进入内容门禁：`shencha-content` 只审成稿、`shencha` 只审代码与工程文档，策略稿统一交主智能体决策，两者完成末行分别给出成稿/未成稿两套措辞。
- `frontend` 交付末行接通 `shencha-content review_profiles=[microcopy] review_tier=STANDARD`（敏感行业升 `HIGH_RISK`）。
- 审计排除规则的文档表述统一为精确模式 `release-audits/v<major>.<minor>.<patch>.md`，与 `release_gate.py` 实际正则一致（原 `release-audits/v*.md` 为过宽表述）。

### 变更
- `frontend` 新增"界面文案（微文案）"职责：按钮、字段 label、错误、空状态、加载、权限不足、破坏性确认与 onboarding 引导由前端实现方撰写，要求与真实状态同源、用户语言、一个概念一个叫法、受限字符空间内不溢出、可见文案与可访问名称一致；营销与 SEO 长文案仍归 `writer`；真实状态无法确定时标 `[文案待确认：<缺什么>]`。
- `frontend` 交付格式新增第 4 项"微文案"，其余项顺延。
- 明确 `frontend` 与 `writer`/`writer-pro` 的**微文案所有权排他**：接入具体 UI、有真实状态与字符约束的界面文案由 `frontend` 唯一负责；`writer` 的 D 类收窄为"脱离具体界面实现"的产品短文案（产品名、slogan、独立通知邮件等），界面类需求转为对 frontend 的输入。
- `shencha-content` 的 `social` 上游生产者由 `sheyun` 扩展为 `sheyun` 与 `gonghao`（公众号长文），两者独立授权、不得互相冒充，报告新增 `upstream_agent` 字段。
- `sheyun` 契约内写明平台范围为 LinkedIn / Facebook / Instagram，并指明公众号归 `gonghao`。

### 兼容性
- 新增 agent 与职责补充均属向后兼容的 minor 更新；普通安装用户直接执行 update 即可，安装 state schema 不变。安装器会新增 `gonghao`、更新 `frontend` 正文，并通过三方合并保留本地 `model`/`thoughtLevel` 与其他本地修改。
- `sheyun` 覆盖范围明确为 LinkedIn / Facebook / Instagram；公众号内容由 `gonghao` 承担。
- 调用方如使用 `gonghao`，交审 profile 为 `review_profiles=[editorial,social]`（长文体裁承载在社媒平台，两类审查联合执行）。

## [4.1.0] — 2026-09-09

### 新增
- 新增 `dongcha` 需求洞察岗：从产品、公开材料与脱敏一方数据发现客群、采购角色、场景、痛点及问题种子，严格分离 ICP 假设池、内容话题池与 `VALIDATION_BACKLOG`。
- 引入 claim/evidence/hypothesis 状态机、E0–E4 证据等级、按 claim_type 的证据下限与专项 verdict 命名空间；`SEARCH_VALIDATED`、`SERP_VALIDATED`、`QUERY_HYPOTHESIS` 和 `AI_PROMPT_VALIDATED` 不得相互越权升级。
- 固定“只提议不授予”：`dongcha` 不能授予 `VERIFIED` 或 `PRODUCTION_ELIGIBLE`；候选经来源追溯、证据分级、证据下限、反证冲突、时效权利、内容与终审资格六道审查，单项最多 3 轮验证后仍不足则保留 backlog。

### 专项交接
- `seoer` 新增 SERP、意图、结果类型、蚕食、可赢性 verdict 与 `SERP_VALIDATED` 边界；`huoke` 新增 ICP predicates、证据等级和 N1/N2/N3 反例语义。
- `outreach` 新增带 claim/evidence 状态的 `angle_draft` 事实钩子门禁；`sheyun` 新增公开讨论安全、视觉证据、互动钩子、lead magnet 与品牌风险字段。

### 双路审查修复
- 修复授予侧装配：`shencha-content` 逐 claim 复核并仅授予 `claim_status=VERIFIED`，`shencha-final` 独占 `production_verdict=PRODUCTION_ELIGIBLE` 授予；`writer`/`writer-pro` 仅消费通过三重事实门禁且携带 claim/evidence 引用的内容。
- 补齐 run、单 claim 与专项重验轮次上限及超限 `BACKLOG`/`REJECTED` 正常交付；将 claim_type 证据下限改为 source_type 白名单、最小独立来源数和禁止替代的结构化表，并阻止以 `need_jtbd` 改标绕过搜索/行为证据门槛。
- 新增否定不变式测试与发布校验：固定 `dongcha` body SHA，校验五岗契约 marker，并拒绝 `claim_status` 枚举包含 `PRODUCTION_ELIGIBLE`。

### 兼容性
- 新增 agent 属于向后兼容的 minor 更新；普通安装用户直接执行 update 即可，安装 state schema 不变。管理器会保留现有 agent 的本地 `model`/`thoughtLevel` 与其他可合并修改。

## [4.0.0] — 2026-09-08

### Breaking changes
- `shencha-content` 的调用与报告主字段由单值 `review_profile` 升级为去重集合 `review_profiles`；旧字段进入 deprecated 兼容期，可临时映射为单元素集合，但新调用应迁移到 `review_profiles`。
- 新增必填 `review_tier=QUICK|STANDARD|HIGH_RISK`。只有 `STANDARD` 或 `HIGH_RISK` 得到 `PASS` 才能输出 `publication_decision=GO`；`QUICK` 永远输出 `NO_GO`。

### 新增
- 新增可与 `seo`、`conversion` 等 profile 组合的 `editorial`，覆盖专业文章、白皮书、行业洞察、thought leadership、case study 与 research report 的论证结构、信息增量和编辑质量；profile 空集、未知 token、不适用组合或专业稿缺 `editorial` 均失败关闭为 CORE UNVERIFIED / INCONCLUSIVE / NO_GO。
- 新增去重 claim ledger、`OBSERVED`/`VERIFIED_EXTERNAL` 等证据边界、分档覆盖率、publication GO/NO_GO、返工工单及 `READY_FOR_RETEST` 独立复测闭环；多语言高风险内容可进入 `PENDING_NATIVE_REVIEW`。

### 双路审查修复
- 修复 Terra P1：HIGH_RISK 长稿先 preflight；超过 40 claims、8000 词、12000 中文字或输出预算时强制 Phase A + 每 part 最多 20 claims，任一 part 缺失、截断、不可解析或 claim 覆盖不精确即 INCONCLUSIVE / NO_GO；无 finding 时也必须对同 snapshot 全部 claims/parts 独立二审。
- 修复 Flash P2/P3：QUICK 禁止 PASS；STANDARD 使用 `(hash, claim-id)` 唯一全序、章节保底后全局补样到固定 K；按 sheyun/outreach 枚举固定映射上游状态；P0/P1 仅 VERIFIED 后可 GO；明确独立复测身份与材料边界；`shencha-content` frontmatter 硬禁止 Bash/Write/Edit，并新增关键协议与写工具负向门禁、CRLF 和确定性抽样复现测试。
- 补非阻断审查改进：报告接口 `next_action` 固定为 `PUBLISH | REWORK | SUPPLY_EVIDENCE | RUN_STANDARD_REVIEW | RUN_HIGH_RISK_REVIEW | NATIVE_REVIEW | INDEPENDENT_RETEST` 枚举；`open_ticket_ids` 固定为处于 OPEN 状态的 finding 工单 ID 数组并取代重复字段 `rework_tickets`，`publication_decision=GO` 时必须 `next_action=PUBLISH` 且 `open_ticket_ids=[]`，`NO_GO` 按真实阻断原因取值；非法输入措辞改为“输入为空，或清洗非法/重复项后集合为空”；发布校验器新增 `FINAL_CONTENT`、`FINAL_DRAFT`、`SEND_BLOCKED`、`READY_FOR_HUMAN_SEND_REVIEW` 契约 marker 与逐枚举负向门禁测试；本地与发布版 `shencha-content` 正文保持一致。
- `sheyun` 两处 deprecated `review_profile=social` 交审调用迁移为 `review_profiles=[social]`；P2/P3 可聚合展示，但不得丢失 claim ledger 证据索引或 finding 必填字段。

### 兼容性与迁移
- breaking 影响 `shencha-content` 的调用与报告字段，并同步迁移 `sheyun` 的交审调用；普通安装 state schema 不变。安装器更新 agent 正文时继续保留本地 `model`/`thoughtLevel` 和其他可合并的本地修改。
- 本次 changed agents 为 `sheyun` 与 `shencha-content`，其他 18 个 `agents/*.md` 不变。回滚时可按现有 snapshot/rollback 流程恢复上一版 agent 正文与 state。

## [3.1.1] — 2026-09-07

### 优化
- README 社群二维码在 v3.1.0 现有尺寸基础上再缩小 50%（`width="50%"` 调整为 `width="25%"`）；“扫码入群”入口、仓库内图片 `src`/`href` 与原始来源说明保持不变。
- README 维护者专用的“每次 push 前必须 GitHub 智能体审查”与 github 智能体优化路线图整段折叠进独立 `<details>`（summary 为“维护者专用：GitHub 发布审查与 PR 门禁”），闭合于“高级安装、兼容性与维护”之前；规则内容完整保留，仅降低标题展示层级。

### 兼容性
- 仅文档展示、校验 marker 与对应测试更新；无智能体契约变更，20 个 `agents/*.md` 均未修改，安装 state schema 不变。

## [3.1.0] — 2026-09-07

### 新增
- README 新增 `width="50%"` 的仓库内社群二维码和“扫码入群”入口；二维码作为版本资产纳入发布 fingerprint，避免外部图片变化影响已发布版本。
- 新增国产模型优先的 `MODEL_SETUP.md`，配套 4 张 API Key 已打码的 ZCode 配置截图，覆盖官方入口、Base URL、协议选择、岗位建议和常见报错排查。
- 国产主推智谱 GLM、DeepSeek、Kimi，并新增阿里云百炼/Qwen 与硅基流动的可选推荐；Google Gemini 仅作为海外可选方案。

### 工程
- 发布校验新增指南内容、二维码 marker、公开文档敏感信息及 5 张 PNG 的存在性、结构、chunk 边界、CRC 和最小体积检查；该检查不做视觉解码，并补齐无网络请求的正负测试。

### 兼容性
- 仅新增普通用户配置指南、社群入口和发布装配校验；无智能体契约变更，20 个 `agents/*.md` 均未修改，安装 state schema 不变。

## [3.0.1] — 2026-09-07

### 维护流程
- 远端 `main` 保护已启用 PR-only：required checks 为 `validate (ubuntu-latest, 3.9)`、`validate (macos-latest, 3.9)`、`validate (windows-latest, 3.9)`，要求分支与 `main` 保持最新（strict），对管理员同样强制（admins enforced），并启用 linear history 与 conversation resolution；禁止 force push 和删除受保护分支。
- required approving review count 为 `0`，避免单人仓库因必须由他人批准而自锁；所有改动仍必须通过功能分支和 PR 合并，禁止直接 push `main`。
- README 与 INSTALL-FOR-AI 的维护者发布说明同步为 audit、gate、功能分支 commit/push、PR、required checks、合并 `main`、更新本地 `main`、再创建 release tag 的顺序。

### 兼容性
- 仅文档、版本与文档 marker 测试更新；无智能体契约变更，普通安装、更新和卸载流程不变。

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
