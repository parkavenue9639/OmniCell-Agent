import scanpy as sc
import numpy as np
import hashlib
import json

# 保底拦截：检查核心变量
if 'adata' not in locals() and 'adata' not in globals():
    raw_data_path = globals().get('raw_data_path')
    if not raw_data_path:
        raise RuntimeError("RECIPE_INPUT_ERROR: raw_data_path is required")
    adata = sc.read_h5ad(raw_data_path)

# 1. 标记线粒体基因：兼容人类 MT- 与小鼠 mt- 命名，并转成标准 numpy bool
mt_mask = adata.var_names.str.startswith(("MT-", "mt-"))
adata.var['mt'] = np.asarray(mt_mask, dtype=bool)
# 2. 计算质控指标
sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], percent_top=None, log1p=False, inplace=True)

# 3. 基础过滤: 参数来自类型化 Tool 请求
min_genes_per_cell = int(tool_parameters["min_genes_per_cell"])
min_cells_per_gene = int(tool_parameters["min_cells_per_gene"])
max_mito_percent = float(tool_parameters["max_mito_percent"])
sc.pp.filter_cells(adata, min_genes=min_genes_per_cell)
sc.pp.filter_genes(adata, min_cells=min_cells_per_gene)
# 4. 过滤线粒体基因比例过高的死细胞/濒死细胞
adata = adata[adata.obs.pct_counts_mt < max_mito_percent, :].copy()
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
scientific_state["quality_control_signature"] = operation_signature
adata.uns["omnicell_scientific_state"] = scientific_state
_atomic_operation_disposition = "executed"
print(f"QC and Filter applied. Remaining cells: {adata.n_obs}, genes: {adata.n_vars}")
