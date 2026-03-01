import scanpy as sc

# 保底拦截：检查核心变量
if 'adata' not in locals() and 'adata' not in globals():
    adata = sc.read_h5ad(globals().get('raw_data_path', '/app/data/pbmc3k_raw.h5ad'))

# 标准化与对数转换
if 'log1p' not in adata.uns_keys():
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    print("Normalization and log1p completed.")
else:
    print("Data already seems to be log-transformed, skipping normalization steps to prevent over-flattening.")
