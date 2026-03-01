---
name: spatial_domain_identification
description: 【空间转录组】基于空间坐标 (Spatial Coordinates) 与基因表达联合执行空间结构域的自动识别与聚类划分 (Spatial Domain Identification)，对应原版系统 Tangram/DeepST/Squidpy 等高阶功能。
license: Internal usage
---

# Spatial Domain Identification Skill

本技能在遇到拥有 `.obsm['spatial']` 属性的切片数据时，通过内置的空间邻域图构建算法（近似于 Squidpy `sq.gr.spatial_neighbors`），结合转录组表达特征进行异构降维和空间域聚类。
