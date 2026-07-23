---
name: deep-cell-annotation
description: 当用户需要检查 marker 契约或完成带验证、评分和一致性审阅的细胞类型注释时使用。
version: "1.0"
tools:
  - inspect_marker_contract
  - extract_marker_genes
  - deep_cell_annotation
---

# 深度细胞类型注释

输入必须是本次 run 明确提供、且在当前 conversation 内已登记并通过校验的 marker-table artifact。调用时必须原样复制完整 `ArtifactRef`，包括 `media_type: null` 或空 `metadata`；不得从历史文本、文件名或 URI 猜测引用。若当前输入仍是已经聚类的数据集，可先使用共享的 `extract_marker_genes` 原子 Tool 生成 marker-table artifact；只需了解 cluster 与 marker 摘要时，使用只读 contract 检查；需要正式 annotation、验证和报告结果时，才使用完整 Graph B 工作流。一般 marker 解释或无需重新计算的问题应直接回复。

完整工作流自行管理 cluster fan-out、annotation、validation、scoring、可选 improvement、跨 cluster 一致性处理和报告。不要把这些内部节点拆开调用，也不要把中间 reasoning state 当作最终结果。
