import scanpy as sc

# 保底拦截：检查核心变量
if 'adata' not in locals() and 'adata' not in globals():
    adata = sc.read_h5ad(globals().get('raw_data_path', '/app/data/pbmc3k_raw.h5ad'))

# 寻找高变基因并进行PCA (如果还没有的话)
if 'X_pca' not in adata.obsm:
    sc.pp.highly_variable_genes(adata, min_mean=0.0125, max_mean=3, min_disp=0.5)
    sc.tl.pca(adata, svd_solver='arpack')

# 计算图构建与Leiden聚类 (修复 FutureWarning 显示指定 igraph 引擎)
if 'leiden' not in adata.obs:
    sc.pp.neighbors(adata, n_neighbors=10, n_pcs=40)
    sc.tl.leiden(adata, flavor="igraph", n_iterations=2, directed=False)
    print("PCA, Neighbors construction, and Leiden clustering finished.")
else:
    print("Leiden clustering already exists, skipping re-computation.")
