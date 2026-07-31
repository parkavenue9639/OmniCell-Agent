# OmniCell-Agent

<p align="center">
  <a href="README.md">English</a> ·
  <strong>简体中文</strong>
</p>

<p align="center">
  <img src="assets/omnicell-agent-icon.svg" alt="OmniCell-Agent 图标" width="160">
</p>

<p align="center">
  面向单细胞 RNA 测序分析的本地、可观察科研 Agent。
</p>

OmniCell-Agent 是一个面向单细胞 RNA 测序分析的本地科研 Agent 原型。系统以通用 LangGraph Agent Loop 为编排核心，让模型根据用户目标动态选择直接回复、检查 Tool、原子科学 Tool、需要 Skill 方法上下文的复合 Tool，或为多步目标创建显式计划。

项目保留了历史分析与细胞注释流程中已经验证的科学行为，但不再把它们按固定图分类暴露给 Agent。公共能力围绕科学目标、输入输出和前置条件组织：

- 数据检查、质量控制、归一化、聚类、marker 分析和科研可视化可以作为原子 Tool 独立组合；
- 细胞类型注释与开放式探索分析作为内聚的复合 Tool，分别保留验证、评分、报告，以及受控执行、评估和有限修复行为。

当前代码采用 `backend + frontend` monorepo 布局，定位是研究生毕业设计所需的单机、可复现、可观察科研系统，而不是生产级多租户平台。

> [!IMPORTANT]
> OmniCell-Agent 是单机科研原型，不是生产级多租户平台。项目优先保证科学行为、可复现性、架构清晰和本地完整演示。

## 为什么需要 OmniCell-Agent

很多分析助手容易把三件不同的事混在一起：代码成功运行、用户要求的分析目标已经完成，以及某个科学结论已经得到验证。OmniCell-Agent 对三者做了明确区分：

- 所有会改变科学数据状态的 Tool 都生成新的版本化 `ArtifactRef`；
- 科学状态来自真实输出和后置校验，不根据 Tool 名称、计划文本、stdout 或孤立的 AnnData metadata 推断；
- marker 阈值、cluster 覆盖和统计量范围属于硬输出契约，而不是尽力满足的提示；
- 当前 Run 的科学证据与历史消息、跨会话记忆相互隔离；
- Frontend 可以展示真实 Tool 行为、容器命令和有界 stdout/stderr，但只有 backend 验证的证据能够支撑当前数据结论；
- 未通过验证的注释、marker 覆盖缺口和证据冲突会失败关闭或进入人工复核。

## 核心特性

- 通用的 LangGraph“推理 → Tool 执行 → 再推理”Agent Loop；
- 可组合 Hook、run-scoped Skill 上下文和动态 Tool 可见性；
- Skill 与 Tool 正交注册，并支持按需加载 Skill 正文、reference 与 example；详细方法问答可以只加载 Skill 后直接回答，不强制执行分析 Tool；
- 公共能力按 `mode`、科学 `effect`、artifact 输入输出和前置条件描述；
- 五个可独立组合的原子分析 Tool，通过版本化 `ArtifactRef` 串联；
- 原子 Tool 记录实际 `executed/reused`、输入输出科学状态、参数、随机种子和后置校验，marker 阈值不做静默降级；
- 当前 Run 的有界科研证据独立于历史消息和 Memory；自然回复与 `finish_task` 共用 backend 权威渲染，模型自由文本不会把错误执行状态、数量或 Artifact 发布为科学终答；
- 探索性分析按 attempt 隔离输出，调用时声明类型化验收目标，并由 backend 生成科学级、结构级和草稿级结果清单及跨产物一致性证据；
- 计划步骤必须绑定当前 run 的真实 Tool 证据后才能完成；
- Tool 失败以稳定 `error_code`、`retryable` 和 `recovery_hint` 返回，阻断无变化的重复失败；
- 基于 Local Docker Backend 的隔离科学计算环境，默认禁止容器网络；
- PostgreSQL 持久化 conversation、run、event、artifact 与 LangGraph checkpoint；
- 可显式授权、纠正、遗忘和永久清除的本地跨会话 Memory Plane，默认关闭；
- 通过逻辑角色 alias 切换模型和 OpenAI-compatible provider 的 LLM Factory；
- 根据首条有效用户目标自动生成并持久化有辨识度的 conversation 标题；
- React conversation 界面，以及可重放的类型化 SSE 事件流；
- run 恢复、取消、人工审核、artifact 上传/预览/下载和事件诊断。

