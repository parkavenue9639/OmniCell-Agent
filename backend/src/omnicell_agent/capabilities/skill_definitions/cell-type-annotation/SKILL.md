---
name: cell-type-annotation
description: 当用户需要基于 marker table 完成带验证、评分、一致性检查和报告的细胞类型注释时使用。
version: "1.1"
tools:
  - inspect_marker_table
  - find_marker_genes
  - annotate_cell_clusters
---

# 细胞类型注释

注释输入必须是当前 conversation 已登记且非空的 marker table。若输入仍是已经聚类的 dataset，应先使用 `find_marker_genes` 生成 marker table；只需查看 cluster 与 marker 摘要时使用 `inspect_marker_table`。用户只要求解释注释方法时，可以基于本 Skill 回答，不读取数据或执行 Tool。

## 证据与标签边界

- cluster marker 是条件差异表达证据，不是细胞身份金标准。优先使用同一谱系内一致的 marker panel，同时检查共享 marker、冲突 marker、应出现但缺失的证据和可能的双细胞、状态信号或环境 RNA。
- 物种、组织和疾病背景用于评估候选标签的合理性，但不能仅因某类细胞在该组织中常见或罕见就确认或排除标签。marker 证据不足时返回 `Unknown`、更宽的 lineage 或人工复核，而不是强行给出精细亚型。
- 标签粒度不能超过输入证据分辨率。统计显著性、效应大小、簇内/簇外表达比例、marker 特异性和跨 cluster 一致性应共同参与解释。
- 输出中的 `confidence_score`/`cs_score` 是当前内部规则合成的启发式证据评分，不是校准概率，不能表述为“认证”“已验证”或错误率保证。

`annotate_cell_clusters` 是一个内聚的复合能力，自行完成 cluster 级候选注释、独立复核、启发式评分、必要的再评估、一致性检查和报告。主 Agent 不拆开控制其内部步骤，也不把内部推理记录当作结果。

验收时检查所有输入 cluster 是否都有输出、标签与 marker 证据是否相容、需要人工复核的数量、annotation artifact 与报告 artifact。低分、冲突、跨 cluster 异常或证据不足的 cluster 必须明确标记不确定性；所有标签均按“基于当前证据的暂定注释”解释。
