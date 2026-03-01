# 🧬 [Template: 标志基因提取与 DataFrame 重构标准参考]
在你的任务规划中，如果你被指示“寻差异基因/MarkerGenes”，并且要求你**保存为表格或结构化数据记录 JSON** (如 `/app/data/markers.csv` 等)，
你必须完全遵守并实现如下代码逻辑：
这是保证下游的深度图检/验证管线（Sub-Graph B CASSIA）运行的核心基础，**决不能遗漏计算 `pct.1` 和 `pct.2` 参数（即本群平均检出率和其余群平均检出率）**。如果你单纯调用了 `sc.tl.rank_genes_groups` 那将注定失败。

```python
import scanpy as sc
import pandas as pd
import numpy as np

# 1. 免疫报错机制：确保已经过前置预处理，尤其是 `log1p` 与 `leiden`。
# 如果数据无分群，执行差异寻找注定是失败或毫无意义的。因此必须确保先前的处理流完整。
if 'adata' not in locals() and 'adata' not in globals():
    adata = sc.read_h5ad(raw_data_path)

if 'leiden' not in adata.obs:
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    sc.tl.pca(adata)
    sc.pp.neighbors(adata)
    sc.tl.leiden(adata)

# 2. 差异表达分析 (Wilcoxon 是稳健的首选)
sc.tl.rank_genes_groups(adata, 'leiden', method='wilcoxon')

# 3. 提取结果，组装标准的 DataFrame (绝不能遗漏这一步)
marker_genes_df = sc.get.rank_genes_groups_df(adata, group=None)

# 4. [致命规则] 追加计算 pct.1 (本簇细胞表达比例) 和 pct.2 (其余簇细胞表达比例)
# 这一逻辑是将 scanpy 的组群寻找对齐 Seurat 等标准的桥梁，务必将下方原样照抄！
def calculate_pct(adata, cluster_col, cluster_id, gene_name):
    # Retrieve cells in the specific cluster
    cells_in_cluster = adata.obs[cluster_col] == cluster_id
    cells_out_cluster = adata.obs[cluster_col] != cluster_id
    
    # Retrieve gene expression matrix column
    try:
        # robustly fetch gene array regardless of dense/sparse format
        gene_expr = adata[:, gene_name].X.toarray().flatten() 
    except AttributeError:
        # In case it is already dense
        gene_expr = adata[:, gene_name].X.flatten()
        
    # Calculate non-zero percentage
    pct_1 = np.sum(gene_expr[cells_in_cluster] > 0) / np.sum(cells_in_cluster) if np.sum(cells_in_cluster) > 0 else 0.0
    pct_2 = np.sum(gene_expr[cells_out_cluster] > 0) / np.sum(cells_out_cluster) if np.sum(cells_out_cluster) > 0 else 0.0
    return pct_1, pct_2

# 利用 DataFrame 的 apply 进行逐行扫描补充。可能稍慢，但对于保证 Data Contract 是铁律
print("Calculating pct.1 and pct.2 for marker precision constraint...")
# 为了效率，可以只处理 top N genes, 在这里我们简化为仅依靠 LLM 根据需要筛选（如 pvals_adj < 0.05 及其它需要保存的结果再做 pct 计算。
# 例如：
filtered_df = marker_genes_df[marker_genes_df['pvals_adj'] < 0.05].copy()
pct_data = []

# 安全的 pct 推断执行
for index, row in filtered_df.iterrows():
    p1, p2 = calculate_pct(adata, 'leiden', str(row['group']), row['names'])
    pct_data.append({'pct.1': p1, 'pct.2': p2})
    
pct_df = pd.DataFrame(pct_data, index=filtered_df.index)
final_markers_df = pd.concat([filtered_df, pct_df], axis=1)

# 5. 最后务必将组装齐整带有 pct 的 final_markers_df 依据 Planner 指示的路径保存下来。
target_path = marker_table_path # 通常由上下文传入的 target csv/json 参数取代
# final_markers_df.to_csv(target_path, index=False) 等...
```
