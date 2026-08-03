# OmniCell-Agent

<p align="center">
  <strong>English</strong> ·
  <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <img src="assets/omnicell-agent-icon.svg" alt="OmniCell-Agent icon" width="160">
</p>

<p align="center">
  A local, observable research agent for single-cell RNA sequencing analysis.
</p>

OmniCell-Agent is a graduate research prototype that combines a general
LangGraph agent loop with reproducible single-cell analysis tools. Given a
natural-language request, the agent chooses the smallest sufficient path:
answer directly, load a domain Skill, inspect an artifact, invoke one scientific
Tool, or create an explicit plan for a genuinely multi-step objective.

The project preserves the validated scientific behavior of its earlier
analysis and annotation workflows without exposing their historical DAG names
or internal node topology. Public capabilities are organized by scientific
goal, typed inputs and outputs, preconditions, and verification criteria.

> [!IMPORTANT]
> OmniCell-Agent is a single-machine research prototype, not a production
> multi-tenant platform. It prioritizes scientific behavior, reproducibility,
> clear architecture, and local demonstration.

## Why OmniCell-Agent?

Many analysis assistants blur three different claims: code ran successfully,
the requested analysis completed, and a scientific statement was verified.
OmniCell-Agent keeps them separate.

- Every state-changing Tool produces a new versioned `ArtifactRef`.
- Scientific state is derived from the actual output and postconditions, not
  from a Tool name, plan text, stdout, or isolated AnnData metadata.
- Marker thresholds, cluster coverage, and statistical ranges are hard output
  contracts rather than best-effort hints.
- The current Run's scientific evidence is isolated from conversation history
  and cross-session memory.
- The frontend can show real Tool activity, container commands, and bounded
  stdout/stderr, while only backend-verified evidence supports scientific
  conclusions.
- Unverified annotations, incomplete marker coverage, and conflicting evidence
  fail closed or require manual review.

## Highlights

- General LangGraph reasoning → Tool execution → reasoning loop.
- Dynamic direct-answer, Skill-only, single-Tool, composite-Tool, and explicit
  plan routes.
- Orthogonal Skill and Tool registration with progressive loading of Skill
  bodies, references, and examples.
- Two inspection Tools, five atomic scientific Tools, and two composite domain
  Tools.
- Versioned artifacts and immutable handoff between scientific steps.
- Local Docker execution backend with network disabled by default.
- PostgreSQL-backed conversations, Runs, events, artifacts, and LangGraph
  checkpoints.
- Typed, replayable SSE events for history recovery and live observation.
- React conversation UI with Skill, Tool, Backend, review, and artifact
  rendering.
- Opt-in local cross-session memory with proposal, approval, correction,
  forgetting, and permanent purge.
- Role-based LLM aliases over OpenAI-compatible providers.
- Recoverable Runs, cancellation, human review, artifact upload/preview/download,
  and deterministic end-to-end tests.

## Capability Map

### Skills

Skills provide method knowledge, selection guidance, assumptions, and evidence
boundaries. Loading a Skill does not automatically read a dataset or execute a
Tool.

| Skill | Purpose |
| --- | --- |
| `single-cell-preprocessing` | Quality control, normalization, and preparation for clustering |
| `cluster-and-marker-analysis` | PCA/Leiden clustering, marker extraction, and interpretation boundaries |
| `scientific-visualization` | Reproducible plots from already verified analysis state |
| `cell-type-annotation` | Marker-based provisional annotation, validation, scoring, consistency checks, and reporting |
| `exploratory-analysis` | Controlled analysis for goals not sufficiently covered by standard Tools |

### Tools

| Type | Tool | Scientific goal |
| --- | --- | --- |
| Inspect | `inspect_dataset` | Read bounded species, tissue, disease-state, and task metadata |
| Inspect | `inspect_marker_table` | Validate and summarize an existing marker table |
| Atomic | `quality_control` | Filter low-quality cells and low-frequency genes |
| Atomic | `normalize_expression` | Apply total-count normalization and `log1p` |
| Atomic | `cluster_cells` | Compute PCA, neighbors, and Leiden clusters |
| Atomic | `find_marker_genes` | Produce a threshold-validated marker table |
| Atomic | `plot_pca_clusters` | Render a PCA cluster plot from verified state |
| Composite | `annotate_cell_clusters` | Produce provisional cluster annotations, review signals, and a report |
| Composite | `run_exploratory_analysis` | Execute a bounded non-standard analysis with typed acceptance criteria |

Internal deterministic Recipes implement parts of these Tools. Recipes are not
additional Agent-facing capabilities.

## How the Agent Routes a Request

| User intent | Default route |
| --- | --- |
| Stable, low-risk conceptual question | Direct answer |
| Method question that depends on scientific assumptions or evidence boundaries | Load the matching Skill, then answer |
| Inspect existing dataset or marker metadata | Inspection Tool |
| One explicit scientific operation | Atomic Tool |
| Full marker-based cell-type annotation | `cell-type-annotation` Skill + composite Tool |
| Non-standard analysis not covered by registered Tools | `exploratory-analysis` Skill + composite Tool |
| Multiple dependent and independently verifiable goals | Explicit plan combining Skills and Tools |

