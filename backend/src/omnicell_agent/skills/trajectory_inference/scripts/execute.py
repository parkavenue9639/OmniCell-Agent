import scanpy as sc

# 保底检查
if 'adata' not in locals() and 'adata' not in globals():
    adata = sc.read_h5ad(globals().get('raw_data_path', '/app/data/pbmc3k_raw.h5ad'))

if 'leiden' not in adata.obs:
    print("Trajectory Inference Aborted: Leiden clusters not found. Please run clustering first.")
else:
    print("Starting PAGA based Trajectory Inference...")
    # PAGA 轨迹连通性探索
    sc.tl.paga(adata, groups='leiden')
    
    # 选择合适的发源根节点 (此处简单默认选择编号0或者出现最早的簇，理想情况下应由外部传入 root)
    root_val = adata.obs['leiden'].cat.categories[0]
    adata.uns['iroot'] = list(adata.obs['leiden'] == root_val).index(True)
    
    # 扩散伪时间计算 (DPT)
    sc.tl.dpt(adata)
    
    print("Trajectory structure (PAGA) and Diffusion Pseudotime (DPT) successfully embedded.")
