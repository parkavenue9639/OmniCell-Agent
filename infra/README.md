# Infra 边界

本目录承载本地开发拓扑、Docker 构建入口和运行环境配置，不承载产品领域逻辑。

Worker 镜像只提供科学计算依赖，不内置 Jupyter 控制通道。容器生命周期、隔离策略、持久化 conversation workspace、输出边界和回收均由 backend 的 Local Docker Backend 统一管理。

本地执行默认连接 OrbStack 提供的 Docker daemon。运行时以不可变镜像身份记录实际执行环境；开发时可通过 `OMNICELL_RUNTIME_IMAGE` 替换 worker 镜像。

`compose.yaml` 提供本地 PostgreSQL 拓扑。应用表与 LangGraph checkpoint 表位于不同 schema，并由各自 migration owner 管理；默认账号仅用于本地开发。
