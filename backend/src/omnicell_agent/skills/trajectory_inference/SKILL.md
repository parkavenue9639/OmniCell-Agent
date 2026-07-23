---
name: trajectory_inference
description: 【高级技能】执行基于 PAGA 或 Pseudotime 分支的单细胞分化轨迹推断。用于推导干细胞向成熟细胞发育的连续演化时间轴和方向。
license: Internal usage
---

# Trajectory Inference Skill

在 Programmer 调度层遇到此 `skill_call` 时，本 `scripts/execute.py` 提供调用 `sc.tl.paga` 创建组织动力学发育概览图的底层物理代码。
