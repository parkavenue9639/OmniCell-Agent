---
name: normalize_log
description: 对单细胞数据进行标准化深度（默认 10000）并应用自然对数转换 (log1p)，为下游高变基因和主成分降维平滑化做准备。
license: Internal usage
---

# Normalize Log Skill

该技能提供了一套标准且强健的 Scanpy 数据标准化流水线。
在 Programmer 调度层遇到此 `skill_call` 时，应当原样读取并投递本目录下的 `scripts/execute.py` 进行物理执行。
