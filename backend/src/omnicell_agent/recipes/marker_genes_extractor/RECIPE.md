---
name: marker_genes_extractor
description: 对当前已经完成聚类的 Leiden 簇应用 `sc.tl.rank_genes_groups` (Wilcoxon) 提取各簇标志性差异表达基因，并将包含 `pct.1`/`pct.2` 与 `logFC` 的科研级 Data Contract Json/CSV 契约落盘导出。
license: Internal usage
---

# Marker Genes Extractor Recipe

该配方提供一套带 marker 输出契约校验的确定性提取实现。
在探索性分析内部遇到此 `recipe_call` 时，读取本目录下的 `scripts/execute.py` 执行。

输入只来自已注入的 `raw_data_path` 或当前会话内存中的 `adata`；marker JSON 必须准确写入已注入的 `marker_table_path`。不得改写目标路径、写入 `/app/data` 根目录或自行加载示例数据。
