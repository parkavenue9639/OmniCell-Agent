---
name: qc_and_filter
description: 自动计算线粒体基因组比例并执行基础的质控过滤（去除低表达细胞与低频基因）。适用于单细胞分析中最开始的预处理步骤。当步骤为“去重、过滤极低细胞、线粒体质控”时触发。
license: Internal usage
---

# QC and Filter Recipe

该配方提供一套确定性的 Scanpy 单细胞初始数据质控实现，供内部 Planner 和公开 Tool adapter 复用。
在探索性分析内部遇到此 `recipe_call` 时，读取本目录下的 `scripts/execute.py` 执行。
