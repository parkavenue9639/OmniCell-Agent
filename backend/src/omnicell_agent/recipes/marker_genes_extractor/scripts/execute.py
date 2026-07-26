import scanpy as sc
import pandas as pd
import numpy as np

if "adata" not in locals() and "adata" not in globals():
    raw_data_path = globals().get("raw_data_path")
    if not raw_data_path:
        raise RuntimeError("RECIPE_INPUT_ERROR: raw_data_path is required")
    adata = sc.read_h5ad(raw_data_path)

marker_method = str(tool_parameters["method"])
top_n_per_cluster = int(tool_parameters["top_n_per_cluster"])
adjusted_p_value_max = float(tool_parameters["adjusted_p_value_max"])
min_log2_fold_change = float(tool_parameters["min_log2_fold_change"])

if "leiden" not in adata.obs:
    raise RuntimeError(
        "RECIPE_INPUT_ERROR: marker extraction requires existing leiden clusters"
    )


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

marker_table_path = globals().get("marker_table_path")
if not marker_table_path:
    raise RuntimeError("RECIPE_INPUT_ERROR: marker_table_path is required")

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

sc.tl.rank_genes_groups(
    adata,
    "leiden",
    method=marker_method,
    groups=groups_to_analyze,
)

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

    df_filtered = df[
        (df["pvals_adj"] < adjusted_p_value_max)
        & (df["logfoldchanges"] > min_log2_fold_change)
    ]
    take = df_filtered.head(top_n_per_cluster)
    if take.empty:
        take = (
            df[df["pvals_adj"] < adjusted_p_value_max]
            .sort_values("pvals_adj")
            .head(top_n_per_cluster)
        )
    if take.empty:
        take = df.sort_values("pvals_adj").head(top_n_per_cluster)
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
