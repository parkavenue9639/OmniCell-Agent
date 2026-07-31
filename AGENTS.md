# OmniCell-Agent 项目工作规则

## 适用范围与架构基线

- 本文件适用于整个仓库；更深层目录若新增 `AGENTS.md`，可以补充局部规则，但不得削弱本文件约束。
- 开始重要架构、实现、迁移或验证工作前，必须先阅读根目录 `ARCHITECTURE.md` 及相关目录规则。
- `ARCHITECTURE.md` 是本轮重构唯一的架构与进度基线。发现实现计划与其冲突时，先澄清或更新架构决策，再修改代码。
- 不在本文件复制完整架构、阶段表或临时状态；职责边界、实施顺序、完成门槛和进度以 `ARCHITECTURE.md` 为准。
- 新增或更新架构、设计、决策和进度类文档时，必须以中文为主体，仅保留必要的英文协议名、路径、文件名和代码标识符。
- 本项目是面向研究生毕业设计的单机科研原型；优先保证科学行为、可复现性、结构清晰和本地演示，不主动引入多租户、高可用、分布式运维或其他生产平台复杂度。

## 核心领域不变量

- 必须保留历史分析与注释流程中已经验证的科学行为，但 Graph A/B 的图名、固定入口、节点名和能力分组不再属于 Agent-facing 或用户可见语义。
- Agent Loop 负责通用编排与完成判断，领域 Tool 负责明确科学目标，内部引擎负责内聚的执行与反馈循环，执行环境负责隔离运行；各层职责不得相互侵入。
- Agent Loop 必须按目标选择最小充分路径：稳定、低风险且不依赖领域方法边界的知识可以直接回复；问题命中 Skill 摘要中的领域术语或方法，且答案依赖其操作定义、适用条件、统计假设或证据边界时必须按需加载 Skill，答复篇幅不是跳过加载的理由；局部读取或校验使用检查 Tool，单一科研目标使用对应领域 Tool，只有包含多个相互依赖且可分别验证的步骤时才创建显式计划。加载 Skill 可以只为当前回复提供方法上下文，不自动要求读取数据或执行 Tool；不得因为存在数据集就默认运行完整分析，也不得为简单任务形式化建计划。
- 顶层 Agent 的统一响应契约只承载跨领域的约束优先级、最小充分表达、证据分层、结构与类比边界，不充当领域知识库；具体方法事实和验证规则由匹配 Skill 渐进提供，Skill 不能覆盖当前用户对语言、篇幅、受众、格式、重点和排除项的明确要求，也不得用输出后机械截断代替生成时取舍。
- 模型可见语义必须保持单一职责来源：顶层 Agent 管理领域无关的路由、表达与完成约束，Skill 提供可渐进加载的方法知识，Tool 契约声明原子科学目标与前后条件，复合能力内部提示词只处理本能力内的专业决策。内部提示词不得要求隐藏思维链、用夸张角色设定制造权威感、把启发式评分表述为校准概率，或在缺少前置条件时隐式补做计划外分析；未被运行时引用且与当前契约冲突的旧提示资产不得作为并行事实源保留。
- 每条用户输入创建 Run，但不得自动创建代表整条输入的根 Task；Task 只表示显式计划步骤或实际能力调用。没有未完成显式计划时，非空且无 Tool 调用的 Assistant 文本应自然完成 Run；普通问答和单 Tool 总结不得强制调用 `finish_task`，未完成计划与空回复仍须保留有界 backpressure。
- 成功的领域 Tool 必须用当前 run 中的真实调用证据自动对账唯一活动计划步骤，不得要求模型猜测内部 Tool call identity；Assistant 文本只有在计划与完成门禁通过后才能发布为公共消息，被拒绝的候选回复不得先进入 frontend。自然完成与 `finish_task` 必须复用同一公共消息资源边界，不能分别维护 URI、宿主路径或控制目录过滤规则。
- 公开能力必须使用科学目标、输入输出 artifact、前置条件和验证标准命名；上层不得依赖历史工作流或内部节点拓扑。
- Skill 与 Tool 必须正交注册：Skill 可以引用多个 Tool，同一 Tool 可以被多个 Skill 复用，未被 Skill 引用的独立 Tool 也允许存在；启动期只校验名称、类型契约和 Skill 引用的 Tool 确实已注册，不建立唯一所有权或反向绑定。
- 初始 Agent 上下文只暴露 Skill 摘要和 Tool 行为提示；Skill 正文、reference 与 example 必须按需渐进加载，并通过 run-scoped 资源标识重建到当前模型视图，不作为普通 ToolMessage 永久堆叠。只有名称、版本和内容哈希均与当前目录一致的正文 `body` 可以满足 `required_skills` 并解锁对应 Tool；reference/example 必须在同版本正文加载后才能加入方法上下文，且不能单独解锁 Tool。模型可见 Tool view、`load_skill` 与领域 Tool 执行门禁都必须重新验证该身份，包含直接恢复到 pending tools 节点的路径；任何已加载正文或子资源无法按当前目录重建时，本次 Tool 必须在执行或加载前结构化拒绝并从状态中清除失效资源，下一轮才允许重新加载。每次渐进加载必须在写入 checkpoint 和发出 completed 事件前验证聚合上下文边界，并形成类型化、可重放的 started/completed/failed 事件；公共事件只暴露 Skill、资源层级、受控用途和结果，不复制正文、宿主路径或模型思维链。每个模型可见 Tool 都必须提供明确的调用与禁用条件，但提示不能替代类型校验、Tool policy、artifact ownership 或执行隔离。
- 所有 Agent-visible Tool，包括 Loop 拒绝、控制 Tool、Skill 加载和领域 Tool，必须使用统一的结构化 outcome；失败至少包含稳定 `error_code`、`retryable` 和 `recovery_hint`，三者必须与状态更新后的真实恢复动作一致，不得回退为模型需要猜测语义的纯文本错误。
- 模型一次返回多个 Tool call 时，必须在写入 checkpoint 前按剩余 Tool 预算做有界规范化；canonical `tool_call_id` 必须非空、有界、属于公共安全字符集且批内唯一，并为每个持久化 ID 生成且只生成一个结构化拒绝结果。不得留下未配对的合法 call、隐藏在 `additional_kwargs` 的原始 call、未清理的 invalid call 或超出预算的计数。同一 run 跨轮复用已有 `tool_call_id` 时，只允许严格幂等重放同名同参的既有结果；名称或参数冲突必须结构化拒绝，已经消费的成功 evidence 只能绑定一个计划步骤。
- Conversation checkpoint 可以保留跨 run 的消息历史，但每条新持久化 AIMessage 的身份必须包含当前 `run_id`；不得仅以 run-scoped turn 或消息内容生成身份，避免 LangGraph reducer 在连续 run 间误替换消息。
- Skill 表达方法、选择和验证规则，Tool 表达类型化执行能力，Recipe 只属于 Tool 内部确定性实现；三者不得复用同一名称掩盖不同语义。
- 能改变科学数据状态的原子 Tool 必须生成新的版本化 ArtifactRef，不得原位覆盖输入，也不得依赖跨 Tool 调用残留的容器局部状态；只有具备明确科学语义、输入前置条件、输出后置条件和代表性验证的能力才能进入公共 Tool 面。
- 领域 Tool 的运行完成、科学目标完成和事实验证必须分层表达；Dataset 状态只能由实际输出、操作处置和科学后置校验生成，孤立 metadata 不能替代矩阵或可信 lineage；marker 阈值、有限统计量和 cluster selection coverage 不得静默降级，annotation 入口缺失完整 selection evidence、验证失败、证据不支持或输入覆盖不完整时必须失败关闭并进入人工复核。
- 当前数据结论只能使用本次 Run 中有界、类型化且绑定来源的科学证据；自然回复与 `finish_task` 必须共用 backend 权威终答渲染，模型自由文本和有限自然语言正则不能充当完整科学门禁，执行状态、数量、Artifact 或证据等级冲突的候选不得进入公共事件或 checkpoint 历史。
- 本轮是全新重构，不保留旧模块路径、旧类名、旧函数、旧 CLI、旧 API、历史 DAG 名称或固定入口兼容；需要延续的只有经验证的科学行为。
- 目录移动、运行环境替换、调用方式变化与科学行为变化必须明确区分，避免在同一项工作中隐式混合。
- 任何可能改变核心分析或注释行为、输出、反馈循环、路由或并发语义的变更，都必须明确说明意图，并用代表性基线或契约证据验证。
- 未经架构决策和显式验证，不得以重构、简化、重命名或接入 Agent Loop 为由削弱既有科学能力。

