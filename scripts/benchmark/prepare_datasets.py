#!/usr/bin/env python3
"""
下载 / 准备泛化基准数据。

每个数据集产出：
  - raw.h5ad：给 Graph A 的原始 counts（或 Baron 的 log-normalized），仅 X + 基因符号，无预计算
  - gold_markers.json：标准预处理 + Leiden 后，在未 scale 的 log 表达矩阵上导出的 Wilcoxon 金标准 markers
  - ground_truth.json、meta.json：评估与指令

用法:
  uv run --package omnicell-agent python scripts/benchmark/prepare_datasets.py
  uv run --package omnicell-agent python scripts/benchmark/prepare_datasets.py --only pbmc3k paul15
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Monorepo 根与 backend 包根
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT / "src"))

import pandas as pd  # noqa: E402
import scanpy as sc  # noqa: E402
import anndata  # noqa: E402

if hasattr(anndata, "settings") and hasattr(anndata.settings, "allow_write_nullable_strings"):
    anndata.settings.allow_write_nullable_strings = True

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BENCHMARK_ROOT = PROJECT_ROOT / "data" / "benchmark"
PURITY_THRESHOLD = 0.6

# Baron 2016 胰腺 h5ad（Zenodo Besca 团队整理，含 cell_type 列；~227 MB）
PANCREAS_H5AD_URLS = (
    "https://zenodo.org/records/3968315/files/baron2016_raw.h5ad?download=true",
)

# Tabula Muris Senis 小鼠肺（cellxgene S3 公开桶；~305 MB，含 cell_type 列）
TMS_LUNG_H5AD_URL = (
    "https://cellxgene-census-public-us-west-2.s3.amazonaws.com/"
    "cell-census/2025-01-30/h5ads/0fb7916e-7a68-4a4c-a441-3ab3989f29a7.h5ad"
)


def _prune_uns_for_h5ad(adata: sc.AnnData) -> None:
    """
    h5ad 无法序列化「值为 dict 且键非字符串」的 uns（如 krumsiek11 的 highlights 用细胞索引作键）。
    """
    for k in list(adata.uns.keys()):
        v = adata.uns[k]
        if isinstance(v, dict) and v and not all(isinstance(x, str) for x in v.keys()):
            del adata.uns[k]
            logger.debug("Dropped uns[%r] (non-string dict keys)", k)


def _write_benchmark_h5ad(adata: sc.AnnData, path: Path) -> None:
    adata.obs_names_make_unique()
    _prune_uns_for_h5ad(adata)
    adata.write_h5ad(path, compression="gzip")


def _looks_like_hdf5(path: Path) -> bool:
    """HDF5 文件头；用于过滤 Figshare 返回的 HTML/错误页。"""
    try:
        with open(path, "rb") as f:
            return f.read(8) == b"\x89HDF\r\n\x1a\n"
    except OSError:
        return False


def _download_to_file(url: str, dest: Path, timeout: int = 600, retries: int = 3) -> None:
    """流式下载大文件，实时打印进度，校验完整性，自动重试。"""
    import sys
    import time
    import urllib.request

    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; OmniCell-Agent/0.1)"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                expected = resp.headers.get("Content-Length")
                total = int(expected) if expected else 0
                total_mb = f"{total / 1e6:.1f} MB" if total else "unknown size"
                logger.info(
                    "Downloading %s (%s) attempt %d/%d …",
                    dest.name, total_mb, attempt, retries,
                )
                received = 0
                t0 = time.time()
                last_print = t0
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        received += len(chunk)
                        now = time.time()
                        if now - last_print >= 2.0:
                            elapsed = now - t0
                            speed = received / elapsed / 1e6 if elapsed > 0 else 0
                            if total:
                                pct = received * 100 / total
                                sys.stderr.write(
                                    f"\r  {received/1e6:.1f}/{total/1e6:.1f} MB"
                                    f"  ({pct:.0f}%)  {speed:.1f} MB/s"
                                )
                            else:
                                sys.stderr.write(
                                    f"\r  {received/1e6:.1f} MB  {speed:.1f} MB/s"
                                )
                            sys.stderr.flush()
                            last_print = now
                sys.stderr.write("\n")
                sys.stderr.flush()
                if total and received < total:
                    raise IOError(
                        f"Incomplete download: got {received} bytes, expected {total}"
                    )
                logger.info("Downloaded %s (%.1f MB)", dest.name, received / 1e6)
            return
        except Exception as e:
            logger.warning("Download attempt %d failed: %s", attempt, e)
            if dest.is_file():
                dest.unlink(missing_ok=True)
            if attempt == retries:
                raise


def _strip_for_agent(adata: sc.AnnData, keep_obs: Optional[list] = None) -> sc.AnnData:
    """
    彻底剥离预计算结果，只保留表达矩阵 + 基因名 + 基础 QC 列，
    确保 Agent 必须从零开始做预处理/聚类/marker 提取。
    """
    import anndata as ad
    import scipy.sparse as sp

    X = adata.X.copy() if sp.issparse(adata.X) else adata.X.copy()
    var = adata.var[[]].copy()  # 只保留 index（gene symbols），丢掉所有 var 列

    safe_keep = {"n_genes", "n_counts", "percent_mito", "total_counts"}
    if keep_obs:
        safe_keep |= set(keep_obs)
    obs_cols = [c for c in adata.obs.columns if c in safe_keep]
    obs = adata.obs[obs_cols].copy() if obs_cols else pd.DataFrame(index=adata.obs.index)

    out = ad.AnnData(X=X, obs=obs, var=var)
    return out


def _majority_per_cluster(
    adata: sc.AnnData, cluster_key: str, ref_key: str
) -> Dict[str, Dict[str, Any]]:
    """每个 cluster 的多数票标签与纯度。"""
    gt: Dict[str, Dict[str, Any]] = {}
    for cid in adata.obs[cluster_key].astype(str).unique():
        mask = adata.obs[cluster_key].astype(str) == cid
        sub = adata.obs.loc[mask, ref_key].astype(str)
        vc = sub.value_counts()
        if len(vc) == 0:
            continue
        top = vc.index[0]
        purity = float(vc.iloc[0] / len(sub))
        ambiguous = purity < PURITY_THRESHOLD
        gt[str(cid)] = {
            "label": top,
            "purity": purity,
            "n_cells": int(mask.sum()),
            "ambiguous": ambiguous,
        }
    return gt


def _standard_rna_clustering(
    adata: sc.AnnData, leiden_key: str = "leiden", *, skip_normalize: bool = False
) -> sc.AnnData:
    """对原始 counts（或已 log1p 矩阵，见 skip_normalize）做标准预处理 + Leiden。

    返回一份用于差异分析的 log-normalized 全基因表达副本。聚类继续在
    HVG + scaled 矩阵上完成，但 marker 导出必须回到未 scale 的表达矩阵，
    避免把 scaled 值误当成表达检出率。
    """
    sc.pp.filter_cells(adata, min_genes=200)
    sc.pp.filter_genes(adata, min_cells=3)
    if adata.n_obs == 0:
        raise RuntimeError("No cells left after filtering")
    if not skip_normalize:
        sc.pp.normalize_total(adata, target_sum=1e4)
        sc.pp.log1p(adata)
    de_adata = adata.copy()
    sc.pp.highly_variable_genes(adata, min_mean=0.0125, max_mean=3, min_disp=0.5, subset=False)
    hv_mask = (
        adata.var["highly_variable"].to_numpy(dtype=bool)
        if "highly_variable" in adata.var
        else None
    )
    if hv_mask is not None and int(hv_mask.sum()) >= 2:
        adata._inplace_subset_var(hv_mask)
        adata.var["highly_variable"] = True
    else:
        logger.warning(
            "HVG selection returned too few genes; using all %d filtered genes for PCA",
            adata.n_vars,
        )
        adata.var["highly_variable"] = True
    sc.pp.scale(adata, max_value=10)
    n_comps = min(50, adata.n_obs - 1, adata.n_vars - 1)
    if n_comps < 1:
        raise RuntimeError(
            f"Not enough cells/genes for PCA after filtering: {adata.n_obs} cells, {adata.n_vars} genes"
        )
    sc.tl.pca(adata, n_comps=n_comps, svd_solver="arpack", random_state=42)
    sc.pp.neighbors(adata, n_neighbors=10, n_pcs=min(40, adata.obsm["X_pca"].shape[1]))
    sc.tl.leiden(adata, resolution=0.5, key_added=leiden_key, random_state=42)
    de_adata.obs[leiden_key] = pd.Categorical(
        adata.obs[leiden_key].astype(str).reindex(de_adata.obs_names)
    )
    return de_adata


def _export_gold_markers(adata: sc.AnnData, cluster_key: str, path: Path) -> None:
    """
    Wilcoxon rank_genes_groups + pct.1/pct.2，导出 JSON。

    输入应是未 scale 的 log-normalized 全基因表达矩阵；聚类标签可来自另一个
    HVG/scaled 工作副本。这样 `pct.1`/`pct.2` 才表示表达检出率，而不是
    scaled value 是否大于 0。
    """
    import numpy as np
    import scipy.sparse as sp

    if cluster_key not in adata.obs.columns:
        raise RuntimeError(f"obs 缺少聚类列 {cluster_key}")

    group_counts = adata.obs[cluster_key].value_counts()
    small_groups = group_counts[group_counts <= 1].index.tolist()
    all_groups = adata.obs[cluster_key].astype(str).unique().tolist()
    groups_to_analyze = [g for g in all_groups if g not in small_groups]

    out: List[Dict[str, Any]] = []
    if not groups_to_analyze:
        pd.DataFrame(
            columns=[
                "cluster_id",
                "gene_name",
                "p_val",
                "p_val_adj",
                "log2FC",
                "pct.1",
                "pct.2",
            ]
        ).to_json(path, orient="records", force_ascii=False)
        logger.warning("gold_markers: no analyzable groups, wrote empty %s", path)
        return

    sc.tl.rank_genes_groups(
        adata, cluster_key, method="wilcoxon", groups=groups_to_analyze
    )
    result = adata.uns["rank_genes_groups"]
    groups = result["names"].dtype.names
    var_set = set(adata.var_names.astype(str))

    for group in groups:
        df = pd.DataFrame(
            {
                "cluster": group,
                "names": result["names"][group],
                "scores": result["scores"][group],
                "pvals": result["pvals"][group],
                "pvals_adj": result["pvals_adj"][group],
                "logfoldchanges": result["logfoldchanges"][group],
            }
        )
        df["names"] = df["names"].astype(str)
        df = df[df["names"].isin(var_set)]
        if df.empty:
            continue
        cells_in = (adata.obs[cluster_key].astype(str) == str(group)).values
        cells_out = ~cells_in
        genes = df["names"].values
        X_genes = adata[:, list(genes)].X
        X_in = X_genes[cells_in, :]
        X_out = X_genes[cells_out, :]

        def _pct_expr(x):
            if sp.issparse(x):
                return np.asarray((x > 0).mean(axis=0)).ravel()
            return np.asarray((np.asarray(x) > 0).mean(axis=0)).ravel()

        df["pct.1"] = _pct_expr(X_in).round(3)
        df["pct.2"] = _pct_expr(X_out).round(3)

        df_f = df[(df["pvals_adj"] < 0.05) & (df["logfoldchanges"] > 1.0)].head(50)
        if df_f.empty:
            df_f = df[df["pvals_adj"] < 0.05].sort_values("pvals_adj").head(50)
        if df_f.empty:
            df_f = df.sort_values("pvals_adj").head(50)
        for _, row in df_f.iterrows():
            out.append(
                {
                    "cluster_id": str(group),
                    "gene_name": str(row["names"]),
                    "p_val": float(row["pvals"]),
                    "p_val_adj": float(row["pvals_adj"]),
                    "log2FC": float(row["logfoldchanges"]),
                    "pct.1": float(row["pct.1"]),
                    "pct.2": float(row["pct.2"]),
                }
            )

    payload = {
        "metadata": {
            "source": "prepare_datasets._export_gold_markers",
            "cluster_key": cluster_key,
        },
        "markers": out,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("Wrote gold_markers.json (%d rows) -> %s", len(out), path)


def prepare_pbmc3k(out_dir: Path) -> None:
    """raw.h5ad：pbmc3k_processed.raw（原始 counts + 全基因组）；gold 与 GT 由标准流程在副本上生成。"""
    logger.info("Preparing pbmc3k …")
    adata_full = sc.datasets.pbmc3k_processed()
    ref_key = "louvain"

    raw_adata = adata_full.raw.to_adata()
    raw_agent = _strip_for_agent(raw_adata)

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_benchmark_h5ad(raw_agent, out_dir / "raw.h5ad")

    work = raw_adata.copy()
    de_work = _standard_rna_clustering(work, "leiden_eval", skip_normalize=False)
    work.obs["_ref_label"] = adata_full.obs.loc[work.obs.index, ref_key].astype(str)

    gt = _majority_per_cluster(work, "leiden_eval", "_ref_label")
    _export_gold_markers(de_work, "leiden_eval", out_dir / "gold_markers.json")

    with open(out_dir / "ground_truth.json", "w", encoding="utf-8") as f:
        json.dump({"clusters": gt, "ref_column": ref_key, "cluster_key": "leiden_eval"}, f, indent=2)
    meta = {
        "dataset": "pbmc3k",
        "species": "Human",
        "tissue": "PBMC",
        "instruction": (
            "这里有一份人类外周血（PBMC）的单细胞测序数据。请完成预处理、聚类、差异基因分析并导出 markers，"
            "最后为各细胞群赋予免疫亚型身份并生成报告。"
        ),
        "skip_eval": False,
    }
    with open(out_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    logger.info("Wrote %s", out_dir)


def prepare_paul15(out_dir: Path) -> None:
    logger.info("Preparing paul15 …")
    adata = sc.datasets.paul15()
    ref_key = "paul15_clusters"
    ref_labels = adata.obs[ref_key].astype(str).copy()

    raw_agent = _strip_for_agent(adata)

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_benchmark_h5ad(raw_agent, out_dir / "raw.h5ad")

    work = adata.copy()
    de_work = _standard_rna_clustering(work, "leiden_eval", skip_normalize=False)
    work.obs["_ref_label"] = ref_labels.loc[work.obs.index]

    gt = _majority_per_cluster(work, "leiden_eval", "_ref_label")
    _export_gold_markers(de_work, "leiden_eval", out_dir / "gold_markers.json")

    with open(out_dir / "ground_truth.json", "w", encoding="utf-8") as f:
        json.dump({"clusters": gt, "ref_column": ref_key, "cluster_key": "leiden_eval"}, f, indent=2)
    meta = {
        "dataset": "paul15",
        "species": "Mouse",
        "tissue": "Bone marrow",
        "instruction": (
            "这是小鼠骨髓造血祖细胞的单细胞数据。请完成标准预处理、聚类与 marker 分析，"
            "导出 markers JSON，并对各群进行造血谱系注释。"
        ),
        "skip_eval": False,
    }
    with open(out_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    logger.info("Wrote %s", out_dir)


def _download_h5ad(urls: tuple | list, dest: Path) -> sc.AnnData:
    """依次尝试 URL 下载 h5ad，全部失败则抛异常。已有有效缓存则直接复用。"""
    if dest.is_file() and dest.stat().st_size > 8192 and _looks_like_hdf5(dest):
        logger.info("Reusing cached %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)
        return sc.read_h5ad(dest)
    for url in urls:
        try:
            logger.info("Trying %s", url)
            _download_to_file(url, dest)
            if dest.stat().st_size < 8192 or not _looks_like_hdf5(dest):
                raise ValueError("downloaded file is not valid HDF5")
            adata = sc.read_h5ad(dest)
            logger.info("OK  %s  (%d cells × %d genes)", dest.name, adata.n_obs, adata.n_vars)
            return adata
        except Exception as e:
            logger.warning("Skip %s: %s", url, e)
            if dest.is_file():
                dest.unlink(missing_ok=True)
    raise RuntimeError(f"All download URLs failed for {dest}")


def prepare_baron_pancreas(out_dir: Path) -> None:
    """Baron 2016 人 + 鼠胰腺 (GSE84133)，从 Zenodo 下载 Besca 整理版。"""
    logger.info("Preparing baron_pancreas …")
    out_dir.mkdir(parents=True, exist_ok=True)
    local_h5 = out_dir / "_pancreas_source.h5ad"

    env_local = (os.environ.get("OMNICELL_BENCHMARK_PANCREAS_H5AD") or "").strip()
    if env_local and Path(env_local).expanduser().is_file():
        adata = sc.read_h5ad(Path(env_local).expanduser())
        logger.info("Loaded from OMNICELL_BENCHMARK_PANCREAS_H5AD=%s", env_local)
    else:
        adata = _download_h5ad(PANCREAS_H5AD_URLS, local_h5)

    ref_key = None
    for k in ("cell_type", "celltype", "assigned_cluster", "str_label", "labels", "CellType"):
        if k in adata.obs:
            ref_key = k
            break
    if ref_key is None:
        raise RuntimeError(f"No cell type column found in obs: {list(adata.obs.columns)}")
    logger.info("ref_key=%s  unique types=%d", ref_key, adata.obs[ref_key].nunique())

    ref_labels = adata.obs[ref_key].astype(str).copy()
    if adata.raw is None:
        raise RuntimeError("baron_pancreas: expected .raw layer (log-normalized matrix)")
    logger.info("Using .raw layer (%d genes) as Agent input Besca log-normalized counts", adata.raw.n_vars)
    raw_adata = adata.raw.to_adata()

    raw_agent = _strip_for_agent(raw_adata)
    _write_benchmark_h5ad(raw_agent, out_dir / "raw.h5ad")

    work = raw_adata.copy()
    de_work = _standard_rna_clustering(work, "leiden_eval", skip_normalize=True)
    work.obs["_ref_label"] = ref_labels.reindex(work.obs.index).fillna("Unknown")

    gt = _majority_per_cluster(work, "leiden_eval", "_ref_label")
    _export_gold_markers(de_work, "leiden_eval", out_dir / "gold_markers.json")

    with open(out_dir / "ground_truth.json", "w", encoding="utf-8") as f:
        json.dump({"clusters": gt, "ref_column": ref_key, "cluster_key": "leiden_eval"}, f, indent=2)
    meta = {
        "dataset": "baron_pancreas",
        "species": "Human/Mouse",
        "tissue": "Pancreas",
        "instruction": (
            "这是胰腺单细胞数据（Baron et al. 2016, GSE84133）。"
            "请完成预处理、聚类、差异基因分析并导出 markers，为各细胞群赋予胰腺相关细胞类型注释。"
        ),
        "skip_eval": False,
        "data_note": (
            "raw.h5ad 来自 Besca 整理的 log-normalized 矩阵（非原始 UMI counts）；"
            "金标准管线跳过 normalize+log1p。"
        ),
    }
    with open(out_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    logger.info("Wrote %s", out_dir)



def prepare_tabula_muris_lung(out_dir: Path) -> None:
    """Tabula Muris Senis 小鼠肺（cellxgene 公开 S3），含 cell_type 注释。"""
    logger.info("Preparing tabula_muris_lung (real TMS lung) …")
    out_dir.mkdir(parents=True, exist_ok=True)
    local_h5 = out_dir / "_tms_lung_source.h5ad"

    env_local = (os.environ.get("OMNICELL_BENCHMARK_LUNG_H5AD") or "").strip()
    if env_local and Path(env_local).expanduser().is_file():
        adata = sc.read_h5ad(Path(env_local).expanduser())
        logger.info("Loaded from OMNICELL_BENCHMARK_LUNG_H5AD=%s", env_local)
    else:
        adata = _download_h5ad((TMS_LUNG_H5AD_URL,), local_h5)

    ref_key = None
    for k in ("cell_type", "celltype", "free_annotation", "cell_ontology_class"):
        if k in adata.obs:
            ref_key = k
            break
    if ref_key is None:
        raise RuntimeError(f"No cell type column found in obs: {list(adata.obs.columns)}")
    logger.info("ref_key=%s  unique types=%d", ref_key, adata.obs[ref_key].nunique())

    ref_labels = adata.obs[ref_key].astype(str).copy()
    if adata.raw is None:
        raise RuntimeError("tabula_muris_lung: expected .raw layer")
    raw_adata = adata.raw.to_adata()
    if "feature_name" in raw_adata.var.columns:
        raw_adata.var_names = raw_adata.var["feature_name"].astype(str)
        raw_adata.var_names_make_unique()
        logger.info("Converted raw var_names from Ensembl IDs to gene symbols (feature_name)")
    raw_agent = _strip_for_agent(raw_adata)
    _write_benchmark_h5ad(raw_agent, out_dir / "raw.h5ad")

    work = raw_adata.copy()
    de_work = _standard_rna_clustering(work, "leiden_eval", skip_normalize=False)
    work.obs["_ref_label"] = ref_labels.loc[work.obs.index]

    gt = _majority_per_cluster(work, "leiden_eval", "_ref_label")
    _export_gold_markers(de_work, "leiden_eval", out_dir / "gold_markers.json")

    with open(out_dir / "ground_truth.json", "w", encoding="utf-8") as f:
        json.dump({"clusters": gt, "ref_column": ref_key, "cluster_key": "leiden_eval"}, f, indent=2)
    meta = {
        "dataset": "tabula_muris_lung",
        "species": "Mouse",
        "tissue": "Lung",
        "instruction": (
            "这是小鼠肺组织单细胞数据（Tabula Muris Senis）。"
            "请完成预处理、聚类、marker 导出与细胞类型注释。"
        ),
        "skip_eval": False,
    }
    with open(out_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    logger.info("Wrote %s", out_dir)


def prepare_spatial_breast(out_dir: Path) -> None:
    """复用 scripts/prepare_spatial_data；raw.h5ad 为计数/原始层；GT 为伪标签（仅冒烟）。"""
    logger.info("Preparing spatial_breast …")
    spatial_default = PROJECT_ROOT / "data" / "spatial_sample.h5ad"
    if not spatial_default.exists():
        logger.warning("%s missing — run scripts/prepare_spatial_data.py first", spatial_default)
        sc.datasets.pbmc3k()
        adata = sc.datasets.pbmc3k_processed()
        sc.pp.subsample(adata, n_obs=400)
        adata.write_h5ad(spatial_default)
    adata = sc.read_h5ad(spatial_default)
    if adata.raw is not None:
        raw_adata = adata.raw.to_adata()
    else:
        raw_adata = adata.copy()

    raw_adata.var_names_make_unique()
    raw_agent = _strip_for_agent(raw_adata)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_benchmark_h5ad(raw_agent, out_dir / "raw.h5ad")

    work = raw_adata.copy()
    de_work = _standard_rna_clustering(work, "leiden_eval", skip_normalize=False)
    work.obs["_ref_label"] = work.obs["leiden_eval"].astype(str).map(lambda x: f"Leiden_{x}")
    gt = _majority_per_cluster(work, "leiden_eval", "_ref_label")
    _export_gold_markers(de_work, "leiden_eval", out_dir / "gold_markers.json")

    with open(out_dir / "ground_truth.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "clusters": gt,
                "ref_column": "pseudo_leiden",
                "cluster_key": "leiden_eval",
                "skip_eval": True,
                "note": "pseudo ground truth from Leiden id — only for pipeline smoke test",
            },
            f,
            indent=2,
        )
    meta = {
        "dataset": "spatial_breast",
        "species": "Human",
        "tissue": "Breast (Visium)",
        "instruction": (
            "这是空间转录组（Visium）数据。请完成空间相关预处理、聚类、marker 导出与注释。"
        ),
        "skip_eval": True,
    }
    with open(out_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    logger.info("Wrote %s", out_dir)


PREPARERS = {
    "pbmc3k": prepare_pbmc3k,
    "baron_pancreas": prepare_baron_pancreas,
    "paul15": prepare_paul15,
    "tabula_muris_lung": prepare_tabula_muris_lung,
    "spatial_breast": prepare_spatial_breast,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--only",
        nargs="*",
        default=list(PREPARERS.keys()),
        help="仅准备这些数据集",
    )
    args = ap.parse_args()

    BENCHMARK_ROOT.mkdir(parents=True, exist_ok=True)
    for name in args.only:
        if name not in PREPARERS:
            raise SystemExit(f"Unknown dataset: {name}. Choose from {list(PREPARERS)}")
        PREPARERS[name](BENCHMARK_ROOT / name)
    logger.info("Done.")


if __name__ == "__main__":
    main()
