---
name: qc_and_filter
description: 自动计算线粒体基因组比例并执行基础的质控过滤（去除低表达细胞与低频基因）。适用于单细胞分析中最开始的预处理步骤。当步骤为“去重、过滤极低细胞、线粒体质控”时触发。
license: Internal usage
---

# QC and Filter Skill

该技能提供了一套标准且强健的 Scanpy 单细胞初始数据质控流水线。它被绑定于 Planner 进行意图匹配。
在 Programmer 等沙盒调度层遇到此 `skill_call` 时，应当原样读取并投递本目录下的 `scripts/execute.py` 进行物理执行。
