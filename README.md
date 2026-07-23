# OmniCell-Agent

OmniCell-Agent 是一个面向单细胞 RNA 测序分析的本地科研 Agent 原型。系统以通用 LangGraph Agent Loop 为编排核心，让模型根据用户目标动态选择直接回复、只读 Tool、原子分析 Tool、完整 workflow Skill，或为复合目标创建显式计划。

项目保留并重新封装了两条核心领域能力：

- **Graph A**：单细胞数据分析，覆盖规划、受控执行、结果评估与重试；
- **Graph B**：深度细胞类型注释，覆盖 cluster 级注释、验证、评分、一致性审阅与报告。

当前代码采用 `backend + frontend` monorepo 布局，定位是研究生毕业设计所需的单机、可复现、可观察科研系统，而不是生产级多租户平台。

## 核心特性

- 通用的 LangGraph“推理 → Tool 执行 → 再推理”Agent Loop；
- Skill 与 Tool 正交注册，并支持按需加载 Skill 正文；
- Graph A/B 作为完整 workflow 保留，不向顶层 Agent 泄漏内部 DAG 节点；
- 五个可独立组合的原子分析 Tool，通过版本化 `ArtifactRef` 串联；
- 基于 Local Docker Backend 的隔离科学计算环境，默认禁止容器网络；
- PostgreSQL 持久化 conversation、run、event、artifact 与 LangGraph checkpoint；
- 通过逻辑角色 alias 切换模型和 OpenAI-compatible provider 的 LLM Factory；
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
    Skills["Skill Catalog"] -. "渐进加载" .-> Loop
    Loop --> Tools["Tool Registry"]
    Tools --> Atomic["原子 Tool"]
    Tools --> GraphA["Graph A workflow"]
    Tools --> GraphB["Graph B workflow"]
    Atomic --> Docker["Local Docker Backend"]
    GraphA --> Docker
    GraphB --> Docker
    Run <--> PG[("PostgreSQL")]
    Docker <--> Workspace["Conversation Workspace"]
```

控制状态和大型科学数据严格分离：PostgreSQL 保存生命周期、事件和 checkpoint，`.h5ad`、图像、表格及分析结果保存在 conversation workspace，并通过有界 `ArtifactRef` 进入 Agent 上下文。

完整架构、设计决策和阶段证据见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## Agent 如何选择能力

Agent 遵循“最小充分路径”，不会因为 conversation 中存在数据集就默认运行 Graph A 或 Graph B。

| 用户目标 | 默认路径 | 当前能力 |
| --- | --- | --- |
| 概念解释、结果解读 | 直接回复 | 不调用领域 Tool |
| 查看数据或 marker 摘要 | 只读 Tool | `inspect_single_cell_context`、`inspect_marker_contract` |
| 单个明确分析操作 | 原子 Tool | `run_qc_and_filter`、`run_normalize_log`、`run_pca_clustering`、`extract_marker_genes`、`generate_pca_scatter` |
| 完整单细胞分析 | workflow Skill | `single-cell-analysis` → `single_cell_analysis`（Graph A） |
| 正式细胞类型注释 | workflow Skill | `deep-cell-annotation` → `deep_cell_annotation`（Graph B） |
| 多个相互依赖的目标 | 显式计划 | 组合 Skill 与 Tool，并逐步更新 task 状态 |

原子 Tool 不依赖上一次容器调用的内存状态。会改变数据的操作总是生成新的 dataset artifact，后续步骤必须使用前一步返回的新 `ArtifactRef`。

## 仓库结构

```text
OmniCell-Agent/
├── backend/       Python 服务、Agent Loop、Graph A/B、持久化与测试
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
uv run --package omnicell-agent omnicell-db migrate
uv run --package omnicell-agent omnicell-db check
```

Migration 是显式管理步骤。API 启动时只校验应用 schema 与 checkpoint schema，不会隐式建表。

### 5. 一键启动前后端

完成上述首次初始化后，日常开发只需：

```bash
make dev
```

该命令会同时启动 FastAPI 和 Vite，并在任一服务退出或收到 `Ctrl+C` 时回收另一侧进程，避免遗留后台服务。

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
uv run --package omnicell-agent omnicell-api
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
4. 在 task、workflow、Tool 和事件面板中观察 Agent 的路由与执行过程；
5. 在 artifact 区预览或下载产生的数据集、marker 表、图像和报告；
6. 如果 run 进入人工审核状态，在 review 面板提交决定并恢复执行。

SSE 连接断开不会取消 run。页面重连后会从最后一个已确认 sequence 重放持久化事件，再继续跟随实时事件。

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

Live E2E 使用确定性的模型和 capability 替身验证产品链路，不把真实 LLM 波动作为回归门槛。

## 当前边界

- 这是本地科研原型，默认仅监听 loopback；项目尚未提供面向公网的鉴权与生产部署方案；
- 当前公开五个经过契约验证的原子 Tool。批次校正、轨迹推断、快速自动注释及空间分析仍是候选能力，尚未进入公共 Tool 面；
- `run_pca_clustering` 要求输入已经归一化，目前由 Skill 指引、Tool 提示和受验证的调用顺序约束，尚未通过 dataset provenance 做运行时 fail-closed 判定；
- 本轮是全新重构，只保留 Graph A/B 的核心科研能力，不兼容旧模块路径、旧 CLI、旧 API 或固定 DAG 入口；
- 真实模型测试属于观察性证据；确定性契约测试和受控模型替身承担可复现回归门槛。

## 延伸文档

- [架构设计与实施进度](ARCHITECTURE.md)
- [项目协作规则](AGENTS.md)
- [Backend 入口与数据库管理](backend/README.md)
- [Frontend 状态、传输与测试](frontend/README.md)
- [公共契约边界](contracts/README.md)
- [本地基础设施边界](infra/README.md)
