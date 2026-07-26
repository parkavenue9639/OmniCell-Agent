---
name: normalize_log
description: 对单细胞数据进行标准化深度（默认 10000）并应用自然对数转换 (log1p)，为下游高变基因和主成分降维平滑化做准备。
license: Internal usage
---

# Normalize Log Recipe

该配方提供一套确定性的 Scanpy 数据标准化实现。
在探索性分析内部遇到此 `recipe_call` 时，读取本目录下的 `scripts/execute.py` 执行。
