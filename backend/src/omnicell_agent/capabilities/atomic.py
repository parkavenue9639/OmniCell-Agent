"""Artifact-bounded atomic adapters over verified Graph A analysis recipes."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ContextManager, Literal, cast
from uuid import uuid4

from omnicell_agent.pipeline.nodes.executor import graph_a_python_session_scope
from omnicell_agent.schema.contract import MarkerTableContract

from .contracts import (
    ArtifactRef,
    AtomicAnalysisRequest,
    AtomicAnalysisResult,
    CapabilityKind,
    CapabilityRequest,
    CapabilitySpec,
    CapabilityStatus,
)
from .errors import CapabilityExecutionError, CapabilityInputError
from .registry import CapabilityContext


PythonSessionScopeFactory = Callable[[Path], ContextManager[Any]]
AtomicMode = Literal["transform", "extract", "visualize"]


@dataclass(frozen=True, slots=True)
class AtomicRecipe:
    name: str
    description: str
    prompt_hint: str
    script_directory: str
    mode: AtomicMode
    require_pca_and_leiden: bool = False

    @property
    def script_path(self) -> Path:
        return (
            Path(__file__).resolve().parents[1]
            / "skills"
            / self.script_directory
            / "scripts"
            / "execute.py"
        )


_RECIPES = (
    AtomicRecipe(
        name="run_qc_and_filter",
        description="对单细胞 dataset 执行既有质量控制与基础过滤，生成新的 dataset artifact。",
        prompt_hint=(
            "仅在用户明确要求质量控制或过滤低质量细胞/低表达基因时调用；"
            "输入必须是 dataset ArtifactRef，结果中的 output_dataset 是后续步骤的新输入。"
        ),
        script_directory="qc_and_filter",
        mode="transform",
    ),
    AtomicRecipe(
        name="run_normalize_log",
        description="对单细胞 dataset 执行既有总量归一化与 log1p 变换，生成新的 dataset artifact。",
        prompt_hint=(
            "仅在用户明确要求归一化或 log1p 变换时调用；实现会检测已经归一化的数据，"
            "结果中的 output_dataset 是后续步骤的新输入。"
        ),
        script_directory="normalize_log",
        mode="transform",
    ),
    AtomicRecipe(
        name="run_pca_clustering",
        description="对已归一化的单细胞 dataset 执行既有 PCA、邻接图与 Leiden 聚类并生成图像。",
        prompt_hint=(
            "用户明确要求 PCA 或聚类时调用；输入应已完成归一化，"
            "结果中的 output_dataset 可继续用于 marker 提取或绘图。"
        ),
        script_directory="pca_clustering",
        mode="transform",
    ),
    AtomicRecipe(
        name="extract_marker_genes",
        description="从单细胞 dataset 提取并校验非空 marker gene 表。",
        prompt_hint=(
            "用户明确要求 marker gene 时调用；优先传入已具备 Leiden 聚类的数据集。"
            "返回的 marker_table 可以直接交给 Graph B 深度注释。"
        ),
        script_directory="marker_genes_extractor",
        mode="extract",
    ),
    AtomicRecipe(
        name="generate_pca_scatter",
        description="从已经具有 PCA 与 Leiden 结果的 dataset 生成 PCA 聚类散点图。",
        prompt_hint=(
            "仅在用户明确要求 PCA 可视化时调用；输入必须已有 X_pca 和 leiden，"
            "若缺失应先调用 run_pca_clustering，不能让绘图步骤隐式重复归一化。"
        ),
        script_directory="pca_scatter",
        mode="visualize",
        require_pca_and_leiden=True,
    ),
)


class AtomicAnalysisCapability:
    request_model = AtomicAnalysisRequest
    result_model = AtomicAnalysisResult

    def __init__(
        self,
        recipe: AtomicRecipe,
        *,
        scope_factory: PythonSessionScopeFactory | None = None,
    ) -> None:
        self._recipe = recipe
        self.spec = CapabilitySpec(
            name=recipe.name,
            kind=CapabilityKind.ATOMIC,
            description=recipe.description,
            prompt_hint=recipe.prompt_hint,
        )
        self._scope_factory = scope_factory or (
            lambda workspace: graph_a_python_session_scope(
                host_workspace=str(workspace)
            )
        )

    def invoke(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
    ) -> AtomicAnalysisResult:
        typed = cast(AtomicAnalysisRequest, request)
        raw_data_path = context.artifacts.sandbox_path(
            typed.dataset,
            expected_kind="dataset",
        )
        token = uuid4().hex
        relative_root = context.artifacts.scoped_output_path(
            f"artifacts/atomic/{self._recipe.name}/{token}"
        )
        sandbox_root = f"/app/data/{relative_root}"
        dataset_relative = f"{relative_root}/dataset.h5ad"
        marker_relative = f"{relative_root}/markers.json"
        summary_relative = f"{relative_root}/summary.json"
        before = context.artifacts.snapshot_files()
        code = _render_atomic_code(
            self._recipe,
            raw_data_path=raw_data_path,
            sandbox_root=sandbox_root,
        )

        with self._scope_factory(context.artifacts.workspace) as session:
            result = session.execute_code(code)
        if result.get("status") != "success":
            diagnostic = str(
                result.get("stderr")
                or result.get("error")
                or "atomic runtime returned a non-success status"
            ).strip()
            error_type = (
                CapabilityInputError
                if "ATOMIC_INPUT_ERROR:" in diagnostic
                else CapabilityExecutionError
            )
            raise error_type(
                f"{self._recipe.name} 执行失败：{diagnostic[-1_500:]}"
            )

        produced = context.artifacts.publish_new_files(
            before,
            within_output_scope=context.artifacts.output_scope is not None,
        )
        by_uri = {ref.uri: ref for ref in produced}
        summary_uri = f"workspace://{summary_relative}"
        if summary_uri not in by_uri:
            raise CapabilityExecutionError(
                f"{self._recipe.name} 未产出受验证的 summary"
            )
        summary_ref = context.artifacts.publish(
            context.artifacts.workspace / summary_relative,
            kind="analysis_metadata",
            media_type="application/json",
            metadata=_provenance(self._recipe, typed.dataset),
        )
        with context.artifacts.open_verified(
            summary_ref,
            expected_kind="analysis_metadata",
        ) as stream:
            try:
                metrics = json.load(stream)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise CapabilityExecutionError(
                    f"{self._recipe.name} summary 无效"
                ) from exc
        if not isinstance(metrics, dict):
            raise CapabilityExecutionError(
                f"{self._recipe.name} summary 必须是 JSON object"
            )

        output_dataset: ArtifactRef | None = None
        dataset_uri = f"workspace://{dataset_relative}"
        if self._recipe.mode == "transform":
            if dataset_uri not in by_uri:
                raise CapabilityExecutionError(
                    f"{self._recipe.name} 未产出新的 dataset"
                )
            output_dataset = context.artifacts.publish(
                context.artifacts.workspace / dataset_relative,
                kind="dataset",
                media_type="application/x-hdf5",
                metadata=_provenance(self._recipe, typed.dataset),
            )

        marker_table: ArtifactRef | None = None
        marker_uri = f"workspace://{marker_relative}"
        if self._recipe.mode == "extract":
            if marker_uri not in by_uri:
                raise CapabilityExecutionError(
                    f"{self._recipe.name} 未产出 marker table"
                )
            marker_table = context.artifacts.publish(
                context.artifacts.workspace / marker_relative,
                kind="marker_table",
                media_type="application/json",
                metadata=_provenance(self._recipe, typed.dataset),
            )
            with context.artifacts.open_verified(
                marker_table,
                expected_kind="marker_table",
            ) as marker_stream:
                marker_contract = MarkerTableContract.load_from_stream(
                    marker_stream
                )
            if not marker_contract.markers:
                raise CapabilityExecutionError(
                    f"{self._recipe.name} 产出的 marker contract 为空"
                )

        explicit_artifacts: list[ArtifactRef] = []
        for ref in produced:
            if ref.uri in {summary_uri, dataset_uri, marker_uri}:
                continue
            if ref.kind == "image":
                explicit_artifacts.append(
                    context.artifacts.publish(
                        context.artifacts.workspace
                        / ref.uri.removeprefix("workspace://"),
                        kind="image",
                        media_type=ref.media_type,
                        metadata=_provenance(
                            self._recipe,
                            typed.dataset,
                        ),
                    )
                )
        if self._recipe.mode == "visualize" and not explicit_artifacts:
            raise CapabilityExecutionError(
                f"{self._recipe.name} 未产出图像"
            )

        artifacts = [*explicit_artifacts, summary_ref]
        if output_dataset is not None:
            artifacts.insert(0, output_dataset)
        if marker_table is not None:
            artifacts.insert(0, marker_table)
        return AtomicAnalysisResult(
            status=CapabilityStatus.COMPLETED,
            operation=self._recipe.name,
            source_dataset=typed.dataset,
            output_dataset=output_dataset,
            artifacts=artifacts,
            marker_table=marker_table,
            metrics=metrics,
        )


def build_atomic_capabilities(
    *,
    scope_factory: PythonSessionScopeFactory | None = None,
) -> tuple[AtomicAnalysisCapability, ...]:
    return tuple(
        AtomicAnalysisCapability(
            recipe,
            scope_factory=scope_factory,
        )
        for recipe in _RECIPES
    )


def _provenance(
    recipe: AtomicRecipe,
    source: ArtifactRef,
) -> dict[str, Any]:
    return {
        "operation": recipe.name,
        "operation_version": "1.0",
        "source_artifact_id": str(source.artifact_id),
    }


def _render_atomic_code(
    recipe: AtomicRecipe,
    *,
    raw_data_path: str,
    sandbox_root: str,
) -> str:
    try:
        script = recipe.script_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CapabilityExecutionError(
            f"atomic recipe 不可读取：{recipe.name}"
        ) from exc

    dataset_path = f"{sandbox_root}/dataset.h5ad"
    marker_path = f"{sandbox_root}/markers.json"
    summary_path = f"{sandbox_root}/summary.json"
    return f"""