Every user message creates a Run, but ordinary questions do not create a
synthetic root task. Tasks represent explicit plan steps or actual capability
calls. The presence of a dataset never implies that the agent should run a full
pipeline.

## Architecture

```mermaid
flowchart LR
    User["User"] <--> Web["React Frontend"]
    Web <-->|"REST + SSE"| API["FastAPI"]
    API --> Run["Run Lifecycle"]
    Run --> Loop["General Agent Loop"]
    Loop <--> LLM["LLM Factory"]
    Hooks["Turn Hooks"] --> Loop
    Skills["Skill Catalog"] -. "Progressive loading" .-> Hooks
    Loop --> Tools["Tool Registry"]
    Tools --> Inspect["Inspection Tools"]
    Tools --> Atomic["Atomic Scientific Tools"]
    Tools --> Composite["Composite Domain Tools"]
    Atomic --> Docker["Local Docker Backend"]
    Composite --> Docker
    Run <--> PG[("PostgreSQL")]
    Docker <--> Workspace["Conversation Workspace"]
```

Control state and scientific data are deliberately separated. PostgreSQL stores
lifecycle metadata, typed events, and checkpoints. `.h5ad` files, images,
tables, and reports remain in the conversation workspace and enter model
context only through bounded artifact identities and summaries.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the authoritative architecture,
decisions, implementation phases, and verification evidence.

## Repository Layout

```text
OmniCell-Agent/
├── backend/       Python API, Agent Loop, capabilities, runtime, persistence, tests
├── frontend/      React UI, event projector, component and browser tests
├── contracts/     Versioned OpenAPI and event JSON Schema snapshots
├── infra/         Local PostgreSQL topology and scientific worker image
├── scripts/       Development, contract, live E2E, and evaluation utilities
├── ARCHITECTURE.md
├── AGENTS.md
└── README.zh-CN.md
```

## Quick Start

All commands below run from the repository root unless noted otherwise.

### 1. Requirements

