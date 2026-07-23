# Contracts 边界

本目录承载 backend 与 frontend 共同使用的版本化公共契约。Graph A/B 当前的 Python 内部 schema 继续留在 backend，不因 monorepo 迁移而移动到这里。

Phase 7 已冻结 v1 契约快照，Phase 8 frontend 以此作为唯一公共契约来源：

- `openapi/v1.json`：REST 与 SSE 入口；
- `events/v1.schema.json`：持久化事件与瞬态事件的 discriminated union。

Backend Pydantic 模型是生成源，`scripts/generate_contracts.py` 负责单向生成；frontend 后续只能从这些快照生成客户端类型，不维护第二套手写 DTO。运行 `uv run --package omnicell-agent python scripts/generate_contracts.py --check` 可以验证快照没有漂移。

所有公开的 `400`、`404`、`409`、`413` 与 `422` 响应统一使用版本化 `ErrorEnvelope`。`request_key` 目前仅属于创建 run 的幂等契约；取消、恢复和审核决定尚未提供幂等语义，因此不暴露无法兑现的字段。
