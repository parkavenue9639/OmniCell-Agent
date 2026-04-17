import logging
import os
import re
import json
from typing import Dict, Any, Optional

from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field

from omnicell_agent.schema.state import DataPipeline_State
from omnicell_agent.core.llm_client import LLMSelector
from omnicell_agent.core.trace_logger import trace_logger

logger = logging.getLogger(__name__)


class ContextProfile(BaseModel):
    """
    结构化上下文剖面：由 LLM 从用户 prompt + h5ad 轻量元数据中抽取，
    用以替代此前通过 CLI 强制传入的 species / tissue 等参数。
    """
    species: str = Field(
        default="Human",
        description="标准物种英文名，如 Human / Mouse。完全无法判断时填 Unknown。",
    )
    tissue: str = Field(
        default="Unknown",
        description=(
            "样本组织或采样来源的规范英文简称，从如下受控集中尽量取一项: "
            "PBMC / Peripheral Blood / Blood / Bone Marrow / Breast Cancer / "
            "Lung Cancer / Liver / Gastric / Brain / Spatial / Unknown。"
        ),
    )
    disease_state: str = Field(
        default="Unknown",
        description="疾病状态或处理条件的简述；若无法判断则填 Unknown，不得臆造。",
    )
    goal_type: str = Field(
        default="general_annotation",
        description=(
            "用户目标类型（小写下划线）。示例: immune_profiling / tumor_microenv / "
            "general_annotation / marker_discovery / spatial_domain_identification。"
        ),
    )


_TISSUE_NORMALIZATION = [
    (re.compile(r"\bpbmc\b|peripheral\s+blood\s+mono", re.I), "PBMC"),
    (re.compile(r"\bperipheral\s+blood\b", re.I), "Peripheral Blood"),
    (re.compile(r"\bwhole\s+blood\b|^\s*blood\s*$", re.I), "Blood"),
    (re.compile(r"\bbone\s+marrow\b", re.I), "Bone Marrow"),
    (re.compile(r"\bbreast\s+(cancer|tumor|carcinoma)\b", re.I), "Breast Cancer"),
    (re.compile(r"\blung\s+(cancer|tumor|carcinoma|adeno\w*)\b", re.I), "Lung Cancer"),
    (re.compile(r"\bgastric\b|\bstomach\b", re.I), "Gastric"),
    (re.compile(r"\bliver\b|\bhcc\b|\bhepat", re.I), "Liver"),
    (re.compile(r"\bbrain\b|\bglioma\b|\bglioblast", re.I), "Brain"),
    (re.compile(r"\bspatial\b|\bvisium\b|\bxenium\b|\bmerfish\b", re.I), "Spatial"),
]


def _normalize_tissue(raw: str) -> str:
    if not raw:
        return "Unknown"
    for pat, canon in _TISSUE_NORMALIZATION:
        if pat.search(raw):
            return canon
    cleaned = raw.strip()
    return cleaned if cleaned else "Unknown"


def _project_root() -> str:
    # context_resolver.py -> nodes -> pipeline -> omnicell_agent -> src -> <project root>
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


def _sandbox_path_to_host(sandbox_path: str) -> Optional[str]:
    """沙盒内 /app/data/xxx 映射到宿主机 <project>/data/xxx，以便本地轻量探测元数据"""
    if not sandbox_path:
        return None
    if os.path.exists(sandbox_path):
        return sandbox_path
    filename = os.path.basename(sandbox_path)
    host_guess = os.path.join(_project_root(), "data", filename)
    if os.path.exists(host_guess):
        return host_guess
    return None


def _probe_h5ad_metadata(path: Optional[str]) -> Dict[str, Any]:
    """
    轻量读取 h5ad 元数据：不加载矩阵，只扫描 uns/obs 列名与候选字段的取值。
    若 anndata 不可用或文件不存在，安全地返回空结构。
    """
    meta: Dict[str, Any] = {
        "filename": None,
        "uns_keys": [],
        "obs_columns": [],
        "obs_tissue_values": [],
        "obs_organism_values": [],
    }
    if not path:
        return meta
    meta["filename"] = os.path.basename(path)
    try:
        import anndata as ad  # type: ignore

        adata = ad.read_h5ad(path, backed="r")
        try:
            if hasattr(adata, "uns") and adata.uns is not None:
                meta["uns_keys"] = list(adata.uns.keys())
            if hasattr(adata, "obs") and adata.obs is not None:
                meta["obs_columns"] = list(adata.obs.columns)
                for col in ("tissue", "organ", "sample_type"):
                    if col in meta["obs_columns"]:
                        try:
                            vals = list(
                                {
                                    str(v)
                                    for v in adata.obs[col].astype(str).head(200).tolist()
                                }
                            )
                            meta["obs_tissue_values"].extend(vals[:5])
                        except Exception:
                            pass
                for col in ("organism", "species"):
                    if col in meta["obs_columns"]:
                        try:
                            vals = list(
                                {
                                    str(v)
                                    for v in adata.obs[col].astype(str).head(200).tolist()
                                }
                            )
                            meta["obs_organism_values"].extend(vals[:5])
                        except Exception:
                            pass
        finally:
            try:
                adata.file.close()
            except Exception:
                pass
    except Exception as e:
        logger.info(f"ContextResolver: 未能读取 h5ad 元数据（可忽略）: {e}")
    return meta


