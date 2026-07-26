---
name: scientific-visualization
description: 当用户需要从已有分析状态生成可复现的科研图表并检查图表前置条件时使用。
version: "1.1"
tools:
  - inspect_dataset
  - cluster_cells
  - plot_pca_clusters
---

# 科研可视化

可视化 Tool 只负责把已经存在的科学状态转化为图像，不应隐式改变表达矩阵、重新归一化或重做聚类。PCA cluster 图要求输入 dataset 已经具有 PCA 坐标和 Leiden 标签；前置条件不满足时，只有用户目标同时包含聚类计算才调用 `cluster_cells`。

绘图前确认用户希望展示的变量、计算表示和分组来源。PCA、UMAP 等坐标是特定算法和参数生成的表示；图中分离、重叠或局部结构是可见观察，不自动证明 cluster 稳健、批次校正成功或细胞身份正确。不要仅凭一张图推断未显示的统计或生物学事实。

验收时检查图像 artifact、源 dataset、实际绘图参数、图类型、坐标标签、颜色映射和图例是否与目标一致。可读性问题可以要求重绘；对科学有效性的结论仍应回到输入数据、算法参数和独立证据，不以美化替代数据含义。
