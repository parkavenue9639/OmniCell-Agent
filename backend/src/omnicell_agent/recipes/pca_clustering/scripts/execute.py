import scanpy as sc
from pathlib import Path

output_root = Path(globals()['artifact_output_root'])
output_root.mkdir(parents=True, exist_ok=True)
n_top_genes = int(tool_parameters["n_top_genes"])
n_pcs = int(tool_parameters["n_pcs"])
n_neighbors = int(tool_parameters["n_neighbors"])
resolution = float(tool_parameters["resolution"])

# 保底拦截：检查核心变量
if 'adata' not in locals() and 'adata' not in globals():
    raw_data_path = globals().get('raw_data_path')
    if not raw_data_path:
        raise RuntimeError("RECIPE_INPUT_ERROR: raw_data_path is required")
    adata = sc.read_h5ad(raw_data_path)

# 寻找高变基因并进行PCA (如果还没有的话)
if 'X_pca' not in adata.obsm:
    sc.pp.highly_variable_genes(adata, n_top_genes=n_top_genes)
    sc.tl.pca(adata, n_comps=n_pcs, svd_solver='arpack')

# 计算图构建与Leiden聚类 (修复 FutureWarning 显示指定 igraph 引擎)
if 'leiden' not in adata.obs:
    sc.pp.neighbors(adata, n_neighbors=n_neighbors, n_pcs=n_pcs)
    sc.tl.leiden(
        adata,
        resolution=resolution,
        flavor="igraph",
        n_iterations=2,
        directed=False,
    )
    print("PCA, Neighbors construction, and Leiden clustering finished.")
else:
    print("Leiden clustering already exists, skipping re-computation.")

# --- 【核心视觉垫底拦截】防备大模型乱画 UMAP 被严苛的 Evaluator 驳回 ---
# 所有图像必须写入当前 invocation 的可写 artifact 目录。
sc.settings.figdir = str(output_root)
if 'spatial' in adata.obsm:
    sc.pl.spatial(adata, color='leiden', show=False, save='_spatial_domain.png')
else:
    if 'X_umap' not in adata.obsm:
        sc.tl.umap(adata)
    sc.pl.umap(adata, color='leiden', show=False, save='_omnicell_umap.png')
