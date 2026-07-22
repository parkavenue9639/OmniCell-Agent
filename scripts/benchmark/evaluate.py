#!/usr/bin/env python3
"""
汇总泛化基准实验指标：读取 annotation_result.json 与 data/benchmark/<ds>/ground_truth.json。

支持多轮实验汇总：当 condition 目录下存在 run_1/run_2/... 子目录时，
自动计算每条件的 mean ± std 并输出到 summary。

用法:
  uv run python scripts/benchmark/evaluate.py
  uv run python scripts/benchmark/evaluate.py --runs-root experiment_records/benchmark
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import linear_sum_assignment

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_BENCHMARK = PROJECT_ROOT / "data" / "benchmark"
DEFAULT_RUNS = PROJECT_ROOT / "experiment_records" / "benchmark"
SYNONYMS_PATH = Path(__file__).resolve().parent / "cell_type_synonyms.json"
HALLUCINATION_PATH = Path(__file__).resolve().parent / "hallucination_keywords.json"

# 有效 GT 簇数低于此阈值时在输出中标记 warning
MIN_VALID_GT_CLUSTERS = 4


def _load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _strip_suffixes(s: str) -> str:
    s = re.sub(r"\s*\(Boosted\).*$", "", s, flags=re.I)
    s = re.sub(r"\s*\(NeedsReview\).*$", "", s, flags=re.I)
    return s.strip()


def _ratio(a: str, b: str) -> float:
    a, b = a.lower().strip(), b.lower().strip()
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _synonym_match(pred: str, gt: str, synonyms: Dict[str, List[str]]) -> bool:
    pred_l, gt_l = pred.lower(), gt.lower()
    if pred_l == gt_l:
        return True
    for canon, alts in synonyms.items():
        pool = [canon.lower()] + [x.lower() for x in alts]
        if pred_l in pool and gt_l in pool:
            return True
        if gt_l == canon.lower() and any(pred_l == a.lower() for a in alts):
            return True
        if pred_l == canon.lower() and any(gt_l == a.lower() for a in alts):
            return True
    return False


def _infer_lineage_from_label(label: str, tissue: str = "") -> str:
    """Map a reference label string to a coarse lineage class for LMR (English keywords).

    When *tissue* is provided, context-sensitive disambiguation is applied:
    bare Greek-letter labels (alpha / beta / delta / gamma / epsilon) are mapped
    to 'Endocrine cells' **only** when tissue contains 'pancreas'; otherwise they
    fall through to hematopoietic or 'Other' matching.
    """
    s = label.lower()
    tissue_l = tissue.lower() if tissue else ""

    # --- Endocrine (pancreas-specific) ---
    endocrine_kw = [
        "islet", "endocrine", "insulin", "glucagon", "somatostatin",
        "pancreatic alpha", "pancreatic beta", "pancreatic delta",
        "pancreatic gamma",
    ]
    # Bare Greek letters → Endocrine ONLY when tissue is pancreas
    if s in ("alpha", "beta", "delta", "gamma", "epsilon"):
        if "pancreas" in tissue_l or "pancreatic" in tissue_l:
            return "Endocrine cells"
        # Otherwise fall through to hematopoietic / Other
    for kw in endocrine_kw:
        if kw in s:
            return "Endocrine cells"

    # --- Hematopoietic progenitors (paul15 etc.) ---
    # Paul15-style compact labels: <digits><abbreviation> (e.g., 1Ery, 7MEP, 11DC, 14Mo)
    paul15_match = re.match(r'^(\d+)([a-z]+)$', s)
    if paul15_match:
        abbr = paul15_match.group(2)
        _paul15_immune = {"ery", "mep", "gmp", "dc", "baso", "mo", "neu", "eos", "lymph", "mk"}
        if abbr in _paul15_immune:
            return "Immune cells"

    # General hematopoietic keywords (substring match)
    hema_kw = [
        "ery", "mep", "gmp", "cmp", "hsc", "mpp", "lmpp", "clp",
        "hspc", "progenitor",
    ]
    for kw in hema_kw:
        if kw in s:
            return "Immune cells"

    # --- Immune ---
    immune_kw = [
        "natural killer", "killer cell", "nk cell",
        "t cell", "b cell", "dendritic", "plasma cell",
        "neutro", "mono", "macro", "lymph",
        "erythroid", "myeloid", "gmp", "mep",
        "baso", "eos", "mast cell", "mast",
        "plasma", "immune",
    ]
    # Word-level NK (covers "NK cells", avoids missing "natural killer" substrings)
    if re.search(r"\bnk\b", s):
        return "Immune cells"
    # Word-level DC (covers "11DC" pattern from paul15)
    if re.search(r"\bdc\b", s):
        return "Immune cells"
    for kw in immune_kw:
        if kw in s:
            return "Immune cells"

    # --- Epithelial ---
    epithelial_kw = [
        "epithelial", "luminal", "ductal", "acinar",
        "malignant epithelial", "alveolar type",
        "type i pneumocyte", "type ii pneumocyte",
        "type 1 pneumocyte", "type 2 pneumocyte",
        "ciliated", "columnar", "goblet", "club cell",
        "basal cell", "pulmonary alveolar", "bronchiolar",
        "respiratory epithelial",
    ]
    for kw in epithelial_kw:
        if kw in s:
            return "Epithelial cells"

    # --- Stromal ---
    stromal_kw = [
        "fibroblast", "caf", "stromal", "matrix", "stellate",
        "smooth muscle", "pericyte", "myofibroblast", "adventitial",
        "mesenchymal", "interstitial fibroblast", "smooth muscle cell",
    ]
    for kw in stromal_kw:
        if kw in s:
            return "Stromal cells"

    # --- Endothelial ---
    endo_kw = ["endothelial", "lymphatic vessel", "blood vessel"]
    for kw in endo_kw:
        if kw in s:
            return "Endothelial cells"

    return "Other"


def _lineage_match(pred_general: str, gt_label: str, tissue: str = "") -> bool:
    pred_l = pred_general.lower()
    exp = _infer_lineage_from_label(gt_label, tissue)
    if exp == "Other":
        return _ratio(pred_general, gt_label) >= 0.5 or _ratio(pred_l, gt_label.lower()) >= 0.4
    return (
        exp.lower() in pred_l
        or pred_l in exp.lower()
        or _infer_lineage_from_label(pred_general, tissue) == exp
    )


def _substring_overlap_match(pred: str, gt: str, min_frac: float = 0.5) -> bool:
    """Substring match only if the shorter string is at least min_frac of the longer (reduces 'T cell' matching all)."""
    pl, gl = pred.lower().strip(), gt.lower().strip()
    if not pl or not gl:
        return False
    if pl in gl or gl in pl:
        shorter, longer = (len(pl), len(gl)) if len(pl) <= len(gl) else (len(gl), len(pl))
        return shorter >= min_frac * longer
    return False


def _fuzzy_subtype_match(pred: str, gt: str, synonyms: Dict[str, List[str]]) -> bool:
    pred = _strip_suffixes(pred)
    gt = _strip_suffixes(gt)
    if _synonym_match(pred, gt, synonyms):
        return True
    if _ratio(pred, gt) >= 0.75:
        return True
    if _substring_overlap_match(pred, gt, min_frac=0.5):
        return True
    return False


def _cluster_id_sort_key(cid: str) -> Tuple[int, Any]:
    s = str(cid)
    return (0, int(s)) if s.isdigit() else (1, s)


def _pair_score_for_alignment(
    pred: Dict[str, Any],
    gt_label: str,
    synonyms: Dict[str, List[str]],
    tissue: str = "",
) -> float:
    pred_sub = _strip_suffixes(str(pred.get("sub_type", "")))
    pred_gen = str(pred.get("general_type", ""))
    s = _ratio(pred_sub, gt_label)
    if _fuzzy_subtype_match(pred_sub, gt_label, synonyms):
        s += 2.0
    if _lineage_match(pred_gen, gt_label, tissue):
        s += 1.0
    return float(s)


def _hungarian_alignment_from_matrix(
    row_ids: List[str], col_ids: List[str], score_matrix: np.ndarray
) -> Tuple[Dict[str, str], float]:
    """Maximize total assignment score via linear_sum_assignment on negative scores.

    Returns (alignment_dict, margin) where margin is the difference between
    the optimal total score and the second-best assignment total score.
    """
    if score_matrix.size == 0:
        return {}, 0.0
    # Minimize cost = -score  <=>  maximize sum of scores
    row_ind, col_ind = linear_sum_assignment(-score_matrix)
    out: Dict[str, str] = {}
    optimal_total = 0.0
    for ri, ci in zip(row_ind.tolist(), col_ind.tolist()):
        out[row_ids[ri]] = col_ids[ci]
        optimal_total += score_matrix[ri, ci]

    # Compute margin: mask one pair at a time and re-solve to find second-best
    margin = float("inf")
    if len(row_ind) > 1:
        for idx in range(len(row_ind)):
            masked = score_matrix.copy()
            masked[row_ind[idx], col_ind[idx]] = -1e9
            _, col2 = linear_sum_assignment(-masked)
            alt_total = sum(masked[r, c] for r, c in zip(row_ind, col2))
            diff = optimal_total - alt_total
            if diff < margin:
                margin = diff
    else:
        # Single pair: margin is the score itself (no alternative)
        margin = optimal_total

    return out, float(margin)


def _build_alignment(
    clusters_pred: Dict[str, Any],
    gt_valid: Dict[str, Any],
    synonyms: Dict[str, List[str]],
    tissue: str = "",
) -> Tuple[Dict[str, str], float]:
    """Hungarian optimal one-to-one alignment: pred cluster -> GT cluster.

    Returns (alignment_dict, alignment_margin).
    """
    pred_ids = sorted(clusters_pred.keys(), key=_cluster_id_sort_key)
    gt_ids = sorted(gt_valid.keys(), key=_cluster_id_sort_key)
    if not pred_ids or not gt_ids:
        return {}, 0.0

    n_p, n_g = len(pred_ids), len(gt_ids)
    s = np.zeros((n_p, n_g), dtype=np.float64)
    gt_labels = [str(gt_valid[gid].get("label", "")) for gid in gt_ids]
    for i, pid in enumerate(pred_ids):
        pred = clusters_pred[pid]
        for j, gt_label in enumerate(gt_labels):
            s[i, j] = _pair_score_for_alignment(pred, gt_label, synonyms, tissue)

    return _hungarian_alignment_from_matrix(pred_ids, gt_ids, s)


def _markers_list_from_file(data: Any) -> List[Dict[str, Any]]:
    """兼容 [ {...}, ... ] 与 { \"markers\": [...] }。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "markers" in data:
        return list(data["markers"])
    return []


def marker_overlap_vs_gold(
    agent_markers_path: Path,
    gold_markers_path: Path,
    top_n: int = 20,
) -> Optional[float]:
    """
    按 cluster 计算 Agent markers 与 gold_markers 在 top-N 基因集合上的平均 Jaccard（Graph A 质量）。
    """
    ag = _load_json(agent_markers_path)
    gd = _load_json(gold_markers_path)
    if not ag or not gd:
        return None

    def _by_cluster(markers: List[Dict[str, Any]]) -> Dict[str, set]:
        by_c: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for m in markers:
            cid = str(m.get("cluster_id", m.get("cluster", "")))
            if not cid:
                continue
            by_c[cid].append(m)

        out: Dict[str, set] = {}
        for cid, lst in by_c.items():

            def _padj(x: Dict[str, Any]) -> float:
                try:
                    return float(x.get("p_val_adj", x.get("pvals_adj", 1.0)))
                except (TypeError, ValueError):
                    return 1.0

            lst.sort(key=_padj)
            names = []
            for x in lst[:top_n]:
                gn = x.get("gene_name", x.get("names", ""))
                if gn is not None and str(gn).strip():
                    names.append(str(gn).strip())
            out[cid] = set(names)
        return out

    a_list = _markers_list_from_file(ag)
    g_list = _markers_list_from_file(gd)
    if not a_list or not g_list:
        return None

    a_cl = _by_cluster(a_list)
    g_cl = _by_cluster(g_list)
    if not a_cl or not g_cl:
        return None

    if set(a_cl.keys()) == set(g_cl.keys()):
        scores: List[float] = []
        for cid in sorted(a_cl.keys(), key=lambda x: (len(x), x)):
            sa, sg = a_cl.get(cid, set()), g_cl.get(cid, set())
            if not sa and not sg:
                continue
            uni = sa | sg
            if not uni:
                continue
            scores.append(len(sa & sg) / len(uni))
        return sum(scores) / len(scores) if scores else None

    a_keys = sorted(a_cl.keys(), key=_cluster_id_sort_key)
    g_keys = sorted(g_cl.keys(), key=_cluster_id_sort_key)
    n_a, n_g = len(a_keys), len(g_keys)
    jmat = np.zeros((n_a, n_g), dtype=np.float64)
    for i, acid in enumerate(a_keys):
        sa = a_cl[acid]
        for j, gcid in enumerate(g_keys):
            sg = g_cl[gcid]
            uni = sa | sg
            jmat[i, j] = len(sa & sg) / len(uni) if uni else 0.0
    row_ind, col_ind = linear_sum_assignment(-jmat)
    aligned_scores = [float(jmat[ri, ci]) for ri, ci in zip(row_ind, col_ind)]
    return sum(aligned_scores) / len(aligned_scores) if aligned_scores else None


def _hallucination(
    tissue: str, sub_type: str, hall_map: Dict[str, List[str]]
) -> bool:
    st = sub_type.lower()
    keys = [tissue, tissue.split("(")[0].strip()]
    for k in keys:
        if k in hall_map:
            for bad in hall_map[k]:
                if bad.lower() in st:
                    return True
    return False


def evaluate_one(
    ann_path: Path,
    gt_data: Dict[str, Any],
    meta: Dict[str, Any],
    synonyms: Dict[str, List[str]],
    hall_map: Dict[str, List[str]],
) -> Dict[str, Any]:
    with open(ann_path, encoding="utf-8") as f:
        ann = json.load(f)
    clusters_pred: Dict[str, Any] = ann.get("cluster_annotations", {})
    gt_clusters: Dict[str, Any] = gt_data.get("clusters", {})

    if gt_data.get("skip_eval") or meta.get("skip_eval"):
        return {
            "dataset": meta.get("dataset", ""),
            "condition": meta.get("condition", ""),
            "run": meta.get("run", ""),
            "skip_eval": True,
            "n_clusters": len(clusters_pred),
        }

    tissue = str(meta.get("tissue", "") or ann.get("tissue", ""))
    condition = str(meta.get("condition", "") or ann.get("condition", ""))

    gt_valid = {k: v for k, v in gt_clusters.items() if not v.get("ambiguous")}

    alignment_margin = 0.0
    if set(clusters_pred.keys()) == set(gt_valid.keys()):
        alignment = {k: k for k in gt_valid}
    else:
        alignment, alignment_margin = _build_alignment(clusters_pred, gt_valid, synonyms, tissue)

    # Warning flag for insufficient valid GT clusters
    gt_warning = len(gt_valid) < MIN_VALID_GT_CLUSTERS

    lmr_num = lmr_den = 0
    sfm_num = sfm_den = 0
    hall_n = 0
    cs_scores: List[float] = []
    boost_n = 0
    flag_n = 0
    sfm_ratios: List[float] = []  # 连续值 ratio 列表

    for pred_cid, gt_cid in alignment.items():
        pred = clusters_pred[pred_cid]
        gt_c = gt_valid[gt_cid]
        gt_label = str(gt_c.get("label", ""))
        sub = str(pred.get("sub_type", ""))
        gen = str(pred.get("general_type", ""))

        if _lineage_match(gen, gt_label, tissue):
            lmr_num += 1
        lmr_den += 1

        sub_clean = _strip_suffixes(sub)
        gt_clean = _strip_suffixes(gt_label)
        r = _ratio(sub_clean, gt_clean)
        sfm_ratios.append(r)

        if _fuzzy_subtype_match(sub, gt_label, synonyms):
            sfm_num += 1
        sfm_den += 1

        if _hallucination(tissue, sub, hall_map):
            hall_n += 1

    matched_gt = set(alignment.values())
    for gid in gt_valid:
        if gid not in matched_gt:
            lmr_den += 1
            sfm_den += 1
            sfm_ratios.append(0.0)  # 未配对 GT 簇得 0 分

    for cid, pred in clusters_pred.items():
        try:
            cs_scores.append(float(pred.get("cs_score", 0.0)))
        except (TypeError, ValueError):
            pass
        flags = pred.get("flags") or []
        if isinstance(flags, list) and "boosted" in flags:
            boost_n += 1
        if isinstance(flags, list) and len(flags) > 0:
            flag_n += 1

    n = max(len(clusters_pred), 1)
    # Baseline hard-codes cs_score=100 in run_baseline_annotation.py; exclude from mean_cs comparison.
    mean_cs_out: Optional[float] = None
    if condition != "baseline" and cs_scores:
        mean_cs_out = sum(cs_scores) / len(cs_scores)

    return {
        "dataset": meta.get("dataset", ""),
        "condition": meta.get("condition", ""),
        "run": meta.get("run", ""),
        "skip_eval": False,
        "LMR": (lmr_num / lmr_den) if lmr_den else None,
        "SFM": (sfm_num / sfm_den) if sfm_den else None,
        "mean_sfm_ratio": (sum(sfm_ratios) / len(sfm_ratios)) if sfm_ratios else None,
        "HR": hall_n / n,
        "mean_cs": mean_cs_out,
        "boost_rate": boost_n / n,
        "flag_rate": flag_n / n,
        "n_clusters": len(clusters_pred),
        "n_aligned": len(alignment),
        "alignment_margin": round(alignment_margin, 4),
        "n_valid_gt": len(gt_valid),
        "gt_warning": gt_warning,
    }


def _find_annotation_results(cond_dir: Path) -> List[Tuple[Path, Optional[str]]]:
    """查找一个条件目录下的所有 annotation_result.json。

    支持两种结构：
    - 单次: cond_dir/annotation_result.json → [(path, None)]
    - 多轮: cond_dir/run_1/annotation_result.json, cond_dir/run_2/... → [(path, "run_1"), ...]
    """
    direct = cond_dir / "annotation_result.json"
    if direct.is_file():
        return [(direct, None)]

    results = []
    for sub in sorted(cond_dir.iterdir()):
        if sub.is_dir() and sub.name.startswith("run_"):
            ann = sub / "annotation_result.json"
            if ann.is_file():
                results.append((ann, sub.name))
    return results


def _aggregate_runs(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """将同一 dataset+condition 的多轮结果汇总为 mean ± std。"""
    if not rows:
        return {}
    if len(rows) == 1:
        return rows[0]

    agg: Dict[str, Any] = {
        "dataset": rows[0]["dataset"],
        "condition": rows[0]["condition"],
        "run": f"agg({len(rows)})",
        "skip_eval": rows[0].get("skip_eval", False),
    }

    numeric_keys = ["LMR", "SFM", "mean_sfm_ratio", "HR", "mean_cs",
                     "boost_rate", "flag_rate", "n_clusters", "n_aligned",
                     "alignment_margin", "n_valid_gt", "MO"]
    for k in numeric_keys:
        vals = [r[k] for r in rows if r.get(k) is not None]
        if vals:
            mean = sum(vals) / len(vals)
            if len(vals) > 1:
                std = (sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5
                agg[k] = round(mean, 4)
                agg[f"{k}_std"] = round(std, 4)
            else:
                agg[k] = round(mean, 4)
        else:
            agg[k] = None

    agg["gt_warning"] = any(r.get("gt_warning", False) for r in rows)
    return agg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS)
    ap.add_argument("--out-csv", type=Path, default=DEFAULT_RUNS / "results_summary.csv")
    ap.add_argument("--out-md", type=Path, default=DEFAULT_RUNS / "results_summary.md")
    args = ap.parse_args()

    synonyms = _load_json(SYNONYMS_PATH) or {}
    hall_map = _load_json(HALLUCINATION_PATH) or {}

    raw_rows: List[Dict[str, Any]] = []
    args.runs_root.mkdir(parents=True, exist_ok=True)

    for ds_dir in sorted(args.runs_root.iterdir()):
        if not ds_dir.is_dir() or ds_dir.name.startswith(".") or ds_dir.suffix == ".csv":
            continue
        gt_path = DATA_BENCHMARK / ds_dir.name / "ground_truth.json"
        meta_path_ds = DATA_BENCHMARK / ds_dir.name / "meta.json"
        gt_data = _load_json(gt_path) or {}
        meta_ds = _load_json(meta_path_ds) or {}

        for cond_dir in sorted(ds_dir.iterdir()):
            if not cond_dir.is_dir():
                continue
            ann_entries = _find_annotation_results(cond_dir)
            if not ann_entries:
                continue

            for ann_path, run_label in ann_entries:
                meta = dict(meta_ds)
                meta["dataset"] = ds_dir.name
                meta["condition"] = cond_dir.name
                if run_label:
                    meta["run"] = run_label
                    mpath = ann_path.parent / "meta.json"
                else:
                    mpath = cond_dir / "meta.json"
                if mpath.is_file():
                    meta.update(_load_json(mpath) or {})

                row = evaluate_one(ann_path, gt_data, meta, synonyms, hall_map)
                row["path"] = str(ann_path.relative_to(PROJECT_ROOT))

                if meta.get("condition") == "e2e":
                    gold_mp = DATA_BENCHMARK / ds_dir.name / "gold_markers.json"
                    agent_mp = DATA_BENCHMARK / ds_dir.name / "markers.json"
                    mo = marker_overlap_vs_gold(agent_mp, gold_mp)
                    if mo is not None:
                        row["MO"] = round(mo, 4)

                raw_rows.append(row)

    if not raw_rows:
        print("No annotation_result.json found under", args.runs_root)
        return

    # 输出原始行（per-run）
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({k for r in raw_rows for k in r.keys()})
    with open(args.out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(raw_rows)

    # 多轮汇总
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in raw_rows:
        key = (r.get("dataset", ""), r.get("condition", ""))
        grouped[key].append(r)

    agg_rows = []
    for key in sorted(grouped.keys()):
        runs = grouped[key]
        if len(runs) > 1:
            agg_rows.append(_aggregate_runs(runs))
        else:
            agg_rows.append(runs[0])

    # 生成 markdown summary
    def _fmt(val: Any, key: str = "", row: Dict[str, Any] = {}) -> str:
        if val is None:
            return "N/A"
        if isinstance(val, bool):
            return "⚠️" if val else ""
        if isinstance(val, float):
            formatted = f"{val:.4f}"
            # 附加 ± std（如果存在）
            std_key = f"{key}_std"
            if std_key in row and row[std_key] is not None:
                formatted += f" ±{row[std_key]:.4f}"
            return formatted
        return str(val)

    lines = [
        "# Benchmark summary",
        "",
        "| dataset | condition | run | LMR | SFM | mean_sfm_ratio | HR | mean_cs | boost_rate | MO | alignment_margin | n_clusters | n_aligned | n_valid_gt | gt_warning |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in agg_rows:
        lines.append(
            "| {ds} | {cond} | {run} | {lmr} | {sfm} | {msr} | {hr} | {mcs} | {br} | {mo} | {am} | {nc} | {na} | {nvg} | {gw} |".format(
                ds=r.get("dataset", ""),
                cond=r.get("condition", ""),
                run=r.get("run", ""),
                lmr=_fmt(r.get("LMR"), "LMR", r),
                sfm=_fmt(r.get("SFM"), "SFM", r),
                msr=_fmt(r.get("mean_sfm_ratio"), "mean_sfm_ratio", r),
                hr=_fmt(r.get("HR"), "HR", r),
                mcs=_fmt(r.get("mean_cs"), "mean_cs", r),
                br=_fmt(r.get("boost_rate"), "boost_rate", r),
                mo=_fmt(r.get("MO"), "MO", r),
                am=_fmt(r.get("alignment_margin"), "alignment_margin", r),
                nc=_fmt(r.get("n_clusters")),
                na=_fmt(r.get("n_aligned")),
                nvg=_fmt(r.get("n_valid_gt")),
                gw=_fmt(r.get("gt_warning")),
            )
        )
    args.out_md.write_text("\n".join(lines), encoding="utf-8")
    print("Wrote", args.out_csv, "and", args.out_md)


if __name__ == "__main__":
    main()
