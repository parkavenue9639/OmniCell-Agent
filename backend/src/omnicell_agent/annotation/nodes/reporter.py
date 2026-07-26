import logging
from typing import Any, Dict, List

from omnicell_agent.schema.state import CellAnnotationState

logger = logging.getLogger(__name__)


def _format_flags(flags: Any) -> str:
    if not flags:
        return "—"
    if isinstance(flags, list):
        return ", ".join(str(f) for f in flags)
    return str(flags)


def reporter_node(state: CellAnnotationState) -> Dict[str, Any]:
    """
    细胞注释内部聚合节点：Reporter。
    汇总并发鉴定结果，输出 Markdown 报告与人工复核清单。
    """
    logger.info("--- NODE: REPORTER (Aggregating Multi-Agent Results) ---")

    cluster_annotations = state.get("cluster_annotations", {})
    species = state.get("species", "Unknown")
    tissue = state.get("tissue", "Unknown")

    if not cluster_annotations:
        logger.warning("未收到任何有效的簇鉴定汇总！")
        return {"final_report": "Error: No valid cluster annotations found."}

    report_lines = [
        "# OmniCell-Agent 细胞类型暂定注释报告",
        f"\n**Species**: `{species}` | **Tissue**: `{tissue}`",
        f"**Total Clusters Annotated**: `{len(cluster_annotations)}`",
        (
            "\n> 注：Evidence Score 是内部启发式证据评分，不是校准概率；"
            "所有标签均应结合原始 marker 和实验背景解释。"
        ),
        "\n| Cluster ID | General Lineage | Candidate Sub-Type | Evidence Score | Flags | Review Status |",
        "| :---: | :--- | :--- | :---: | :--- | :--- |",
    ]

    try:
        sorted_items = sorted(cluster_annotations.items(), key=lambda x: int(x[0]))
    except ValueError:
        sorted_items = sorted(cluster_annotations.items())

    review_rows: List[str] = []

    for cid, ann in sorted_items:
        general = ann.get("general_type", "Unknown")
        subtype = ann.get("sub_type", "Unknown")
        try:
            score = float(ann.get("cs_score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        flags = ann.get("flags") or []

        evidence = "No automatic review flag"
        if "boosted" in flags:
            evidence = "Reassessed; inspect evidence"
        if "needs_review" in flags or score < 75:
            evidence = "Manual review recommended"
        if "cross_cluster_outlier" in flags:
            evidence = "Cross-cluster outlier; review"

        flag_str = _format_flags(flags)

        report_lines.append(
            f"| {cid} | {general} | **{subtype}** | {score:.1f} | {flag_str} | {evidence} |"
        )

        needs_list = (
            score < 75.0
            or bool(flags)
        )
        if needs_list:
            review_rows.append(
                f"- Cluster **{cid}**: `{subtype}` (score {score:.1f}) — flags: {flag_str or '—'}"
            )

    report_lines.append("\n## 需人工复核清单 (Manual review queue)\n")
    if review_rows:
        report_lines.extend(review_rows)
    else:
        report_lines.append("_没有 cluster 被自动规则标记为需要人工复核。_")

    final_markdown = "\n".join(report_lines)

    logger.info("系统最终汇整验证报告已生成。")
    print("\n" + "=" * 80)
    print(final_markdown)
    print("=" * 80 + "\n")

    return {"final_report": final_markdown}
