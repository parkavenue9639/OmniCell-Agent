# OmniCell-Agent 项目工作规则

## 适用范围与架构基线

- 本文件适用于整个仓库；更深层目录若新增 `AGENTS.md`，可以补充局部规则，但不得削弱本文件约束。
- 开始重要架构、实现、迁移或验证工作前，必须先阅读根目录 `ARCHITECTURE.md` 及相关目录规则。
- `ARCHITECTURE.md` 是本轮重构唯一的架构与进度基线。发现实现计划与其冲突时，先澄清或更新架构决策，再修改代码。
- 不在本文件复制完整架构、阶段表或临时状态；职责边界、实施顺序、完成门槛和进度以 `ARCHITECTURE.md` 为准。
- 新增或更新架构、设计、决策和进度类文档时，必须以中文为主体，仅保留必要的英文协议名、路径、文件名和代码标识符。
- 本项目是面向研究生毕业设计的单机科研原型；优先保证科学行为、可复现性、结构清晰和本地演示，不主动引入多租户、高可用、分布式运维或其他生产平台复杂度。

## 核心领域不变量

- Graph A 与 Graph B 是必须保留的一等领域能力，不是待删除的遗留实现。
- Agent Loop 负责通用编排与完成判断，Graph A/B 负责领域工作流，执行环境负责隔离运行；三者职责不得相互侵入。
- Agent Loop 必须按目标选择最小充分路径：上下文足够时直接回复，局部读取或校验使用 Tool，完整领域目标使用 workflow Skill，只有包含多个相互依赖且可分别验证的步骤时才创建显式计划；不得因为存在数据集就默认运行 Graph A/B，也不得为简单任务形式化建计划。
- 两者应通过稳定、类型明确的 Skill 与 Tool 边界供 Agent 调用；上层不得依赖工作流内部节点拓扑。
- Skill 与 Tool 必须正交注册：Skill 可以引用多个 Tool，同一 Tool 可以被多个 Skill 复用，未被 Skill 引用的独立 Tool 也允许存在；启动期只校验名称、类型契约和 Skill 引用的 Tool 确实已注册，不建立唯一所有权或反向绑定。
- 初始 Agent 上下文只暴露 Skill 摘要和 Tool 行为提示；Skill 正文、reference 与 example 必须按需渐进加载。每次渐进加载必须形成类型化、可重放的 started/completed/failed 事件，公共事件只暴露 Skill、资源层级、受控用途和结果，不复制正文、宿主路径或模型思维链。每个模型可见 Tool 都必须提供明确的调用与禁用条件，但提示不能替代类型校验、Tool policy、artifact ownership 或执行隔离。
- 能改变科学数据状态的原子 Tool 必须生成新的版本化 ArtifactRef，不得原位覆盖输入，也不得依赖跨 Tool 调用残留的容器局部状态；只有具备明确科学语义、输入前置条件、输出后置条件和代表性验证的能力才能进入公共 Tool 面。
- 本轮是全新重构，不保留旧模块路径、旧类名、旧函数、旧 CLI、旧 API 或固定 DAG 入口兼容；需要延续的只有 Graph A/B 核心领域能力与经验证的科学行为。
- 目录移动、运行环境替换、调用方式变化与科学行为变化必须明确区分，避免在同一项工作中隐式混合。
- 任何可能改变 Graph A/B 行为、输出、反馈循环、路由或并发语义的变更，都必须明确说明意图，并用代表性基线或契约证据验证。
- 未经架构决策和显式验证，不得以重构、简化或接入 Agent Loop 为由削弱 Graph A/B 的既有能力。

## 实施顺序与进度证据

- 按 `ARCHITECTURE.md` 规定的阶段顺序实施；可以提前进行只读探索或无依赖准备，但不得绕过未完成的前置条件。
- 开始、阻塞或完成一个阶段时，同步维护 `ARCHITECTURE.md` 的进度台账。
- 阶段完成必须绑定可复查证据；门槛未全部通过时，不得标记完成或用后续阶段结果替代当前阶段证据。
- 新的架构决策应先写入 `ARCHITECTURE.md`，再让实现依赖该决策。
- 交接时明确区分已完成、已验证、仅推断、仍阻塞和后续工作，避免把代码存在等同于阶段完成。

## Monorepo 高层边界

