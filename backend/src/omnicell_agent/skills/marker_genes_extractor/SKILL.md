---
name: marker_genes_extractor
description: 对当前已经完成聚类的 Leiden 簇应用 `sc.tl.rank_genes_groups` (Wilcoxon) 提取各簇标志性差异表达基因，并将包含 `pct.1`/`pct.2` 与 `logFC` 的科研级 Data Contract Json/CSV 契约落盘导出。
license: Internal usage
---

# Marker Genes Extractor Skill

该技能提供了一套内置自动合并算子（防坑：Scanpy 默认 DataFrame 不出细胞比例字段）提取极高质量生信分析 Marker 的片段。
在 Programmer 调度层遇到此 `skill_call` 时，应当原样读取并投递本目录下的 `scripts/execute.py` 进行物理执行。

输入只来自已注入的 `raw_data_path` 或当前会话内存中的 `adata`；marker JSON 必须准确写入已注入的 `marker_table_path`。不得改写目标路径、写入 `/app/data` 根目录或自行加载示例数据。