## 实施顺序与进度证据

- 按 `ARCHITECTURE.md` 规定的阶段顺序实施；可以提前进行只读探索或无依赖准备，但不得绕过未完成的前置条件。
- 开始、阻塞或完成一个阶段时，同步维护 `ARCHITECTURE.md` 的进度台账。
- 阶段完成必须绑定可复查证据；门槛未全部通过时，不得标记完成或用后续阶段结果替代当前阶段证据。
- 新的架构决策应先写入 `ARCHITECTURE.md`，再让实现依赖该决策。
- 交接时明确区分已完成、已验证、仅推断、仍阻塞和后续工作，避免把代码存在等同于阶段完成。

## Monorepo 高层边界

- `backend` 负责权威的 conversation/run 生命周期、Agent 与领域能力执行、执行环境、模型选择、持久化、artifact 和事件流。
- `frontend` 负责用户交互与服务端事件的确定性投影；不得从本地 UI 状态推断权威 run 终态，也不得依赖 backend 内部状态结构。
- Conversation 标题属于 backend 权威 metadata。新建空 conversation 可以由 frontend 显示占位，但首条有效用户目标应通过统一 `summary` LLM alias 生成有界标题，并在 provider 失败或输出非法时使用确定性兜底；自动标题只能替换空值或保留占位，不能覆盖明确标题、反复抖动或阻断 Run。
- Frontend 主时间线只把 Agent 的执行行为呈现为 Skill 加载、Tool 调用和 Backend 操作；内部 `capability.*` 生命周期必须并入对应 Tool，不能作为第四类“能力”暴露。三类活动卡片都应直接说明动作、过程和结果，不能用无定位价值的内部抽象或泛化翻译替代真实行为。
- `contracts` 负责 backend 与 frontend 共用的版本化公共契约；契约变化必须同时验证两端兼容性。
- Frontend 公共 DTO 必须从 `contracts` 单向生成并通过漂移检查，不得另建手写的并行契约或让生成检查直接覆盖工作区。
- `infra` 负责本地拓扑、运行依赖和环境边界；不得承载产品领域逻辑。
- 跨层协作应通过稳定契约和资源引用完成，不得泄漏数据库行结构、LangGraph 内部状态、frontend store 或内部引擎节点。
- 大型科学数据、生成文件和执行输出属于 workspace/artifact 层，不进入控制状态、checkpoint 或事件 payload；checkpoint 写前约束必须覆盖状态、metadata 和中间 writes 的完整 saver 写入面。
- 应用表与 LangGraph checkpoint 表必须分别由项目 migration 和 saver migration 唯一管理，且使用不同 schema；禁止双重建表、同名 schema 或跨边界修改。
- 单个 run 的事件顺序必须由数据库原子分配；run 状态和对应事件同应用事务提交，不得把独立连接上的 checkpoint 写入宣称为同一原子事务。
- 顶层 Agent 只能通过受支持的 run 生命周期入口执行；不得直接调用 compiled graph 绕过 run 创建、事件、终态、artifact 登记、取消传播或资源收尾。
- 多 worker 执行必须以持久化 lease 与 attempt fence 约束所有权敏感写入；heartbeat 失效时当前执行应 fail-closed，旧 owner 不得在新 owner 接管后继续提交事件或终态。
- 正式 Agent 组合路径调用普通同步 capability 时必须使用可终止的隔离执行边界，不得回退到线程执行并把 Future 取消视为底层工作已停止；测试专用的进程内替身必须显式声明其不提供硬终止保证。
- 隔离执行的存活续期只能由已成功提交的数据库 claim/heartbeat 驱动；取消、续期失效或父进程失联后，必须确认 worker 进程组及其精确 owned runtime 已回收，再释放 lease 或写入终态。跨进程 runtime claim 必须位于容器不可见的 backend 控制目录，且仅作为定位线索；回收前必须复验容器 ownership label 和不可变 identity，不能信任子进程可写的名称或 ID。
- 数据库 lease claim 不等于 Agent 已经开始；`run.started` 与 start/resume 状态转换必须在 durable runtime 清理门禁通过后由当前 attempt fence 提交。门禁未决时应保留原运行模式和 lease，不能把可恢复的 start、review resume、取消或关闭竞态提前改写成错误终态。
- Conversation checkpoint 可以保留跨 run 的对话历史，但新 run 必须重置完成判断、预算计数等 run-scoped state；selected-input artifact context 只能来自当前 run，并与持久化对话历史分离，禁止沿用旧 run 的数据选择或终态。
- 跨 conversation Memory Plane 只属于本地安装级 `local-default` scope，必须与 checkpoint、conversation history、artifact、Skill 和当前 Run evidence 分离；读取、候选生成和 Agent-visible control Tool 使用独立且默认关闭的开关，向 LLM provider 发送正文前必须具有当前版本的显式 consent。
- Memory 正文只允许在 backend 逐 turn 瞬态解析，并作为低优先级、不可信数据进入模型视图；checkpoint、Run 请求、公共事件、control Tool 参数/结果和 frontend 持久化存储只能携带精确 item/version/hash identity。Purge 返回后还必须清理 frontend 当前页面的可控正文缓存，但不能宣称召回 provider 已接收或已获授权、正在发送的请求。
- 每个携带 Memory 正文的 Agent-level provider attempt 都必须通过短事务 pre-dispatch 门禁，复验 consent、使用开关、精确 identity、撤销/清除/suppression 状态及单调 disclosure epoch；revoke/purge 必须推进 epoch，使尚未授权的 attempt 和 Agent retry fail-closed。不得跨 provider 调用持有应用数据库事务或连接；成功 preflight 是该 attempt 已授权在途的线性化边界。
- Memory correction 必须追加不可变版本；forget/revoke 停止后续授权与未来检索；purge 删除正文和派生明文，并同时保留无正文的内容指纹与来源 message identity 摘要 suppression；任何自动 proposal/candidate 必须在读取旧消息正文前拒绝被抑制来源，且与 purge 通过同一持久化锁线性化并持锁到新版本提交，不能在 tombstone 检查后等待 purge、再通过拼接其他消息改变整体指纹后写入。Memory control Tool 必须按 run/attempt/canonical tool call/request hash 持久化 identity-only 幂等事实；任何 control Tool 发现 attempt/lease fence 丢失时必须终止当前 Agent 执行，不得降级为可重试 ToolMessage；Agent 不得自主检索 scientific observation，科学历史只能由用户显式 selected 且不能成为当前科学证据或 artifact 权限。
- 科研原型的 Frontend 只提供一个跨会话记忆总开关；开启后普通消息自动使用 `default`，关闭或服务不可用时自动使用 `off`，不在日常输入区暴露三门禁或 `selected` 精确版本模式。Agent 可以从用户明确表达的稳定偏好、用户事实和项目背景中主动提出候选，也可以从明确撤销或替换语义中请求 forget，不依赖“记住”或“忘记”关键词；候选每个 Run 最多一条且必须经用户确认。候选按引用的用户消息完整保存，正文及其 hash 必须保留原始 Unicode 与空白，规范化值只能用于 fingerprint 去重或抑制；只有整条消息主要表达一项单一、可独立复用的长期信息时才能提议。长期信息与当前任务、临时条件或科学内容混合，以及一次性要求、敏感推断、当前科学结论和普通闲聊均不得主动记忆。候选与遗忘确认就近显示在会话时间线；确认前必须可以查看完整来源原文并同时提供采用和拒绝，拒绝须清除候选正文并保留抑制身份，避免旧对话再次产生同一候选；处理中和失败状态必须按精确 memory item identity 显示在对应卡片。纠正、其他永久清除和技术身份保留在低频管理区。
- 取消先作为 PostgreSQL 中的命令事实提交，再由有效 owner 传播并确认资源收尾；非 owner 不得在有效 lease 存续时抢先写入 cancelled 终态。审核决定也必须按单一权威事实原子解决，不能留下相互冲突的 resolved 事件。
- PostgreSQL 中的类型化持久化事件是 frontend 恢复和权威状态投影的事实源；SSE 断开不得隐式取消 run，瞬态增量也不得驱动不可恢复的产品状态。
- Run、task 与 capability 的公共失败契约只允许暴露稳定 `error_code`、受控摘要和必要关联身份；原始异常、provider 返回、宿主路径、凭据和 capability 子进程任意输出只能进入服务端诊断日志。由可信 Local Docker runtime 独立采集的公开执行转录可以通过类型化事件展示容器逻辑 command、exit code 和有界 stdout/stderr，但必须显式标记 `redacted`、`truncated` 与编码状态，且不得包含宿主绝对路径、环境变量值、凭据或 backend 控制命令。
- Frontend projector 只有在事件通过版本化 schema、run/conversation identity 与连续 sequence 校验后才能推进持久化游标；gap、identity 冲突和非法事件必须停止当前投影，瞬态事件不得推进游标。
- Run 终态事件必须是该 run 的最后一个持久化事件；所有公共事件 payload 都必须先通过版本化契约校验，事件 sequence 跨端传输时必须保持无精度损失。
- Artifact 上传、解析、预览和下载必须经过 conversation ownership 与 workspace 边界校验；下载应从已经校验并固定的文件句柄流式返回，不能在校验后重新按路径打开；Agent-facing 参数只传稳定 `artifact_id` 句柄，由 backend 执行适配层恢复并复验权威 ArtifactRef；模型上下文、Tool 结果、最终回复和公共 API 只暴露稳定引用、有界 metadata 与领域摘要，不得暴露 workspace URI 或宿主路径。
- Capability 输出先进入 invocation-scoped 非权威空间，容器仅能写当前 invocation 并受文件数、单文件和总字节边界约束；只有当前 attempt fence 内的生命周期事务可以登记为权威 artifact，禁止全 workspace 差集或跨 attempt 残片发布。
- 探索性分析的每次修复尝试必须使用独立输出目录，只有成功尝试可以进入结果 Artifact 集合；调用时必须声明 backend 支持的类型化验收目标，局部事实不能替代目标验收；结果清单必须分级并对账同语义 cluster 产物的完整 ID、逐簇 count 和 proportion 映射，结构可读不等于科学结论已验证，未知文件只能作为非权威草稿。
- Conversation 对应顶层 checkpoint thread；compiled root graph 使用 LangGraph 根 namespace，嵌套复合能力使用框架管理的 namespace，不得把顶层自定义 `checkpoint_ns` 当作能力隔离保证。同一 thread 可承载多个顺序 run，恢复时必须对账 checkpoint state 的 run identity 与 review anchor 后再选择 start、resume 或 continue，不能把旧 run checkpoint 当作当前 run 已启动的证明。
- Checkpoint retention 只在 run 终态宽限后执行，必须保护最新恢复点及已声明的审核/工作流锚点；孤儿清理只能处理本次 prune 的候选版本，不得对活跃 namespace 做全局扫除。
- 数据库日志不得输出原始 DSN、用户信息、密码或可能携带凭据的 query 参数。
- 领域 Tool 与内部引擎代码只依赖稳定的 LLM 角色 alias，不得直接选择 provider/model、读取模型凭据或构造供应商客户端；这些职责统一归属于组合根与 LLM Factory。
- LLM alias 必须声明其最低能力要求并在启动期完成校验；不得保留或新增绕过统一 Factory 的旧模型构造入口。
- API 进程启动只校验既有 schema，数据库 migration 必须由显式管理入口执行；本地服务默认仅监听 loopback，扩大访问范围前必须同步补齐鉴权与来源边界。
- 公共列表 API 的过滤、稳定排序、offset 与 `limit + 1` 必须下推 PostgreSQL；不得先做固定数量截断再在内存分页，嵌套 `run_id` 查询必须校验 conversation 归属。
- 每个 conversation workspace 的生命周期必须长于其执行容器；容器只能由 Local Docker Backend 创建、识别和回收，不得附着到未经 profile 验证的外部容器。
- Docker runtime 默认禁止网络，并使用不可变 image identity、直接 argv 执行、降权用户、只读根文件系统和明确资源边界；只有显式 Tool policy 与 profile 同时允许时才能开放 shell 或网络能力。
- Runtime 的完成、状态与授权判断必须来自不可信代码无法伪造的控制面；stdout、stderr 和 artifact 只属于输出数据面，不得兼任可信控制信号。
- 宿主 secret 不得下发到执行容器，runtime metadata 不得暴露宿主绝对路径或环境变量值；时间、进程、输入输出和文件传输必须有硬上限。
- Docker 执行无论成功、失败、超时或取消都必须回收本次派生进程；阶段验证必须覆盖宿主 Docker CLI 与容器内进程两侧的收尾，并确认 conversation workspace 可在容器替换后继续使用。

