# OmniCell-Agent Backend

本目录是 OmniCell-Agent 的 Python package 与 backend 工程边界，承载 Agent Loop、科学领域能力、运行时、持久化、公共 API 和测试。

产品执行的唯一受支持入口是 conversation/run 生命周期 API；检查、原子分析和复合领域能力只能由 Agent Loop 按需调用，不提供旧固定 DAG、旧能力名或直接工作流 CLI 入口。数据库 schema 只通过独立的管理命令维护。

整体架构、实施顺序与跨端边界以仓库根目录的 `ARCHITECTURE.md` 为准。

在仓库根目录执行 `uv sync --package omnicell-agent` 可建立包含测试工具的开发环境。开发态 API 通过 `PYTHONPATH=backend/src .venv/bin/python -m omnicell_agent.api.cli` 从当前 checkout 启动，避免复用虚拟环境中可能陈旧的 console-script；默认只监听 `127.0.0.1:8000`。安装后的 wheel 仍提供 `omnicell-api` console-script。

启动 API 前必须通过 `uv run --package omnicell-agent omnicell-db migrate` 显式执行应用 migration 与 checkpoint schema 初始化；服务启动只校验 schema revision，不在隐式启动路径中建表。Alembic 脚本随 backend package 分发，editable 开发态与 wheel 安装态使用同一组 migration 资源。
