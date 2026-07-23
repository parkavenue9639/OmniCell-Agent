import scanpy as sc
import numpy as np

# 保底检查
if 'adata' not in locals() and 'adata' not in globals():
    adata = sc.read_h5ad(globals().get('raw_data_path', '/app/data/spatial_sample.h5ad'))

if 'spatial' not in adata.obsm:
    print("Spatial Aborted: The dataset does not contain '.obsm[\"spatial\"]' coordinates. Not a spatial transcriptomics dataset.")
else:
    print("Starting Spatial Domain Identification...")
    
    # 模拟通用空间组分析框架 (类似于 Squidpy 的核心逻辑平替)
    try:
        import squidpy as sq
        print("Using Squidpy for spatial neighbor graph construction.")
        sq.gr.spatial_neighbors(adata)
    except ImportError:
        print("Squidpy not installed. Falling back to Scanpy's basic coordinate-based neighbor graph.")
        # 取巧方案：强制使用空间横纵坐标作为近邻图计算的底座
        sc.pp.neighbors(adata, use_rep='spatial', key_added='spatial')
        
    # 基于空间图聚类形成空间物理域 (Spatial Domains)
    sc.tl.leiden(adata, neighbors_key='spatial', key_added='spatial_domain')
    
    print("Spatial domains successfully clustered into 'adata.obs[\"spatial_domain\"]'.")