## 系统架构

```mermaid
flowchart LR
    User["用户"] <--> Web["React Frontend"]
    Web <-->|"REST + SSE"| API["FastAPI"]
    API --> Run["Run 生命周期"]
    Run --> Loop["通用 Agent Loop"]
    Loop <--> LLM["LLM Factory"]
    Hooks["Turn Hooks"] --> Loop
    Skills["Skill Catalog"] -. "渐进加载" .-> Hooks
    Loop --> Tools["Tool Registry"]
    Tools --> Inspect["检查 Tool"]
    Tools --> Atomic["原子科学 Tool"]
    Tools --> Composite["复合领域 Tool"]
    Atomic --> Docker["Local Docker Backend"]
    Composite --> Docker
    Run <--> PG[("PostgreSQL")]
    Docker <--> Workspace["Conversation Workspace"]
```

控制状态和大型科学数据严格分离：PostgreSQL 保存生命周期、事件和 checkpoint，`.h5ad`、图像、表格及分析结果保存在 conversation workspace，并通过有界 `ArtifactRef` 进入 Agent 上下文。

完整架构、设计决策和阶段证据见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 能力地图

### Skills

Skill 提供方法知识、选择规则、适用条件、统计假设和证据边界。加载 Skill 不会自动读取数据，也不会自动执行 Tool。

| Skill | 作用 |
| --- | --- |
| `single-cell-preprocessing` | 质量控制、归一化与聚类前表达矩阵准备 |
| `cluster-and-marker-analysis` | PCA/Leiden 聚类、marker 提取及解释边界 |
| `scientific-visualization` | 从已经验证的分析状态生成可复现图表 |
| `cell-type-annotation` | 基于 marker 的暂定注释、验证、评分、一致性检查和报告 |
| `exploratory-analysis` | 标准 Tool 无法充分覆盖时的受控开放式分析 |

### Tools

| 类型 | Tool | 科学目标 |
| --- | --- | --- |
| 检查 | `inspect_dataset` | 读取有界的物种、组织、疾病状态和任务元数据 |
| 检查 | `inspect_marker_table` | 校验并摘要已有 marker table |
| 原子 | `quality_control` | 过滤低质量细胞和低频基因 |
| 原子 | `normalize_expression` | 执行总量归一化与 `log1p` |
| 原子 | `cluster_cells` | 计算 PCA、邻接图和 Leiden cluster |
| 原子 | `find_marker_genes` | 生成满足阈值硬契约的 marker table |
| 原子 | `plot_pca_clusters` | 从已验证状态生成 PCA cluster 图 |
| 复合 | `annotate_cell_clusters` | 生成暂定 cluster 注释、复核信号和报告 |
| 复合 | `run_exploratory_analysis` | 按类型化验收目标执行非标准受控分析 |

内部确定性 Recipe 负责实现部分 Tool，但不作为额外的 Agent 可见能力暴露。

## Agent 如何选择能力

Agent 遵循“最小充分路径”，不会因为 conversation 中存在数据集就默认执行完整分析或注释流程。

| 用户目标 | 默认路径 | 当前能力 |
| --- | --- | --- |
| 稳定、低风险的概念解释 | 直接回复 | 不调用 Skill 或领域 Tool |
| 依赖领域操作定义、假设或证据边界的解释 | Skill → 直接回复 | 加载匹配领域 Skill，不以答复长短跳过，也不读取数据或强制调用领域 Tool |
| 查看数据或 marker 摘要 | 检查 Tool | `inspect_dataset`、`inspect_marker_table` |
| 单个明确分析操作 | 原子 Tool | `quality_control`、`normalize_expression`、`cluster_cells`、`find_marker_genes`、`plot_pca_clusters` |
| 正式细胞类型注释 | Skill + 复合 Tool | `cell-type-annotation` → `annotate_cell_clusters` |
| 非标准开放式分析 | Skill + 复合 Tool | `exploratory-analysis` → `run_exploratory_analysis` |
| 多个相互依赖的目标 | 显式计划 | 组合 Skill 与 Tool，并用 Tool 调用或 artifact 证据完成步骤 |

