---
name: batch_correction
description: 【高级技能】使用 Harmony 自动处理数据中因不同样本、多组织或实验批次带来的批次效应 (Batch Effects)，对 PCA 空间进行平滑对齐整合。如果不需要纠正批次不要滥用。
license: Internal usage
---

# Batch Correction Skill

在 Programmer 调度层遇到此 `skill_call` 时，本 `scripts/execute.py` 尝试执行 harmonypy 以覆盖默认的邻接点连接矩阵。
