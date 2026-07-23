import scanpy as sc
import matplotlib.pyplot as plt
from pathlib import PurePosixPath

output_root = globals()['artifact_output_root']
sc.settings.figdir = output_root

if 'adata' not in locals() and 'adata' not in globals():
    adata = sc.read_h5ad(globals().get('raw_data_path', '/app/data/pbmc3k_raw.h5ad'))

# 强制保底依赖链路，防止直接画报 KeyError
if 'leiden' not in adata.obs or 'X_pca' not in adata.obsm:
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    sc.tl.pca(adata) 
    sc.pp.neighbors(adata, n_neighbors=10, n_pcs=40)
    sc.tl.leiden(adata, flavor="igraph", n_iterations=2, directed=False)

# 全局美学参数环境
sc.set_figure_params(dpi=300, facecolor='white', transparent=False, fontsize=12)

fig, ax = plt.subplots(figsize=(8, 6))

# 使用更柔和高级的科研配色 (Set2/Paired 等), 移出图例，增加散点大小
sc.pl.pca(
    adata, 
    color='leiden', 
    size=50, 
    alpha=0.85, 
    palette='Set2', 
    ax=ax, 
    show=False,
    legend_loc='right margin',
    legend_fontsize=11
)

# 科研级边框与坐标轴精装修：加重左下轴线，完全剔除顶部与右侧冗余线条
ax.spines['right'].set_visible(False)
ax.spines['top'].set_visible(False)
ax.spines['left'].set_linewidth(1.5)
ax.spines['bottom'].set_linewidth(1.5)

# 增强刻度可见性
ax.tick_params(axis='both', which='major', labelsize=10, width=1.5, length=6)
# 改写为严谨坐标名
ax.set_xlabel("Principal Component 1", fontsize=13, fontweight='bold')
ax.set_ylabel("Principal Component 2", fontsize=13, fontweight='bold')

ax.set_title("Single-Cell Transcriptomic PCA Profile\n(Clustered by Leiden)", 
             fontsize=16, fontweight='bold', pad=20)

target_filename = "current_pca.png"
target_path = str(PurePosixPath(output_root) / target_filename)
fig.savefig(target_path, bbox_inches='tight', dpi=300)
print(f"saving figure to file {target_path}")
plt.close(fig)
