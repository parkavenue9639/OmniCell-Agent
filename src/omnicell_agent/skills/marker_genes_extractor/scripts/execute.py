import scanpy as sc
import pandas as pd

if 'adata' not in locals() and 'adata' not in globals():
    adata = sc.read_h5ad(globals().get('raw_data_path', '/app/data/pbmc3k_raw.h5ad'))

if 'leiden' not in adata.obs:
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    sc.tl.pca(adata)
    sc.pp.neighbors(adata, n_neighbors=10, n_pcs=40)
    sc.tl.leiden(adata, flavor="igraph", n_iterations=2, directed=False)

import numpy as np

# 防御性逻辑：识别并筛分单细胞样本簇 (避免 rank_genes_groups 运行期间崩溃)
group_counts = adata.obs['leiden'].value_counts()
small_groups = group_counts[group_counts <= 1].index.tolist()
all_groups = adata.obs['leiden'].cat.categories.tolist()
groups_to_analyze = [g for g in all_groups if g not in small_groups]

if not groups_to_analyze:
    print("Warning: 没有可用于统计分析的正常簇（都不大于1个细胞）。强制终止 Marker 搜寻并导出空表。")
    pd.DataFrame(columns=['cluster_id', 'gene_name', 'p_val', 'p_val_adj', 'log2FC', 'pct.1', 'pct.2']).to_json(globals().get('marker_table_path', '/app/data/markers.json'), orient='records', force_ascii=False)
    import sys; sys.exit(0)

# 使用 Wilcoxon rank-sum test 仅在有效的多元群体中寻找差异表达基因
sc.tl.rank_genes_groups(adata, 'leiden', method='wilcoxon', groups=groups_to_analyze)

# 提取并严谨组装包含表达比例 (pct.1/pct.2) 的科研明细 DataFrame
result = adata.uns['rank_genes_groups']
groups = result['names'].dtype.names
marker_dfs = []

for group in groups:
    # 基础指标提取
    df = pd.DataFrame({
        'cluster': group,
        'names': result['names'][group],
        'scores': result['scores'][group],
        'pvals': result['pvals'][group],
        'pvals_adj': result['pvals_adj'][group],
        'logfoldchanges': result['logfoldchanges'][group]
    })
    
    # 【核心防御】 手动计算簇内外表达率 pct.1 和 pct.2，通过 .values 剥离 Index 以兼容新版 scipy sparse bool 掩码切片
    cells_in_cluster = (adata.obs['leiden'] == group).values
    cells_out_cluster = ~cells_in_cluster
    
    # 为了避免因为非稀疏矩阵而导致计算报错，采用 np.asarray 并统计大于 0 的率
    # 抽取对应基因簇的稠密矩阵切片
    genes = df['names'].values
    # 构建当前查询基因维度的整切骗矩阵
    X_genes = adata[:, genes].X
    
    # 获取掩码下所需的局部视图，兼容 scipy sparse 和 numpy dense array
    X_in = X_genes[cells_in_cluster, :]
    X_out = X_genes[cells_out_cluster, :]
    
    if hasattr(X_in, "toarray"):
        pct_1 = (X_in > 0).mean(axis=0).A1
        pct_2 = (X_out > 0).mean(axis=0).A1
    else:
        # Dense Array directly applies reduction
        pct_1 = (X_in > 0).mean(axis=0)
        pct_2 = (X_out > 0).mean(axis=0)
        
    df['pct.1'] = pct_1.round(3)
    df['pct.2'] = pct_2.round(3)
    
    # 过滤筛选并取 Top 50 写入
    df_filtered = df[(df['pvals_adj'] < 0.05) & (df['logfoldchanges'] > 1.0)]
    marker_dfs.append(df_filtered.head(50))

all_markers_df = pd.concat(marker_dfs, ignore_index=True)
export_path = globals().get('marker_table_path', '/app/data/markers.json')
all_markers_df.to_json(export_path, orient='records', force_ascii=False)
print(f"Marker genes analysis completed and deeply JSON contract saved to {export_path}")
