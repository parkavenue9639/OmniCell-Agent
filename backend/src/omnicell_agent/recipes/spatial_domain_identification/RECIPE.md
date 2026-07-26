---
name: spatial_domain_identification
description: 【空间转录组】基于空间坐标 (Spatial Coordinates) 与基因表达联合执行空间结构域的自动识别与聚类划分 (Spatial Domain Identification)，对应原版系统 Tangram/DeepST/Squidpy 等高阶功能。
license: Internal usage
---

# Spatial Domain Identification Recipe

该配方在输入拥有 `.obsm['spatial']` 属性时，通过空间邻域图构建算法结合转录组表达特征进行空间域聚类。
