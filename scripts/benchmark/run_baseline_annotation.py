#!/usr/bin/env python3
"""
LLM 直标 baseline：无 Validator / Scorer / Boost / ConsistencyReviewer。
对每个 cluster 仅一次 LLM 调用（与 Annotator 相同的 structured output）。

用法:
  uv run --package omnicell-agent python scripts/benchmark/run_baseline_annotation.py \\
    --markers-json data/benchmark/pbmc3k/gold_markers.json \\
    --output experiment_records/benchmark/pbmc3k_baseline.json \\
    --species Human --tissue PBMC
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT / "src"))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from omnicell_agent import llm  # noqa: E402
from omnicell_agent.schema.contract import MarkerTableContract  # noqa: E402
from omnicell_agent.annotation.nodes.annotator import AnnotationOutput  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TOP_N = 20


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markers-json", type=str, required=True)
    ap.add_argument("--output", type=str, required=True)
    ap.add_argument("--species", type=str, default="Human")
    ap.add_argument("--tissue", type=str, default="PBMC")
    args = ap.parse_args()

    contract = MarkerTableContract.load_from_json(args.markers_json)
    by_cid: dict[str, list] = {}
    for m in contract.markers:
        by_cid.setdefault(str(m.cluster_id), []).append(m)
    for cid in by_cid:
        by_cid[cid].sort(key=lambda x: x.p_val_adj)

    cluster_annotations: dict[str, dict] = {}
    model = llm.get_llm_by_alias(llm.LLMRole.ANNOTATION, temperature=0.1)
    structured = model.with_structured_output(AnnotationOutput)

    for cid, markers in sorted(by_cid.items(), key=lambda x: int(x[0]) if x[0].isdigit() else x[0]):
        top_n = [m.gene_name for m in markers[:TOP_N]]
        system_prompt = (
            "You are an expert single-cell biologist and a rigorous cell type annotator. "
            f"Your task is to annotate a specific cell cluster from a {args.species} {args.tissue} sample.\n"
            "You will be provided with the top differentially expressed marker genes for this cluster.\n"
            "Follow Chain-of-Thought (CoT) and list marker_evidence for your chosen sub_type."
        )
        user_prompt = (
            f"Top Marker Genes for Cluster {cid}:\n{', '.join(top_n)}\n\n"
            "Provide reasoning, marker_evidence, general_type, and sub_type."
        )
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        out: AnnotationOutput = structured.invoke(messages)
        cluster_annotations[cid] = {
            "general_type": out.general_type,
            "sub_type": out.sub_type,
            "reasoning_chain": out.reasoning_chain,
            "marker_evidence": out.marker_evidence,
            "cs_score": 100.0,
            "self_consistency_ok": 1.0,
            "flags": ["baseline_direct_llm"],
        }

    payload = {
        "species": args.species,
        "tissue": args.tissue,
        "condition": "baseline",
        "cluster_annotations": cluster_annotations,
    }
    outp = Path(args.output)
    outp.parent.mkdir(parents=True, exist_ok=True)
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("Wrote %s", outp)


if __name__ == "__main__":
    main()
