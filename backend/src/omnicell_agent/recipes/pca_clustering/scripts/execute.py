import scanpy as sc
from pathlib import Path
import hashlib
import json
import numpy as np

output_root = Path(globals()['artifact_output_root'])
output_root.mkdir(parents=True, exist_ok=True)
n_top_genes = int(tool_parameters["n_top_genes"])
n_pcs = int(tool_parameters["n_pcs"])
n_neighbors = int(tool_parameters["n_neighbors"])
resolution = float(tool_parameters["resolution"])

# 保底拦截：检查核心变量
if 'adata' not in locals() and 'adata' not in globals():
    raw_data_path = globals().get('raw_data_path')
    if not raw_data_path:
        raise RuntimeError("RECIPE_INPUT_ERROR: raw_data_path is required")
    adata = sc.read_h5ad(raw_data_path)

# 只接受已经进入 log-expression 空间的数据。原子 Tool 的外层会做同样的
# 类型化检查；这里也保持 Recipe 单独执行时 fail-closed。
detector = globals().get("_atomic_detect_expression_space")
if callable(detector):
    input_expression_space, _ = detector(adata)
else:
    recorded_state = adata.uns.get("omnicell_scientific_state")
    recorded_state = recorded_state if isinstance(recorded_state, dict) else {}
    if recorded_state.get("expression_space") in {
        "normalized_log1p",
        "log1p_detected",
    }:
        input_expression_space = str(recorded_state["expression_space"])
    elif "log1p" in adata.uns:
        input_expression_space = "normalized_log1p"
    else:
        values = (
            np.asarray(adata.X.data)
            if hasattr(adata.X, "data") and not isinstance(adata.X, np.ndarray)
            else np.asarray(adata.X).ravel()
        )
        values = values[np.isfinite(values)]
        positive = values[values > 0][:200000]
        input_expression_space = "unknown"
        if positive.size:
            non_integer_fraction = float(
                np.mean(np.abs(positive - np.round(positive)) > 1e-3)
            )
            if float(np.max(positive)) <= 30.0 and non_integer_fraction >= 0.1:
                input_expression_space = "log1p_detected"
if input_expression_space == "unknown":
    raise RuntimeError(
        "RECIPE_INPUT_ERROR: clustering requires log-normalized expression"
    )

operation_signature = globals().get("_atomic_parameter_signature")
if not operation_signature:
    operation_signature = hashlib.sha256(
        json.dumps(
            tool_parameters,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
scientific_state = dict(
    adata.uns.get("omnicell_scientific_state") or {}
)
can_reuse = (
    "X_pca" in adata.obsm
    and "neighbors" in adata.uns
    and "connectivities" in adata.obsp
    and "distances" in adata.obsp
    and "leiden" in adata.obs
    and scientific_state.get("pca_signature") == operation_signature
    and scientific_state.get("clustering_signature") == operation_signature
)

if can_reuse:
    _atomic_operation_disposition = "reused"
    print("PCA, neighbor graph, and Leiden labels match the requested signature; reusing them.")
else:
    # 不复用无签名或参数不一致的历史结果，避免把旧聚类误报成当前请求。
    for key in ("X_pca", "X_umap"):
        if key in adata.obsm:
            del adata.obsm[key]
    if "leiden" in adata.obs:
        del adata.obs["leiden"]
    for key in ("pca", "neighbors", "leiden", "umap"):
        if key in adata.uns:
            del adata.uns[key]
    for key in ("connectivities", "distances"):
        if key in adata.obsp:
            del adata.obsp[key]

    if n_pcs >= min(int(adata.n_obs), int(adata.n_vars)):
        raise RuntimeError(
            "RECIPE_INPUT_ERROR: n_pcs must be smaller than both cells and genes"
        )
    sc.pp.highly_variable_genes(adata, n_top_genes=n_top_genes)
    sc.tl.pca(
        adata,
        n_comps=n_pcs,
        svd_solver="arpack",
        random_state=0,
    )
    sc.pp.neighbors(
        adata,
        n_neighbors=n_neighbors,
        n_pcs=n_pcs,
        random_state=0,
    )
    sc.tl.leiden(
        adata,
        resolution=resolution,
        flavor="igraph",
        n_iterations=2,
        directed=False,
        random_state=0,
    )
    scientific_state["pca_signature"] = operation_signature
    scientific_state["clustering_signature"] = operation_signature
    scientific_state["expression_space"] = input_expression_space
    adata.uns["omnicell_scientific_state"] = scientific_state
    _atomic_operation_disposition = "executed"
    print("PCA, Neighbors construction, and Leiden clustering finished.")

# --- 【核心视觉垫底拦截】防备大模型乱画 UMAP 被严苛的 Evaluator 驳回 ---
# 所有图像必须写入当前 invocation 的可写 artifact 目录。
sc.settings.figdir = str(output_root)
if 'spatial' in adata.obsm:
    sc.pl.spatial(adata, color='leiden', show=False, save='_spatial_domain.png')
else:
    if 'X_umap' not in adata.obsm or not can_reuse:
        sc.tl.umap(adata, random_state=0)
    sc.pl.umap(adata, color='leiden', show=False, save='_omnicell_umap.png')