def _heuristic_from_filename(filename: Optional[str]) -> Dict[str, str]:
    """文件名粗启发式：只作为最后兜底的低置信线索"""
    if not filename:
        return {}
    lower = filename.lower()
    hints: Dict[str, str] = {}
    if "pbmc" in lower:
        hints["tissue_hint"] = "PBMC"
    elif "spatial" in lower or "visium" in lower or "xenium" in lower:
        hints["tissue_hint"] = "Spatial"
    if "human" in lower or "_hg_" in lower or "hg38" in lower or "grch38" in lower:
        hints["species_hint"] = "Human"
    elif "mouse" in lower or "_mm_" in lower or "mm10" in lower or "grcm" in lower:
        hints["species_hint"] = "Mouse"
    return hints


def _structured_extract(user_prompt: str, h5ad_meta: Dict[str, Any], filename_hints: Dict[str, str]) -> ContextProfile:
    """调用 LLM 进行结构化抽取；失败则回落到基于启发式的保守默认。"""
    system_prompt = (
        "You are a biomedical metadata extractor. Given a user's natural-language instruction "
        "and lightweight metadata probed from an .h5ad file, output STRICT JSON matching the "
        "requested schema.\n\n"
        "Decision rules:\n"
        "1. User instruction is the primary source of truth; file metadata only supplements unknowns.\n"
        "2. Use canonical English tokens only. Prefer: Human / Mouse for species; "
        "PBMC / Peripheral Blood / Blood / Bone Marrow / Breast Cancer / Lung Cancer / "
        "Liver / Gastric / Brain / Spatial / Unknown for tissue.\n"
        "3. Never invent disease states without support in either source; use Unknown.\n"
        "4. goal_type should be a short snake_case tag reflecting user's stated aim."
    )
    human_prompt = (
        f"User instruction:\n{user_prompt}\n\n"
        f"File name: {h5ad_meta.get('filename')}\n"
        f"h5ad obs columns: {h5ad_meta.get('obs_columns')}\n"
        f"h5ad obs tissue-like values: {h5ad_meta.get('obs_tissue_values')}\n"
        f"h5ad obs organism-like values: {h5ad_meta.get('obs_organism_values')}\n"
        f"Filename heuristics: {filename_hints}\n"
    )

    try:
        llm = LLMSelector.get_llm(model_name="onerouter:default", temperature=0.0)
        structured_llm = llm.with_structured_output(ContextProfile)
        profile: ContextProfile = structured_llm.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=human_prompt),
            ]
        )
        trace_logger.append_llm_interaction(
            system_prompt=system_prompt,
            human_prompt=human_prompt,
            llm_response=json.dumps(profile.model_dump(), ensure_ascii=False),
            role_name="ContextResolver_LLM_Structured",
        )
        return profile
    except Exception as e:
        logger.warning(f"ContextResolver: 结构化抽取失败，回落启发式兜底: {e}")
        return ContextProfile(
            species=filename_hints.get("species_hint", "Human"),
            tissue=filename_hints.get("tissue_hint", "Unknown"),
        )


def run_context_resolver(state: DataPipeline_State) -> Dict[str, Any]:
    """
    Sub-Graph A 首节点：在 Planner 之前从 prompt + h5ad 元数据推断
    species / tissue / disease_state / goal_type，写入 task_context.resolved_context。
    后续 bridge_state_node 会将其提升为母图顶层的 species/tissue 字段，供 Graph B 使用。
    """
    logger.info("--- NODE: CONTEXT_RESOLVER ---")
    trace_logger.append_node_start("CONTEXT_RESOLVER")

    messages = state.get("messages", []) or []
    user_prompt = ""
    if messages:
        last = messages[-1]
        user_prompt = getattr(last, "content", "") or ""

    raw_data_path = state.get("raw_data_path", "") or ""
    host_path = _sandbox_path_to_host(raw_data_path)
    h5ad_meta = _probe_h5ad_metadata(host_path)
    filename_hints = _heuristic_from_filename(h5ad_meta.get("filename"))

    profile = _structured_extract(user_prompt, h5ad_meta, filename_hints)

    tissue_norm = _normalize_tissue(profile.tissue or "Unknown")
    if tissue_norm == "Unknown":
        if filename_hints.get("tissue_hint"):
            tissue_norm = filename_hints["tissue_hint"]
        elif h5ad_meta.get("obs_tissue_values"):
            tissue_norm = _normalize_tissue(str(h5ad_meta["obs_tissue_values"][0]))

    species_final = (profile.species or "Human").strip() or "Human"
    if species_final.lower() == "unknown":
        species_final = filename_hints.get("species_hint", "Human")
    if h5ad_meta.get("obs_organism_values") and species_final.lower() in ("", "unknown", "human") :
        first_org = str(h5ad_meta["obs_organism_values"][0]).lower()
        if "mus" in first_org or "mouse" in first_org:
            species_final = "Mouse"
        elif "homo" in first_org or "human" in first_org:
            species_final = "Human"

    resolved = {
        "species": species_final,
        "tissue": tissue_norm,
        "disease_state": profile.disease_state or "Unknown",
        "goal_type": profile.goal_type or "general_annotation",
        "sources": {
            "user_prompt_excerpt": (user_prompt[:200] + "...") if len(user_prompt) > 200 else user_prompt,
            "h5ad_metadata": h5ad_meta,
            "filename_hints": filename_hints,
            "raw_profile": profile.model_dump(),
        },
    }

    logger.info(
        "ContextResolver 推断结果: species=%s | tissue=%s | goal=%s",
        resolved["species"],
        resolved["tissue"],
        resolved["goal_type"],
    )

    task_context = dict(state.get("task_context", {}) or {})
    task_context["resolved_context"] = resolved
    return {"task_context": task_context}
