---
name: pca_scatter
description: 绘制符合科研出版级别的 PCA 散点图。此技能强制附加了最佳配色的细胞分群 `color='leiden'` 以及坐标轴抗锯齿美化，专为生成高质量的成果大图设计。
license: Internal usage
---

# PCA Scatter Skill

该技能提供了一套包含坐标轴强制绘制、去除多余冗余线并且拉升分辨率至 300 DPI 的完美版 Scatter 绘图脚本。
在 Programmer 调度层遇到此 `skill_call` 时，应当原样读取并投递本目录下的 `scripts/execute.py` 进行物理执行。

输入只来自已注入的 `raw_data_path` 或当前会话内存中的 `adata`；图片必须写入已注入的 `artifact_output_root`，并输出实际保存路径供 Evaluator 识别。不得写入 `/app/data` 根目录或当前工作目录。
