---
name: pca_clustering
description: 提取高变基因 (HVG)，对单细胞数据进行主成分降维 (PCA)，并立刻计算最近邻域图 (Neighbors) 然后应用 Leiden 分群或聚类算法以确定单细胞社区边界。
license: Internal usage
---

# PCA Clustering Skill

该技能提供了一套标准的 PCA 与 Leiden 分群流水线。
在 Programmer 调度层遇到此 `skill_call` 时，应当原样读取并投递本目录下的 `scripts/execute.py` 进行物理执行。
