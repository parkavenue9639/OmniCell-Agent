---
name: pca_scatter
description: 绘制符合科研出版级别的 PCA 散点图，附加细胞分群配色 `color='leiden'` 与坐标轴抗锯齿美化。
license: Internal usage
---

# PCA Scatter Recipe

该配方提供一套带明确输入前置条件的 PCA Scatter 绘图实现。
在探索性分析内部遇到此 `recipe_call` 时，读取本目录下的 `scripts/execute.py` 执行。

输入只来自已注入的 `raw_data_path` 或当前会话内存中的 `adata`；图片必须写入已注入的 `artifact_output_root`，并输出实际保存路径供 Evaluator 识别。不得写入 `/app/data` 根目录或当前工作目录。
