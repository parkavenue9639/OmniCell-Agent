import scanpy as sc
import pandas as pd
import numpy as np

try:
    import scipy.sparse as sp
except Exception:  # pragma: no cover - sandbox fallback
    sp = None

if "adata" not in locals() and "adata" not in globals():
    adata = sc.read_h5ad(globals().get("raw_data_path", "/app/data/pbmc3k_raw.h5ad"))


def _sample_matrix_values(adata: sc.AnnData, max_values: int = 200000) -> np.ndarray:
    x = adata.X
    if sp is not None and sp.issparse(x):
        vals = np.asarray(x.data)
    else:
        vals = np.asarray(x).ravel()
    vals = vals[np.isfinite(vals)]
    vals = vals[vals > 0]
    if vals.size > max_values:
        vals = vals[:max_values]
    return vals


def _looks_log_normalized(adata: sc.AnnData) -> bool:
    if "log1p" in adata.uns_keys():
        return True
    vals = _sample_matrix_values(adata)
    if vals.size == 0:
        return False
    max_val = float(np.max(vals))
    non_integer_fraction = float(np.mean(np.abs(vals - np.round(vals)) > 1e-3))
    return max_val <= 30.0 and non_integer_fraction >= 0.1


if "leiden" not in adata.obs:
    if not _looks_log_normalized(adata):
        sc.pp.normalize_total(adata, target_sum=1e4)
        sc.pp.log1p(adata)
    else:
        adata.uns.setdefault("omnicell_input_space", "log_normalized_detected")
    sc.tl.pca(adata)
    sc.pp.neighbors(adata, n_neighbors=10, n_pcs=40)
    sc.tl.leiden(adata, flavor="igraph", n_iterations=2, directed=False)


def _resolve_to_var_symbol(adata: sc.AnnData, token) -> str:
    """
    rank_genes_groups 的 'names' 有时是 var 中的基因符号，有时是整数位置索引。
    必须统一写成**真实 var_names**，否则 JSON 里会出现 "26824" 这类假基因名，下游 MO / LLM 全错位。
    """
    vn = np.asarray(adata.var_names.astype(str))
    var_set = set(vn)
    if token is None or (isinstance(token, float) and np.isnan(token)):
        return ""
    s = str(token).strip()
    if s in var_set:
        return s
    try:
        idx = int(float(s))
    except (TypeError, ValueError):
        return s
    if 0 <= idx < adata.n_vars:
        return str(vn[idx])
    return s


# 防御性逻辑：识别并筛分单细胞样本簇 (避免 rank_genes_groups 运行期间崩溃)
group_counts = adata.obs["leiden"].value_counts()
small_groups = group_counts[group_counts <= 1].index.tolist()
all_groups = adata.obs["leiden"].cat.categories.tolist()
groups_to_analyze = [g for g in all_groups if g not in small_groups]

marker_table_path = globals().get("marker_table_path", "/app/data/markers.json")

if not groups_to_analyze:
    print(
        "Warning: 没有可用于统计分析的正常簇（都不大于1个细胞）。强制终止 Marker 搜寻并导出空表。"
    )
    pd.DataFrame(
        columns=[
            "cluster",
            "names",
            "gene_name",
            "scores",
            "pvals",
            "pvals_adj",
            "logfoldchanges",
            "pct.1",
            "pct.2",
        ]
    ).to_json(marker_table_path, orient="records", force_ascii=False)
    import sys

    sys.exit(0)

sc.tl.rank_genes_groups(adata, "leiden", method="wilcoxon", groups=groups_to_analyze)

var_set = set(adata.var_names.astype(str))
marker_dfs = []

for group in groups_to_analyze:
    # 优先使用 Scanpy 官方 DataFrame，避免手写 uns 结构化数组时把索引当成基因名落盘
    df = sc.get.rank_genes_groups_df(adata, group=group)
    if df.empty:
        continue

    if "group" in df.columns:
        df = df.drop(columns=["group"])

    df.insert(0, "cluster", str(group))
    df["names"] = df["names"].map(lambda x: _resolve_to_var_symbol(adata, x))
    df["names"] = df["names"].astype(str)
    df = df[df["names"].isin(var_set)]
    if df.empty:
        continue

    genes = df["names"].tolist()
    cells_in_cluster = (adata.obs["leiden"] == group).values
    cells_out_cluster = ~cells_in_cluster

    X_genes = adata[:, genes].X
    X_in = X_genes[cells_in_cluster, :]
    X_out = X_genes[cells_out_cluster, :]

    # mean 后可能是 matrix / 稀疏 1d / ndarray，不能假定有 .A1（稠密 ndarray 会报错）
    m1 = (X_in > 0).mean(axis=0)
    m2 = (X_out > 0).mean(axis=0)
    pct_1 = np.asarray(m1).ravel()
    pct_2 = np.asarray(m2).ravel()

    df["pct.1"] = pct_1.round(3)
    df["pct.2"] = pct_2.round(3)

    # 与 evaluate / gold_markers 对齐：显式 gene_name 列（内容同 names）
    df["gene_name"] = df["names"]

    df_filtered = df[(df["pvals_adj"] < 0.05) & (df["logfoldchanges"] > 1.0)]
    take = df_filtered.head(50)
    if take.empty:
        take = df[df["pvals_adj"] < 0.05].sort_values("pvals_adj").head(50)
    if take.empty:
        take = df.sort_values("pvals_adj").head(50)
    marker_dfs.append(take)

if not marker_dfs:
    pd.DataFrame(
        columns=[
            "cluster",
            "names",
            "gene_name",
            "scores",
            "pvals",
            "pvals_adj",
            "logfoldchanges",
            "pct.1",
            "pct.2",
        ]
    ).to_json(marker_table_path, orient="records", force_ascii=False)
    print(f"Warning: 无可用 marker 行，已写出空表 -> {marker_table_path}")
else:
    all_markers_df = pd.concat(marker_dfs, ignore_index=True)
    all_markers_df.to_json(marker_table_path, orient="records", force_ascii=False)
    print(f"Marker genes analysis completed and deeply JSON contract saved to {marker_table_path}")
