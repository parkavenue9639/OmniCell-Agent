import json
import logging
import os
from typing import Any, Dict, List

from omnicell_agent.schema.state import SubGraphB_State

logger = logging.getLogger(__name__)


def _format_flags(flags: Any) -> str:
    if not flags:
        return "—"
    if isinstance(flags, list):
        return ", ".join(str(f) for f in flags)
    return str(flags)


def reporter_node(state: SubGraphB_State) -> Dict[str, Any]:
    """
    Sub-Graph B 总管节点: Reporter
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
        "# OmniCell-Agent 深度共识细胞鉴定报告 (Deep Annotation Report)",
        f"\n**Species**: `{species}` | **Tissue**: `{tissue}`",
        f"**Total Clusters Authenticated**: `{len(cluster_annotations)}`",
        "\n| Cluster ID | General Lineage | Specific Sub-Type | CS Score | Flags | Validated Evidence |",
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

        evidence = "✅ Verified"
        if isinstance(subtype, str) and "(Boosted)" in subtype:
            evidence = "⚠️ Escalate & Boosted"
        if isinstance(subtype, str) and "(NeedsReview)" in subtype:
            evidence = "⚠️ Needs manual review"
        if score < 60:
            evidence = "❌ High Hallucination Risk"
        if "cross_cluster_outlier" in flags:
            evidence = "⚠️ Cross-cluster outlier"

        flag_str = _format_flags(flags)

        report_lines.append(
            f"| {cid} | {general} | **{subtype}** | {score:.1f} | {flag_str} | {evidence} |"
        )

        needs_list = (
            score < 60.0
            or bool(flags)
            or (isinstance(subtype, str) and "(NeedsReview)" in subtype)
        )
        if needs_list:
            review_rows.append(
                f"- Cluster **{cid}**: `{subtype}` (score {score:.1f}) — flags: {flag_str or '—'}"
            )

    report_lines.append("\n## 需人工复核清单 (Manual review queue)\n")
    if review_rows:
        report_lines.extend(review_rows)
    else:
        report_lines.append("_No clusters flagged for mandatory review._")

    final_markdown = "\n".join(report_lines)

    dump_path = os.environ.get("OMNICELL_ANNOTATION_DUMP", "").strip()
    if dump_path:
        payload = {
            "species": species,
            "tissue": tissue,
            "cluster_annotations": cluster_annotations,
        }
        _parent = os.path.dirname(os.path.abspath(dump_path))
        if _parent:
            os.makedirs(_parent, exist_ok=True)
        with open(dump_path, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
        logger.info("annotation_result.json 已写入: %s", dump_path)

    logger.info("系统最终汇整验证报告已生成。")
    print("\n" + "=" * 80)
    print(final_markdown)
    print("=" * 80 + "\n")

    return {"final_report": final_markdown}