- `backend` 负责权威的 conversation/run 生命周期、Agent 与领域工作流执行、执行环境、模型选择、持久化、artifact 和事件流。
- `frontend` 负责用户交互与服务端事件的确定性投影；不得从本地 UI 状态推断权威 run 终态，也不得依赖 backend 内部状态结构。
- `contracts` 负责 backend 与 frontend 共用的版本化公共契约；契约变化必须同时验证两端兼容性。
- Frontend 公共 DTO 必须从 `contracts` 单向生成并通过漂移检查，不得另建手写的并行契约或让生成检查直接覆盖工作区。
- `infra` 负责本地拓扑、运行依赖和环境边界；不得承载产品领域逻辑。
- 跨层协作应通过稳定契约和资源引用完成，不得泄漏数据库行结构、LangGraph 内部状态、frontend store 或工作流内部节点。
- 大型科学数据、生成文件和执行输出属于 workspace/artifact 层，不进入控制状态、checkpoint 或事件 payload；checkpoint 写前约束必须覆盖状态、metadata 和中间 writes 的完整 saver 写入面。
- 应用表与 LangGraph checkpoint 表必须分别由项目 migration 和 saver migration 唯一管理，且使用不同 schema；禁止双重建表、同名 schema 或跨边界修改。
- 单个 run 的事件顺序必须由数据库原子分配；run 状态和对应事件同应用事务提交，不得把独立连接上的 checkpoint 写入宣称为同一原子事务。
- 顶层 Agent 只能通过受支持的 run 生命周期入口执行；不得直接调用 compiled graph 绕过 run 创建、事件、终态、artifact 登记、取消传播或资源收尾。
- 多 worker 执行必须以持久化 lease 与 attempt fence 约束所有权敏感写入；heartbeat 失效时当前执行应 fail-closed，旧 owner 不得在新 owner 接管后继续提交事件或终态。
- 正式 Agent 组合路径调用普通同步 capability 时必须使用可终止的隔离执行边界，不得回退到线程执行并把 Future 取消视为底层工作已停止；测试专用的进程内替身必须显式声明其不提供硬终止保证。
- 隔离执行的存活续期只能由已成功提交的数据库 claim/heartbeat 驱动；取消、续期失效或父进程失联后，必须确认 worker 进程组及其精确 owned runtime 已回收，再释放 lease 或写入终态。跨进程 runtime claim 必须位于容器不可见的 backend 控制目录，且仅作为定位线索；回收前必须复验容器 ownership label 和不可变 identity，不能信任子进程可写的名称或 ID。
- 数据库 lease claim 不等于 Agent 已经开始；`run.started` 与 start/resume 状态转换必须在 durable runtime 清理门禁通过后由当前 attempt fence 提交。门禁未决时应保留原运行模式和 lease，不能把可恢复的 start、review resume、取消或关闭竞态提前改写成错误终态。
- Conversation checkpoint 可以保留跨 run 的对话历史，但新 run 必须重置完成判断、预算计数等 run-scoped state；selected-input artifact context 只能来自当前 run，并与持久化对话历史分离，禁止沿用旧 run 的数据选择或终态。
- 取消先作为 PostgreSQL 中的命令事实提交，再由有效 owner 传播并确认资源收尾；非 owner 不得在有效 lease 存续时抢先写入 cancelled 终态。审核决定也必须按单一权威事实原子解决，不能留下相互冲突的 resolved 事件。
- PostgreSQL 中的类型化持久化事件是 frontend 恢复和权威状态投影的事实源；SSE 断开不得隐式取消 run，瞬态增量也不得驱动不可恢复的产品状态。
- Run、task 与 capability 的公共失败契约只允许暴露稳定 `error_code`、受控摘要和必要关联身份；原始异常、provider 返回、宿主路径、凭据和 capability 子进程任意输出只能进入服务端诊断日志。由可信 Local Docker runtime 独立采集的公开执行转录可以通过类型化事件展示容器逻辑 command、exit code 和有界 stdout/stderr，但必须显式标记 `redacted`、`truncated` 与编码状态，且不得包含宿主绝对路径、环境变量值、凭据或 backend 控制命令。
- Frontend projector 只有在事件通过版本化 schema、run/conversation identity 与连续 sequence 校验后才能推进持久化游标；gap、identity 冲突和非法事件必须停止当前投影，瞬态事件不得推进游标。
- Run 终态事件必须是该 run 的最后一个持久化事件；所有公共事件 payload 都必须先通过版本化契约校验，事件 sequence 跨端传输时必须保持无精度损失。
- Artifact 上传、解析、预览和下载必须经过 conversation ownership 与 workspace 边界校验；下载应从已经校验并固定的文件句柄流式返回，不能在校验后重新按路径打开；公共 API 只暴露稳定引用和有界 metadata，不得暴露 workspace URI 或宿主路径。
- Capability 输出先进入 invocation-scoped 非权威空间，容器仅能写当前 invocation 并受文件数、单文件和总字节边界约束；只有当前 attempt fence 内的生命周期事务可以登记为权威 artifact，禁止全 workspace 差集或跨 attempt 残片发布。
- Conversation 对应顶层 checkpoint thread；compiled root graph 使用 LangGraph 根 namespace，嵌套工作流使用框架管理的 namespace，不得把顶层自定义 `checkpoint_ns` 当作能力隔离保证。同一 thread 可承载多个顺序 run，恢复时必须对账 checkpoint state 的 run identity 与 review anchor 后再选择 start、resume 或 continue，不能把旧 run checkpoint 当作当前 run 已启动的证明。
- Checkpoint retention 只在 run 终态宽限后执行，必须保护最新恢复点及已声明的审核/工作流锚点；孤儿清理只能处理本次 prune 的候选版本，不得对活跃 namespace 做全局扫除。
- 数据库日志不得输出原始 DSN、用户信息、密码或可能携带凭据的 query 参数。
- 领域与工作流代码只依赖稳定的 LLM 角色 alias，不得直接选择 provider/model、读取模型凭据或构造供应商客户端；这些职责统一归属于组合根与 LLM Factory。
- LLM alias 必须声明其最低能力要求并在启动期完成校验；不得保留或新增绕过统一 Factory 的旧模型构造入口。
- API 进程启动只校验既有 schema，数据库 migration 必须由显式管理入口执行；本地服务默认仅监听 loopback，扩大访问范围前必须同步补齐鉴权与来源边界。
- 公共列表 API 的过滤、稳定排序、offset 与 `limit + 1` 必须下推 PostgreSQL；不得先做固定数量截断再在内存分页，嵌套 `run_id` 查询必须校验 conversation 归属。
- 每个 conversation workspace 的生命周期必须长于其执行容器；容器只能由 Local Docker Backend 创建、识别和回收，不得附着到未经 profile 验证的外部容器。
- Docker runtime 默认禁止网络，并使用不可变 image identity、直接 argv 执行、降权用户、只读根文件系统和明确资源边界；只有显式 Tool policy 与 profile 同时允许时才能开放 shell 或网络能力。
- Runtime 的完成、状态与授权判断必须来自不可信代码无法伪造的控制面；stdout、stderr 和 artifact 只属于输出数据面，不得兼任可信控制信号。
- 宿主 secret 不得下发到执行容器，runtime metadata 不得暴露宿主绝对路径或环境变量值；时间、进程、输入输出和文件传输必须有硬上限。
- Docker 执行无论成功、失败、超时或取消都必须回收本次派生进程；阶段验证必须覆盖宿主 Docker CLI 与容器内进程两侧的收尾，并确认 conversation workspace 可在容器替换后继续使用。

## 变更与验证原则

- 保持变更范围最小且与当前阶段一致；机械迁移与有意行为变化应拆分，以便审查和回归定位。
- 先检查工作区现状，保留用户已有修改；不得覆盖、清理或重写无关变更。
- 验证强度应与风险和阶段门槛匹配，优先验证受影响边界、核心能力、失败路径和恢复路径。
- 每项阶段证据都应可复查，至少记录验证对象、所用方式、结果和仍未覆盖的限制。
- 最终产品闭环应至少保留一条不 mock HTTP 的浏览器测试，连接真实 React、FastAPI、PostgreSQL、checkpointer 与 SSE；模型和科学 capability 可以使用确定性替身，避免把真实 LLM 波动作为回归门槛。
- Playwright 默认使用其隔离管理的 Chromium；只有显式验证系统浏览器 channel 时才允许切换到系统 Chrome，避免测试进程污染用户浏览器状态或放大 macOS 沙箱启动故障。
- Graph A/B 核心能力验证必须区分确定性契约、受控模型替身与真实模型观察；前两者承担可复现门槛，真实模型结果不得成为唯一阻断依据，旧路径或旧符号不属于验证目标。
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