import json as _atomic_json
from pathlib import Path as _AtomicPath
import anndata as _atomic_anndata
import scanpy as sc

if (
    hasattr(_atomic_anndata, "settings")
    and hasattr(
        _atomic_anndata.settings,
        "allow_write_nullable_strings",
    )
):
    _atomic_anndata.settings.allow_write_nullable_strings = True

raw_data_path = {raw_data_path!r}
artifact_output_root = {sandbox_root!r}
marker_table_path = {marker_path!r}
_atomic_output_root = _AtomicPath(artifact_output_root)
_atomic_output_root.mkdir(parents=True, exist_ok=True)
adata = sc.read_h5ad(raw_data_path)
_atomic_before = {{
    "n_obs": int(adata.n_obs),
    "n_vars": int(adata.n_vars),
    "has_pca": bool("X_pca" in adata.obsm),
    "has_leiden": bool("leiden" in adata.obs),
    "has_log1p": bool("log1p" in adata.uns),
}}
if {recipe.require_pca_and_leiden!r} and (
    "X_pca" not in adata.obsm or "leiden" not in adata.obs
):
    raise ValueError(
        "ATOMIC_INPUT_ERROR: generate_pca_scatter requires X_pca and leiden"
    )
try:
    exec(
        compile(
            {script!r},
            {"<omnicell-skill:" + recipe.name + ">"!r},
            "exec",
        ),
        globals(),
        globals(),
    )
