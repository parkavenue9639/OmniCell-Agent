import pandas as pd
import scanpy as sc

# 保底拦截
if 'adata' not in locals() and 'adata' not in globals():
    adata = sc.read_h5ad(globals().get('raw_data_path', '/app/data/pbmc3k_raw.h5ad'))

if 'leiden' not in adata.obs:
    print("Cannot perform Auto Annotation: Leiden clusters not found.")
else:
    # 极简通用粗粒度免疫细胞打标示例
    common_markers = {
        'T cell': ['CD3D', 'CD3E', 'CD3G'],
        'B cell': ['CD79A', 'MS4A1'],
        'Macrophage/Monocyte': ['CD14', 'FCGR3A', 'LZTFL1'],
        'NK cell': ['GNLY', 'NKG7'],
    }
    
    # 一个非常粗糙的单基因库加权累加作为临时初始标签
    score_df = pd.DataFrame(index=adata.obs['leiden'].cat.categories, columns=common_markers.keys()).fillna(0.0)
    for ctype, m_genes in common_markers.items():
        valid_genes = [g for g in m_genes if g in adata.var_names]
        if len(valid_genes) > 0:
            sc.tl.score_genes(adata, gene_list=valid_genes, score_name=f'score_{ctype}')
            for cluster in score_df.index:
                mean_score = adata.obs.loc[adata.obs['leiden'] == cluster, f'score_{ctype}'].mean()
                score_df.loc[cluster, ctype] = mean_score
        else:
            print(f"Warning: No valid marker genes found for cell type '{ctype}'. Skipping scoring.")
            
    # 取最值作为 Base Annotation
    base_annotations = score_df.idxmax(axis=1)
    adata.obs['celltype_base'] = adata.obs['leiden'].map(base_annotations)
    print("Auto Baseline Annotation finished. Added 'celltype_base' into adata.obs.")
