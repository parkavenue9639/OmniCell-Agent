import scanpy as sc

if 'adata' not in locals() and 'adata' not in globals():
    adata = sc.read_h5ad(globals().get('raw_data_path', '/app/data/pbmc3k_raw.h5ad'))

try:
    # 优先寻找常见的批次列名称
    batch_key_candidates = ['batch', 'sample', 'donor', 'dataset']
    target_key = None
    for k in batch_key_candidates:
        if k in adata.obs.columns:
            target_key = k
            break
            
    if target_key:
        print(f"Applying Harmony batch correction over column [{target_key}]...")
        # 依赖 harmonypy 安装
        sc.external.pp.harmony_integrate(adata, target_key)
        
        # 覆盖使用新降维重建邻接图
        sc.pp.neighbors(adata, use_rep='X_pca_harmony', n_neighbors=15)
        sc.tl.leiden(adata, flavor="igraph", n_iterations=2, directed=False)
        print("Harmony spatial correction and re-clustering done.")
    else:
        print("Batch Correction Skipped: Cannot identify a valid batch/sample column.")
except ImportError:
    print("Warning: harmonypy missing, skipping advanced batch correction.")
except Exception as e:
    print(f"Batch Correction Failed: {e}")