except SystemExit as _atomic_exit:
    if _atomic_exit.code not in (None, 0):
        raise

_atomic_images = sorted(
    str(path.name)
    for path in _atomic_output_root.iterdir()
    if path.is_file() and path.suffix.lower() in {{".png", ".jpg", ".jpeg", ".svg"}}
)
_atomic_metrics = {{
    "n_obs_before": _atomic_before["n_obs"],
    "n_obs_after": int(adata.n_obs),
    "n_vars_before": _atomic_before["n_vars"],
    "n_vars_after": int(adata.n_vars),
    "pca_reused": bool(_atomic_before["has_pca"]),
    "clustering_reused": bool(_atomic_before["has_leiden"]),
    "normalization_applied": bool(
        not _atomic_before["has_log1p"] and "log1p" in adata.uns
    ),
    "has_pca": bool("X_pca" in adata.obsm),
    "has_leiden": bool("leiden" in adata.obs),
    "cluster_count": int(
        adata.obs["leiden"].nunique() if "leiden" in adata.obs else 0
    ),
    "images": _atomic_images,
}}
if {recipe.mode!r} == "transform":
    if {recipe.name!r} == "run_pca_clustering" and (
        "X_pca" not in adata.obsm or "leiden" not in adata.obs
    ):
        raise RuntimeError("PCA/clustering postcondition failed")
    adata.write_h5ad({dataset_path!r})
elif {recipe.mode!r} == "extract":
    _atomic_marker_path = _AtomicPath(marker_table_path)
    if not _atomic_marker_path.is_file():
        raise RuntimeError("marker output missing")
    with _atomic_marker_path.open("r", encoding="utf-8") as _atomic_marker_stream:
        _atomic_marker_rows = _atomic_json.load(_atomic_marker_stream)
    if not isinstance(_atomic_marker_rows, list) or not _atomic_marker_rows:
        raise RuntimeError("marker output is empty")
    _atomic_metrics["marker_count"] = len(_atomic_marker_rows)
elif {recipe.mode!r} == "visualize" and not _atomic_images:
    raise RuntimeError("visualization output missing")

with _AtomicPath({summary_path!r}).open("w", encoding="utf-8") as _atomic_summary:
    _atomic_json.dump(
        _atomic_metrics,
        _atomic_summary,
        ensure_ascii=False,
        separators=(",", ":"),
    )
del adata
""".strip()


__all__ = [
    "AtomicAnalysisCapability",
    "AtomicRecipe",
    "build_atomic_capabilities",
]