- Python `>= 3.11`
- [uv](https://docs.astral.sh/uv/)
- Node.js `>= 22.13` and `< 25`
- npm `11.x`
- A running Docker daemon; local development currently uses OrbStack
- An OpenRouter or OneRouter account for the built-in OpenAI-compatible
  provider configuration

### 2. Install dependencies

```bash
uv sync --package omnicell-agent

cp .env.example .env

cd frontend
npm ci
cd ..
```

### 3. Configure an LLM provider

The default aliases use OpenRouter:

```dotenv
OPENROUTER_API_KEY="your-api-key"
OMNICELL_LLM_DEFAULT="openrouter/default"
```

To use OneRouter instead:

```dotenv
ONEROUTER_API_KEY="your-api-key"
OMNICELL_LLM_DEFAULT="onerouter/default"
```

Roles such as `agent_primary`, `annotation`, `validation`, `summary`, and
`vision` can be overridden independently in `.env`. Unset roles fall back to
`OMNICELL_LLM_DEFAULT`. Never commit a real `.env` file or API key.

### 4. Start PostgreSQL and build the worker image

```bash
docker compose -f infra/compose.yaml up -d postgres

docker build \
  -t omnicell-worker:latest \
  -f infra/docker/Dockerfile.worker \
  .
```

The default PostgreSQL port is `55432`; the default worker image is
`omnicell-worker:latest`.

### 5. Initialize and verify the database

```bash
make db-migrate
make db-check
```

Migrations are always explicit. API startup verifies the application and
checkpoint schemas but never creates or upgrades them implicitly.

### 6. Start the backend and frontend

```bash
make dev
```

`make dev` starts FastAPI and Vite together. If an OmniCell backend belonging to
this checkout already owns the configured port, it is stopped precisely and
restarted. The command refuses to kill an unrelated process that happens to use
the same port.

Default endpoints:

- Web UI: <http://127.0.0.1:5173>
- API: <http://127.0.0.1:8000>
- Swagger UI: <http://127.0.0.1:8000/api/v1/docs>
- Liveness: <http://127.0.0.1:8000/api/v1/health/live>
- Readiness: <http://127.0.0.1:8000/api/v1/health/ready>

Readiness checks the application database, PostgreSQL checkpointer, and Docker
execution backend separately.

### Start services separately

Backend:

```bash
PYTHONPATH=backend/src .venv/bin/python -m omnicell_agent.api.cli
```

Frontend:

```bash
cd frontend
npm run dev
```

Vite proxies `/api/v1` to `http://127.0.0.1:8000` by default. Set
`OMNICELL_API_PROXY_TARGET` before starting Vite to use another backend address.

## Using the Web UI

1. Create or select a conversation.
2. Upload the `.h5ad` dataset required for the current request.
3. Describe a goal in natural language.
4. Follow Skill, Tool, and Backend cards in the main timeline.
5. Preview or download generated datasets, marker tables, plots, and reports.
6. Resolve a review request if the Run requires human confirmation.

Example requests:

```text
What assumptions should I check before comparing marker genes across clusters?
```

```text
Normalize this dataset, cluster the resulting artifact, and then extract marker genes.
```

```text
Using the existing marker table, produce provisional cell-type annotations and flag uncertain clusters.
```

The backend generates a bounded title from the first meaningful user goal.
Conversation titles, messages, events, Run status, and artifacts recover from
PostgreSQL after refresh. Losing an SSE connection does not cancel a Run.

## Cross-Session Memory

Cross-session memory is off by default. Enable the single memory switch in the
right-hand panel and confirm that recalled content may be sent to the configured
LLM provider.

After that, normal language is enough:

- A stable preference such as “lead with the conclusion in future answers” may
  produce a proposal in the timeline. It becomes active only after approval.
- A clear revocation such as “stop using that response preference” can produce
  a confirmation request without requiring a special “forget” command.
- A temporary instruction such as “use two sentences this time” is not stored.
- A message that mixes a long-term fact with the current analysis task is not
  proposed automatically; express the durable fact separately.

The agent proposes at most one candidate per Run. It does not proactively store
credentials, patient information, sensitive inference, execution output,
casual conversation, or current-dataset scientific conclusions. Forgetting
stops future use; permanent purge removes stored plaintext and suppresses
relearning from the old source. Content already sent to an LLM provider cannot
be recalled.

Memory never unlocks a Skill, authorizes a Tool, grants artifact access,
completes a plan, or becomes evidence for the current scientific result.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `OMNICELL_LLM_DEFAULT` | Default `provider/model` alias target | `openrouter/default` in `.env.example` |
| `OMNICELL_LLM_<ROLE>` | Override a specific logical LLM role | Falls back to the default alias |
| `OMNICELL_POSTGRES_DSN` | PostgreSQL used by the app and checkpointer | Local Compose DSN |
| `OMNICELL_WORKSPACE_ROOT` | Conversation data and artifact root | `data/conversations` |
| `OMNICELL_RUNTIME_IMAGE` | Local Docker worker image | `omnicell-worker:latest` |
| `OMNICELL_API_HOST` | Backend bind address | `127.0.0.1` |
| `OMNICELL_API_PORT` | Backend port | `8000` |
| `OMNICELL_API_PROXY_TARGET` | Frontend development proxy target | `http://127.0.0.1:8000` |

See [.env.example](.env.example) for the complete local configuration.

## Validation

### Backend

```bash
uv run --package omnicell-agent \
  pytest backend/tests \
  -m "not postgres and not docker and not live_llm"
```

PostgreSQL integration tests:

```bash
OMNICELL_TEST_POSTGRES_DSN="postgresql://omnicell:omnicell_dev@127.0.0.1:55432/omnicell" \
  uv run --package omnicell-agent pytest backend/tests -m postgres
```

Local Docker Backend tests:

```bash
OMNICELL_RUN_DOCKER_TESTS=1 \
  uv run --package omnicell-agent pytest backend/tests -m docker
```

### Frontend and browser

```bash
cd frontend
npm run contracts:check
npm run typecheck
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

Playwright uses its managed Chromium by default and does not start the user's
daily system Chrome.

### Real HTTP product loop

The live E2E connects real React, FastAPI, PostgreSQL, the checkpointer, and SSE
while using deterministic model and scientific capability substitutes:

```bash
cd frontend
OMNICELL_TEST_POSTGRES_DSN="postgresql://omnicell:omnicell_dev@127.0.0.1:55432/omnicell" \
  npm run test:e2e:live
```

To preserve the generated conversation, events, and artifacts for manual UI
inspection:

```bash
OMNICELL_TEST_POSTGRES_DSN="postgresql://omnicell:omnicell_dev@127.0.0.1:55432/omnicell" \
  make e2e-live-inspect
```

The inspect command prints the frontend URL, isolated PostgreSQL schemas,
workspace path, and `inspection.json` receipt. `Ctrl+C` stops the temporary
services while retaining the inspection data.

## Current Scope and Limitations

- The application is local-only and binds to loopback by default. It does not
  provide public deployment authentication or production operations.
- The public Tool surface currently covers inspection, quality control,
  normalization, clustering, marker extraction, PCA visualization, annotation,
  and bounded exploratory analysis.
- Batch correction, trajectory inference, rapid reference annotation, and
  spatial analysis remain candidate Recipes and are not public Tools.
- Clustering and marker analysis require a log-expression state confirmed by
  matrix characteristics and trusted lineage; isolated metadata cannot unlock
  execution.
- The current refactor intentionally does not preserve legacy module paths,
  APIs, CLIs, capability names, or fixed DAG entry points.
- Real-model tests are observational. Deterministic contracts and controlled
  model substitutes are the reproducible regression gates.

## Further Documentation

- [Architecture and implementation progress](ARCHITECTURE.md) — Chinese
- [Repository collaboration rules](AGENTS.md) — Chinese
- [Backend entry points and database management](backend/README.md) — Chinese
- [Frontend state, transport, and testing](frontend/README.md) — Chinese
- [Public contract boundary](contracts/README.md) — Chinese
- [Local infrastructure boundary](infra/README.md) — Chinese
- [Detailed E2E guide](docs/e2e_guide.md) — Chinese