## 变更与验证原则

- GitHub 分支 `archive/pre-agent-loop-refactor` 是本轮全新重构前的只读基线；后续合并、发布或分支清理不得删除、强制推送或移动该分支。
- 保持变更范围最小且与当前阶段一致；机械迁移与有意行为变化应拆分，以便审查和回归定位。
- 先检查工作区现状，保留用户已有修改；不得覆盖、清理或重写无关变更。
- 验证强度应与风险和阶段门槛匹配，优先验证受影响边界、核心能力、失败路径和恢复路径。
- 每项阶段证据都应可复查，至少记录验证对象、所用方式、结果和仍未覆盖的限制。
- 最终产品闭环应至少保留一条不 mock HTTP 的浏览器测试，连接真实 React、FastAPI、PostgreSQL、checkpointer 与 SSE；模型和科学 capability 可以使用确定性替身，避免把真实 LLM 波动作为回归门槛。
- Live E2E 默认必须清理临时 schema、workspace、服务和执行容器；只有显式 inspect 模式可以保留独立测试数据供人工查看，并且必须打印 frontend 地址、schema、workspace 与本地回执身份，不能写入或复用日常开发 schema。
- Playwright 默认使用其隔离管理的 Chromium；只有显式验证系统浏览器 channel 时才允许切换到系统 Chrome，避免测试进程污染用户浏览器状态或放大 macOS 沙箱启动故障。
- 核心科学行为验证必须区分确定性契约、受控模型替身与真实模型观察；前两者承担可复现门槛，真实模型结果不得成为唯一阻断依据，旧路径或旧符号不属于验证目标。
- 验证失败时，不得降低标准、跳过前置条件或把部分成功表述为完成；应保留证据并明确阻塞。
- 尚未落地的目录命令、类名、接口、测试入口或环境假设，不应提前固化为项目规则。

