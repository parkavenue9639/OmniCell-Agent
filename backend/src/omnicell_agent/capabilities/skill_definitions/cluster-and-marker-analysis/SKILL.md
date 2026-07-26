---
name: cluster-and-marker-analysis
description: 当目标涉及降维聚类、cluster 特征比较、marker gene 的定义、提取与解释，或这些方法的适用条件与证据边界时使用。
version: "1.2"
tools:
  - inspect_dataset
  - cluster_cells
  - find_marker_genes
  - inspect_marker_table
---

# 聚类与 Marker 分析

`cluster_cells` 消费已经归一化的 dataset，并生成包含 PCA、邻接图与 Leiden 标签的新 dataset。`find_marker_genes` 消费已经聚类的 dataset，按照显式统计参数生成 marker table。前置条件不足时结构化拒绝或明确补齐独立步骤，不让 marker 提取或绘图隐式替用户补做未声明的预处理。

如果用户只要求方法解释，可以直接基于本 Skill 回答，不读取 dataset，也不调用领域 Tool。如果只要求查看已有 marker table，使用 `inspect_marker_table`；只有需要重新计算时才调用分析 Tool。

## 方法与证据边界

- Leiden 等图聚类方法通常作用于由降维特征构建的邻接图；PCA 可以参与该计算链路，UMAP 或 t-SNE 通常用于展示。除非实际参数或产物能够证明，不要从可视化坐标反推聚类算法的输入空间。
- marker 分析是在既有 cluster 标签条件下比较表达分布，用于描述 cluster 的分子特征、辅助注释和发现异常。它不等同于寻找造成聚类的因果“驱动基因”，也不说明基因只在该 cluster 表达。
- 聚类与 marker 检验经常复用同一份数据，因此 post-clustering 显著性受到先分组再检验的选择过程影响。marker 的存在不能独立证明 cluster 稳健、真实或具有唯一生物学解释；稳健性还需结合重采样或参数敏感性、批次结构、数据质量、外部参考和实验背景判断。
- marker 不是细胞身份的金标准。解释时同时区分校正后显著性、效应大小、簇内与簇外表达比例、cluster 特异性和跨样本可复现性，并优先使用正反 marker panel 和多类证据，而不是单个基因作确定性结论。

多个步骤必须通过每次返回的新 ArtifactRef 串联。验收至少包括非空 cluster、非空 marker table、实际使用的聚类与检验参数，以及 marker 结果中可用的效应、显著性和表达比例字段；这些验收说明产物可解释，不等同于独立生物学验证。
