import scanpy as sc
import numpy as np

try:
    import scipy.sparse as sp
except Exception:  # pragma: no cover - sandbox fallback
    sp = None

# 保底拦截：检查核心变量
if 'adata' not in locals() and 'adata' not in globals():
    adata = sc.read_h5ad(globals().get('raw_data_path', '/app/data/pbmc3k_raw.h5ad'))

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
    if 'log1p' in adata.uns_keys():
        return True
    vals = _sample_matrix_values(adata)
    if vals.size == 0:
        return False
    max_val = float(np.max(vals))
    non_integer_fraction = float(np.mean(np.abs(vals - np.round(vals)) > 1e-3))
    # log1p-normalized scRNA matrices usually have compact positive values and
    # many decimals even when uns["log1p"] was stripped during benchmark prep.
    return max_val <= 30.0 and non_integer_fraction >= 0.1


# 标准化与对数转换
if not _looks_log_normalized(adata):
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    print("Normalization and log1p completed.")
else:
    adata.uns.setdefault('omnicell_input_space', 'log_normalized_detected')
    print("Data already seems to be log-transformed, skipping normalization steps to prevent over-flattening.")
