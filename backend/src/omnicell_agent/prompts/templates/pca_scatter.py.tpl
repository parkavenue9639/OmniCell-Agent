# 📈 [Template: 科学图表绘制标准规范与代码示例]
如果你的任务涉及绘制单细胞降维散点图（如 PCA 或 UMAP），为了确保生信审阅者的审核通过并防止引擎报错，你必须遵从如下代码框架。
它包含：强制内存变量复用、强制预处理计算链保底防报错、精美配色设计（附图表标题与图例）、以及用于唤醒拦截钩子 (Webhook) 的 `print` 输出逻辑。

```python
import scanpy as sc
import matplotlib.pyplot as plt

sc.settings.figdir = '/app/data/'

# 1. 保底拦截：检查核心变量
if 'adata' not in locals() and 'adata' not in globals():
    adata = sc.read_h5ad(raw_data_path)

# 2. 免疫报错机制：无论 Planner 有没有明说，你要给图表着色就必须先确保具备着色需要的降维或分群数据！
# 在画带有 color='leiden' 或者 'louvain' 的图之前，强制先运行配套处理，避免抛出 KeyError 崩溃！
if 'leiden' not in adata.obs:
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    sc.tl.pca(adata) # SVD solver defaulting is robust
    sc.pp.neighbors(adata, n_neighbors=10, n_pcs=40)
    sc.tl.leiden(adata)

# 3. 执行最终图表绘制并严格对齐文件名
# 注意这里我们不要依赖 sc.pl 自带的 save 魔法防止被魔改文件名，直接获取 fig 对象
# 强制开启高 DPI 与美观参数
sc.set_figure_params(dpi=300, facecolor='white', transparent=False)
fig, ax = plt.subplots(figsize=(8, 8))

# 【绝对强制】：即便 Planner 没有明确要求进行按照聚类等生物学特征着色，你也必须默认保留 color='leiden' 进行异质性上色！
# 严禁生成毫无着色分类的纯色白板散点图！！
# 使用适中的 size (例如 30) 和合适的 palette (比如 'tab20' 调色板) 展现科研级美感。
sc.pl.pca(adata, color='leiden', size=30, alpha=0.9, palette='tab20', ax=ax, show=False)

# 4. 图表审美的强制挽救措施：强制显示 x 与 y 上坐标轴刻度、隐藏冗余的头部与右侧脊线
ax.spines['right'].set_visible(False)
ax.spines['top'].set_visible(False)
ax.tick_params(axis='x', which='both', bottom=True, top=False, labelbottom=True)
ax.tick_params(axis='y', which='both', left=True, right=False, labelleft=True)
# 必须带图名
ax.set_title("PCA / Clustering Scatter Plot", fontsize=16, fontweight='bold', pad=15)

# 5. [致命规则] 保存盘与记录钩子！
target_filename = "current_pca.png"  # 根据用户的实际要求动态替换此处文件名
fig.savefig(f"/app/data/{target_filename}", bbox_inches='tight', dpi=300)
# 必须调用此行，打出精确日志以触发 Vision 节点嗅探
print(f"saving figure to file /app/data/{target_filename}")
plt.close(fig)
```
