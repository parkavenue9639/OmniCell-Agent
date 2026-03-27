import logging
from typing import Dict, Any

from omnicell_agent.schema.state import SubGraphB_State

logger = logging.getLogger(__name__)

def reporter_node(state: SubGraphB_State) -> Dict[str, Any]:
    """
    Sub-Graph B 总管节点: Reporter (收集聚合中心)
    作为整个 Map-Reduce 并发图谱的终点，它将回收散发出去打架的 `Annotation_State` 所提交的汇总结果。
    格式化输出一份科研级别的 Markdown Markdown 报告。
    """
    logger.info("--- NODE: REPORTER (Aggregating Multi-Agent Results) ---")
    
    cluster_annotations = state.get("cluster_annotations", {})
    species = state.get("species", "Unknown")
    tissue = state.get("tissue", "Unknown")
    
    if not cluster_annotations:
        logger.warning("未收到任何有效的簇鉴定汇总！")
        return {"final_report": "Error: No valid cluster annotations found."}
    
    # 构造 Markdown 报告
    report_lines = [
        f"# OmniCell-Agent 深度共识细胞鉴定报告 (Deep Annotation Report)",
        f"\n**Species**: `{species}` | **Tissue**: `{tissue}`",
        f"**Total Clusters Authenticated**: `{len(cluster_annotations)}`",
        f"\n| Cluster ID | General Lineage | Specific Sub-Type | CS Score (0-100) | Validated Evidence |",
        f"| :---: | :--- | :--- | :---: | :--- |"
    ]
    
    # 将字典按 Cluster ID 排序展示
    try:
        sorted_items = sorted(cluster_annotations.items(), key=lambda x: int(x[0]))
    except ValueError:
        sorted_items = sorted(cluster_annotations.items())
        
    for cid, ann in sorted_items:
        general = ann.get("general_type", "Unknown")
        subtype = ann.get("sub_type", "Unknown")
        score = ann.get("cs_score", 0.0)
        
        # 简化证据列：是否成功，是否被 Boost 介入抢救
        evidence = "✅ Verified"
        if "(Boosted)" in subtype:
            evidence = "⚠️ Escalate & Boosted"
        elif score < 60:
            evidence = "❌ High Hallucination Risk"
            
        report_lines.append(f"| {cid} | {general} | **{subtype}** | {score:.1f} | {evidence} |")
        
    final_markdown = "\n".join(report_lines)
    
    logger.info("系统最终汇整验证报告已生成。")
    print("\n" + "="*80)
    print(final_markdown)
    print("="*80 + "\n")
    
    return {"final_report": final_markdown}