每条用户输入对应一个 Run，但普通问答不会额外创建 `agent_goal` Task。没有未完成显式计划时，Agent 返回非空最终文本即可完成 Run；Task 只在创建计划步骤或实际调用 Tool 时出现，`finish_task` 仅用于需要显式 evidence 或 limitations 的结构化完成。

原子 Tool 不依赖上一次容器调用的内存状态。会改变数据的操作总是生成新的 dataset artifact，后续步骤必须使用前一步返回的新 `ArtifactRef`。

“Tool 已运行”“科学目标已完成”和“某个事实已经验证”是三个不同层次。Frontend 可以展示真实命令和 stdout 供理解过程，但只有 backend 科学后置校验形成的当前 Run 证据可以支撑当前数据结论；注释验证失败、证据不支持或 marker 覆盖不完整时会明确要求人工复核。

## 仓库结构

```text
OmniCell-Agent/
├── backend/       Python 服务、Agent Loop、科学能力、持久化与测试
├── frontend/      React 界面、事件 projector 与浏览器测试
├── contracts/     OpenAPI 与事件 JSON Schema 公共契约
├── infra/         PostgreSQL Compose 与科学计算 worker 镜像
├── scripts/       契约生成、live E2E 与科研评估脚本
├── ARCHITECTURE.md
└── AGENTS.md
```

## 本地快速启动

### 1. 环境要求

