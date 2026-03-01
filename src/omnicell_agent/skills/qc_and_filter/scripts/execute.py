import scanpy as sc

# 保底拦截：检查核心变量
if 'adata' not in locals() and 'adata' not in globals():
    adata = sc.read_h5ad(globals().get('raw_data_path', '/app/data/pbmc3k_raw.h5ad'))

# 1. 标记线粒体基因 (增加 .astype(bool) 兼容新版 Pandas与稀疏矩阵)
adata.var['mt'] = adata.var_names.str.startswith('MT-').astype(bool)
# 2. 计算质控指标
sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], percent_top=None, log1p=False, inplace=True)

# 3. 基础过滤: 移除表达基因数极少的细胞和表达细胞数极少的基因
sc.pp.filter_cells(adata, min_genes=200)
sc.pp.filter_genes(adata, min_cells=3)
# 4. 过滤线粒体基因比例过高的死细胞/濒死细胞
adata = adata[adata.obs.pct_counts_mt < 20, :]
print(f"QC and Filter applied. Remaining cells: {adata.n_obs}, genes: {adata.n_vars}")
