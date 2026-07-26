---
name: pca_clustering
description: 提取高变基因 (HVG)，对单细胞数据进行主成分降维 (PCA)，并立刻计算最近邻域图 (Neighbors) 然后应用 Leiden 分群或聚类算法以确定单细胞社区边界。
license: Internal usage
---

# PCA Clustering Recipe

该配方提供一套确定性的 PCA 与 Leiden 分群实现。
在探索性分析内部遇到此 `recipe_call` 时，读取本目录下的 `scripts/execute.py` 执行。

输入只来自已注入的 `raw_data_path` 或当前会话内存中的 `adata`；图像等文件只写入已注入的 `artifact_output_root`。不得写入 `/app/data` 根目录、当前工作目录或自行下载替代数据。