- Python `>= 3.11`；
- [uv](https://docs.astral.sh/uv/)；
- Node.js `>= 22.13` 且 `< 25`；
- npm `11.x`；
- 可用的 Docker daemon。本项目本地开发默认使用已经运行的 OrbStack；
- 一个 OpenRouter 或 OneRouter 账号；它们是当前内置的两个 OpenAI-compatible provider。

下面的命令均从仓库根目录执行，除非特别说明。

### 2. 安装依赖并配置环境

```bash
uv sync --package omnicell-agent

cp .env.example .env

cd frontend
npm ci
cd ..
```

编辑 `.env`，至少配置一个模型 provider。默认 alias 使用 OpenRouter：

```dotenv
OPENROUTER_API_KEY="你的密钥"
OMNICELL_LLM_DEFAULT="openrouter/default"
```

如需使用 OneRouter：

```dotenv
ONEROUTER_API_KEY="你的密钥"
OMNICELL_LLM_DEFAULT="onerouter/default"
```

`agent_primary`、`annotation`、`validation`、`vision` 等角色可以在 `.env` 中分别覆盖；未设置的角色会回落到 `OMNICELL_LLM_DEFAULT`。不要提交包含真实密钥的 `.env`。

### 3. 启动 PostgreSQL 并构建 worker 镜像

```bash
docker compose -f infra/compose.yaml up -d postgres

docker build \
  -t omnicell-worker:latest \
  -f infra/docker/Dockerfile.worker \
  .
```

默认 PostgreSQL 地址为 `127.0.0.1:55432`，默认 worker image 为 `omnicell-worker:latest`。两者都可以通过环境变量替换。

### 4. 初始化数据库

```bash
make db-migrate
make db-check
```

Migration 是显式管理步骤。API 启动时只校验应用 schema 与 checkpoint schema，不会隐式建表。
这两个 Make 入口与 `make dev` 一样直接使用当前 checkout 的
`backend/src`，不依赖虚拟环境中 editable-install 的 `.pth` 状态。

### 5. 一键启动前后端

完成上述首次初始化后，日常开发只需：

```bash
make dev
```

该命令默认同时启动 FastAPI 和 Vite，并在任一由本次命令启动的服务退出或收到 `Ctrl+C` 时回收另一侧进程，避免遗留后台服务。如果配置端口上已经运行通过 liveness 识别、且监听进程属于当前仓库的 OmniCell API，命令会先精确停止该 backend，再启动新的前后端服务，确保代码更新生效；不会按端口宽泛杀进程。若端口被其他服务占用，或识别到的 OmniCell 监听进程不属于当前仓库，命令会明确拒绝停止并报错。

默认地址：

- Web：<http://127.0.0.1:5173>
- API：<http://127.0.0.1:8000>
- Swagger UI：<http://127.0.0.1:8000/api/v1/docs>
- Liveness：<http://127.0.0.1:8000/api/v1/health/live>
- Readiness：<http://127.0.0.1:8000/api/v1/health/ready>

Readiness 会分别检查应用数据库、PostgreSQL checkpointer 和 Docker execution backend。

### 6. 分别启动服务

需要分别调试进程时，在一个终端启动 backend：

```bash
PYTHONPATH=backend/src .venv/bin/python -m omnicell_agent.api.cli
```

在另一个终端启动 frontend：

```bash
cd frontend
npm run dev
```

访问 <http://127.0.0.1:5173>。Vite 默认将 `/api/v1` 代理到 `http://127.0.0.1:8000`；backend 使用其他地址时，可在启动 frontend 前设置 `OMNICELL_API_PROXY_TARGET`。

## 页面使用流程

1. 新建或选择一个 conversation；
2. 上传本次分析需要的 `.h5ad` dataset；
3. 在对话区描述目标，例如“先归一化并聚类，再提取 marker gene”；
4. 在主时间线中通过 Skill、Tool 和 Backend 卡片观察 Agent 的动作、过程与结果，并可在事件面板中进一步诊断；
5. 在 artifact 区预览或下载产生的数据集、marker 表、图像和报告；
6. 如果 run 进入人工审核状态，在 review 面板提交决定并恢复执行。

示例请求：

```text
比较不同 cluster 的 marker gene 之前，我应该检查哪些方法假设？
```

```text
请先归一化这个数据集，再对新产物做聚类，最后提取 marker gene。
```

```text
基于当前 marker table 生成暂定细胞类型注释，并明确标出需要人工复核的 cluster。
```

空 conversation 在提交首条有效目标后会由 backend 自动生成简洁标题；标题会同步到侧边栏与页面头部，并在刷新后从 PostgreSQL 恢复。标题模型失败不会影响 Run，系统会使用当前目标生成有界兜底标题。

SSE 连接断开不会取消 run。页面重连后会从最后一个已确认 sequence 重放持久化事件，再继续跟随实时事件。

### 跨会话记忆

跨会话记忆默认关闭。第一次使用时，在右侧“记忆”面板开启一次“跨会话记忆”，并确认相关内容可以发送给当前配置的 LLM。此后每条消息都会自动查找少量相关记忆，不需要为每个 Run 再选择模式。

日常使用直接通过自然语言完成：

- 表达“以后回答先给结论”这类长期偏好时，Agent 可以主动在当前时间线提出候选，点击“确认记住”后才会生效，不需要固定说“请记住”；
- 表达“以后不再使用刚才的回答偏好”这类明确撤销意图时，Agent 会定位目标并请求确认，不需要固定说“忘记”；
- “这次只用两句话”这类仅限当前任务的要求不会成为长期记忆；
- 候选会保留整条来源消息；如果长期偏好和“现在分析这个数据集”等当前任务写在同一条消息里，Agent 会跳过提议，建议在后续消息中单独表达长期偏好；
- 在右侧“记忆”面板可以手动新增、纠正、忘记或彻底删除，也可以随时关闭总开关。

Agent 每个 Run 最多提出一条候选，而且不会主动记住寒暄、敏感推断、凭据、患者信息、执行输出或当前数据集的科学结论。“忘记”会让未来对话停止使用该内容；“彻底删除”还会清除保存的正文，并防止 Agent 从旧对话重新学习它。已经发送给当前 LLM 的内容无法撤回。系统内部仍保留精确版本、来源、Run snapshot 和 identity-only 事件，但这些技术细节默认折叠，不影响普通使用；历史科学观察也不会被自动当成当前数据证据或扩大 artifact 权限。

## 常用环境变量

| 变量 | 作用 | 默认值 |
| --- | --- | --- |
| `OMNICELL_LLM_DEFAULT` | 默认 LLM alias target，格式为 `provider/model` | `openrouter/default`（`.env.example`） |
| `OMNICELL_LLM_<ROLE>` | 为特定 LLM 角色覆盖 target | 回落到默认 alias |
| `OMNICELL_POSTGRES_DSN` | 应用与 checkpointer 使用的 PostgreSQL | 本地 Compose DSN |
| `OMNICELL_WORKSPACE_ROOT` | conversation 数据与 artifact 根目录 | `data/conversations` |
| `OMNICELL_RUNTIME_IMAGE` | Local Docker Backend worker image | `omnicell-worker:latest` |
| `OMNICELL_API_HOST` | backend 监听地址 | `127.0.0.1` |
| `OMNICELL_API_PORT` | backend 监听端口 | `8000` |
| `OMNICELL_API_PROXY_TARGET` | frontend 开发代理目标 | `http://127.0.0.1:8000` |

完整示例见 [.env.example](.env.example)。

## 验证

### Backend 默认测试

```bash
uv run --package omnicell-agent \
  pytest backend/tests \
  -m "not postgres and not docker and not live_llm"
```

### PostgreSQL 集成测试

```bash
OMNICELL_TEST_POSTGRES_DSN="postgresql://omnicell:omnicell_dev@127.0.0.1:55432/omnicell" \
  uv run --package omnicell-agent pytest backend/tests -m postgres
```

### Local Docker Backend 测试

```bash
OMNICELL_RUN_DOCKER_TESTS=1 \
  uv run --package omnicell-agent pytest backend/tests -m docker
```

### Frontend 检查与浏览器测试

```bash
cd frontend
npm run contracts:check
npm run typecheck
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

Playwright 默认使用隔离管理的 Chromium，不启动或污染日常使用的系统 Chrome。只有明确要验证系统 channel 时，才设置 `OMNICELL_PLAYWRIGHT_BROWSER_CHANNEL=chrome`。

连接真实 React、FastAPI、PostgreSQL、checkpointer 与 SSE 的确定性产品闭环测试：

```bash
cd frontend
OMNICELL_TEST_POSTGRES_DSN="postgresql://omnicell:omnicell_dev@127.0.0.1:55432/omnicell" \
  npm run test:e2e:live
```

Live E2E 使用确定性的模型和 Tool handler 替身验证产品链路，不把真实 LLM 波动作为回归门槛。

需要在测试结束后打开页面检查刚生成的 conversation、事件和 artifact 时，使用可观察模式：

```bash
OMNICELL_TEST_POSTGRES_DSN="postgresql://omnicell:omnicell_dev@127.0.0.1:55432/omnicell" \
  make e2e-live-inspect
```

测试通过后命令会保持 frontend 与 backend 运行，并打印可直接打开的 frontend 地址、独立 PostgreSQL schema、workspace 和 `inspection.json` 回执路径。按 `Ctrl+C` 会停止本次服务，但保留 schema 与 workspace；默认 `test:e2e:live` 仍会自动清理全部临时资源。保留的数据仅用于本机人工检查，不会写入日常开发 schema。

## 当前边界

- 这是本地科研原型，默认仅监听 loopback；项目尚未提供面向公网的鉴权与生产部署方案；
- 当前公开五个经过契约验证的原子 Tool。批次校正、轨迹推断、快速自动注释及空间分析仍是候选能力，尚未进入公共 Tool 面；
- `cluster_cells` 和 marker 分析要求输入具有经过矩阵特征与 lineage 共同确认的 log-expression 状态；孤立的 AnnData metadata 不能解锁执行，状态不明时运行时 fail-closed；
- 本轮是全新重构，只保留历史流程中已验证的核心科研行为，不兼容旧模块路径、旧 CLI、旧 API、旧能力名或固定 DAG 入口；
- 真实模型测试属于观察性证据；确定性契约测试和受控模型替身承担可复现回归门槛。

## 延伸文档

- [架构设计与实施进度](ARCHITECTURE.md)
- [项目协作规则](AGENTS.md)
- [Backend 入口与数据库管理](backend/README.md)
- [Frontend 状态、传输与测试](frontend/README.md)
- [公共契约边界](contracts/README.md)
- [本地基础设施边界](infra/README.md)
- [完整端到端验证指南](docs/e2e_guide.md)
