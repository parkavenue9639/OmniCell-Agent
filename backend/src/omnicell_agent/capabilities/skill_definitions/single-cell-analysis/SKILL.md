---
name: single-cell-analysis
description: 当用户需要理解单细胞数据语境，或完成从数据到分析产物的受控流程时使用。
version: "1.0"
tools:
  - inspect_single_cell_context
  - run_qc_and_filter
  - run_normalize_log
  - run_pca_clustering
  - extract_marker_genes
  - generate_pca_scatter
  - single_cell_analysis
---

# 单细胞数据分析

先确认输入是本次 run 明确提供、且属于当前 conversation 的数据集 artifact。调用时必须原样复制完整 `ArtifactRef`，包括 `media_type: null` 或空 `metadata`；不得仅凭文件名、URI 或历史消息重建引用。

仅需识别物种、组织、疾病状态或分析目标时，使用只读检查。用户只要求质量控制、归一化、PCA 与聚类、marker gene 提取或 PCA 可视化中的一个明确操作时，优先调用对应原子 Tool；多个原子步骤通过前一步返回的新 `ArtifactRef` 串联，不能假设容器内仍保留上一步的 `adata`。需要自主规划、执行、评估与重试的完整分析目标时，才调用完整 Graph A workflow。一般解释、结果解读或无需实际计算的问题直接回复，不要因为已有数据集就运行工作流。

完整工作流和原子 Tool 都只读取所给 artifact 对应的受控挂载数据，不下载示例数据，也不依赖网络。输出进入当前 invocation 的可写 artifact 空间。不要直接控制 Graph A 的 Planner、Programmer、Executor 或 Evaluator 节点，也不要把宿主路径、运行时私有状态或大型矩阵放入调用结果。
