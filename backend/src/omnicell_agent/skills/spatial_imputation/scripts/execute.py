import scanpy as sc
import numpy as np
import scipy.sparse as sp

# 保底检查
if 'adata' not in locals() and 'adata' not in globals():
    adata = sc.read_h5ad(globals().get('raw_data_path', '/app/data/spatial_sample.h5ad'))

if 'spatial' not in adata.obsm:
    print("Spatial Imputation Aborted: Missing '.obsm[\"spatial\"]'.")
else:
    print("Starting Spatial Transcriptomics Imputation (Smoothing)...")
    
    # 一个通用的空间卷积平滑实现（作为 Imputation 基础平替算法）
    # 当找不到如 Tangram 这种重型端点时，使用近邻平滑消除 Dropout
    if 'spatial_connectivities' not in adata.obsp:
        print("Building spatial connectivity graph for imputation.")
        sc.pp.neighbors(adata, use_rep='spatial', key_added='spatial')
        
    conn = adata.obsp['spatial_connectivities']
    # 加入自环，保留自身原始表达特征
    conn = conn + sp.eye(conn.shape[0])
    
    # 归一化转移概率矩阵
    row_sums = np.array(conn.sum(axis=1))[:, 0]
    row_sums[row_sums == 0] = 1.0 # 控制异常
    norm_conn = conn.multiply(1.0 / row_sums[:, None])
    
    # 执行平滑乘子插值
    print("Executing iterative sparse array imputation...")
    adata.layers['imputed_counts'] = norm_conn.dot(adata.X)
    
    print("Spatial imputation successfully generated in 'adata.layers[\"imputed_counts\"]'.")
