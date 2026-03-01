import scanpy as sc
import anndata
import os

# 允许将 Pandas 新版 StringArray 写入 HDF5 (解决 Scanpy 新老 API 崩溃问题)
if hasattr(anndata, 'settings') and hasattr(anndata.settings, 'allow_write_nullable_strings'):
    anndata.settings.allow_write_nullable_strings = True

data_dir = os.path.join(os.path.dirname(__file__), "../data")
os.makedirs(data_dir, exist_ok=True)
spatial_path = os.path.join(data_dir, "spatial_sample.h5ad")

if not os.path.exists(spatial_path):
    print("Downloading built-in Spatial Transcriptomics dataset (Visium V1_Breast_Cancer_Block_A_Section_1)...")
    try:
        # scanpy datasets uses this function to grab subset
        adata = sc.datasets.visium_sge(sample_id="V1_Breast_Cancer_Block_A_Section_1")
        
        if adata.shape[0] > 500:
            sc.pp.subsample(adata, n_obs=500)
            
        adata.write_h5ad(spatial_path)
        print(f"Spatial dataset saved to {spatial_path}")
        print(f"Data shape: {adata.shape}")
        print(f"Has spatial coordinates: {'spatial' in adata.obsm}")
    except Exception as e:
        print(f"Failed to download dataset. Executing backup fake spatial creation... Error: {e}")
        # fallback for network issues: generate a fake spatial layout
        adata = sc.datasets.pbmc3k()
        sc.pp.subsample(adata, n_obs=500)
        import numpy as np
        # Generate random 2D spatial coordinates for testing
        adata.obsm['spatial'] = np.random.rand(adata.n_obs, 2) * 100
        adata.write_h5ad(spatial_path)
        print(f"Fake Spatial dataset (derived from pbmc3k) saved to {spatial_path}")
else:
    print(f"Spatial dataset already exists at {spatial_path}")
