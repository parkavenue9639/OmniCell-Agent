import scanpy as sc
import numpy as np
import hashlib
import json

try:
    import scipy.sparse as sp
except Exception:  # pragma: no cover - sandbox fallback
    sp = None

# 保底拦截：检查核心变量
if 'adata' not in locals() and 'adata' not in globals():
    raw_data_path = globals().get('raw_data_path')
    if not raw_data_path:
        raise RuntimeError("RECIPE_INPUT_ERROR: raw_data_path is required")
    adata = sc.read_h5ad(raw_data_path)

def _sample_matrix_values(adata, max_values=200000):
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


def _looks_log_normalized(adata) -> bool:
    vals = _sample_matrix_values(adata)
    if vals.size == 0:
        return False
    max_val = float(np.max(vals))
    non_integer_fraction = float(np.mean(np.abs(vals - np.round(vals)) > 1e-3))
    # log1p-normalized scRNA matrices usually have compact positive values and
    # many decimals even when uns["log1p"] was stripped during benchmark prep.
    return max_val <= 30.0 and non_integer_fraction >= 0.1


detector = globals().get("_atomic_detect_expression_space")
if callable(detector):
    input_expression_space, _ = detector(adata)
else:
    input_expression_space = (
        "log1p_detected" if _looks_log_normalized(adata) else "unknown"
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

# 标准化与对数转换
scientific_state = dict(
    adata.uns.get("omnicell_scientific_state") or {}
)
if input_expression_space == "unknown":
    sc.pp.normalize_total(adata, target_sum=float(tool_parameters["target_sum"]))
    sc.pp.log1p(adata)
    scientific_state["expression_space"] = "normalized_log1p"
    scientific_state["normalization_signature"] = operation_signature
    _atomic_operation_disposition = "executed"
    print("Normalization and log1p completed.")
else:
    scientific_state["expression_space"] = input_expression_space
    _atomic_operation_disposition = "reused"
    print(
        "Data already has a log-expression space; "
        "reusing it without repeating normalization."
    )
adata.uns["omnicell_scientific_state"] = scientific_state