## AGENTS.md 自闭环维护

- 完成重要架构、组件、公共契约、迁移或验证流程后，在宣告完成前，必须判断该工作是否形成了新的、可复用且长期稳定的仓库规则。
- 若形成了长期规则，应在同一次变更中更新适用范围内的 `AGENTS.md`，并确保规则与 `ARCHITECTURE.md` 一致。
- 若未形成长期规则，应在交接说明或阶段证据中明确记录“已评估，无需更新 AGENTS.md”及简要原因。
- 可写入的内容包括稳定边界、持续适用的不变量、必要前置条件和可重复的验证约束。
- 不得写入一次性故障、临时进度、当前机器状态、短期命令、具体实现清单或仅服务单次任务的结论。
- 更新时保持中文主体、原则级和可执行；优先修改已有规则，避免重复、膨胀或与架构文档争夺事实来源。

## Agent 委派与集成责任

- 子 Agent 仅用于可隔离的范围、独立只读探索或独立评审；没有明确收益时由主 Agent 直接完成。
- 并行 Maker 必须拥有互不重叠的写入范围和清晰交付物，不得同时修改同一公共契约或架构基线。
- Checker 应基于准确快照独立审查并返回证据，不直接修改被审查内容。
- 主 Agent 始终保留架构判断、跨层集成、委派结果核验、最终验证和用户交付责任。
