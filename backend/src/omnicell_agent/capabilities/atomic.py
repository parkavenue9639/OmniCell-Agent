"""Artifact-bounded scientific Tool adapters over verified deterministic recipes."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ContextManager, Literal, cast
from uuid import uuid4

from omnicell_agent.pipeline.nodes.executor import analysis_python_session_scope
from omnicell_agent.recipes.catalog import (
    RecipeCatalog,
    RecipeCatalogError,
    RecipeDefinition,
    load_builtin_recipe_catalog,
)
from omnicell_agent.schema.contract import MarkerTableContract

from .contracts import (
    ArtifactRef,
    AtomicAnalysisResult,
    CapabilityEffect,
    CapabilityMode,
    CapabilityRequest,
    CapabilitySpec,
    CapabilityStatus,
    ClusterCellsRequest,
    DatasetCapabilityRequest,
    FindMarkerGenesRequest,
    NormalizeExpressionRequest,
    PlotPcaClustersRequest,
    QualityControlRequest,
)
from .errors import CapabilityExecutionError, CapabilityInputError
from .registry import CapabilityContext


PythonSessionScopeFactory = Callable[[Path], ContextManager[Any]]
AtomicMode = Literal["transform", "extract", "visualize"]


@dataclass(frozen=True, slots=True)
class AtomicToolBinding:
    tool_name: str
    description: str
    prompt_hint: str
    request_model: type[DatasetCapabilityRequest]
    effect: CapabilityEffect
    produces: tuple[str, ...]
    preconditions: tuple[str, ...]
    recipe_id: str
    mode: AtomicMode
    dataset_state_updates: tuple[str, ...] = ()
    required_features: tuple[Literal["pca", "leiden"], ...] = ()

    def __post_init__(self) -> None:
        if self.tool_name == self.recipe_id:
            raise ValueError("Tool 名称与内部 Recipe ID 必须保持语义分离")


_ATOMIC_TOOL_BINDINGS = (
    AtomicToolBinding(
        tool_name="quality_control",
        description="过滤低质量细胞和低频基因，并生成新的单细胞数据集。",
        prompt_hint=(
            "仅在用户明确要求质量控制或过滤低质量细胞/低表达基因时调用；"
            "不要为方法问答、数据检查或其他分析目标擅自过滤；"
            "输入必须是 dataset ArtifactRef，结果中的 output_dataset 是后续步骤的新输入。"
        ),
        request_model=QualityControlRequest,
        effect=CapabilityEffect.TRANSFORM,
        produces=("dataset", "analysis_metadata"),
        preconditions=("输入是可读取的单细胞 dataset",),
        recipe_id="qc_and_filter",
        mode="transform",
        dataset_state_updates=("quality_controlled",),
    ),
    AtomicToolBinding(
        tool_name="normalize_expression",
        description="执行总量归一化与 log1p 变换，并生成新的单细胞数据集。",
        prompt_hint=(
            "仅在用户明确要求归一化或 log1p 变换时调用；不要为概念问答调用，"
            "也不要把归一化描述为批次校正；实现会拒绝重复归一化，"
            "结果中的 output_dataset 是后续步骤的新输入。"
        ),
        request_model=NormalizeExpressionRequest,
        effect=CapabilityEffect.TRANSFORM,
        produces=("dataset", "analysis_metadata"),
        preconditions=("输入是可读取的单细胞 dataset",),
        recipe_id="normalize_log",
        mode="transform",
        dataset_state_updates=("normalized_log1p",),
    ),
    AtomicToolBinding(
        tool_name="cluster_cells",
        description="对已归一化的表达矩阵执行高变基因选择、PCA、邻接图和 Leiden 聚类。",
        prompt_hint=(
            "仅在用户明确要求计算 PCA、邻接图或聚类时调用；输入必须已完成归一化，"
            "缺少前置条件时不要在本 Tool 内隐式补做；结果中的 output_dataset "
            "可继续用于 marker 提取或绘图，但聚类标签不等同于细胞类型。"
        ),
        request_model=ClusterCellsRequest,
        effect=CapabilityEffect.ANALYZE,
        produces=("dataset", "image", "analysis_metadata"),
        preconditions=("表达矩阵已经完成归一化和 log1p 变换",),
        recipe_id="pca_clustering",
        mode="transform",
        dataset_state_updates=("pca", "neighbors", "leiden_clusters"),
    ),
    AtomicToolBinding(
        tool_name="find_marker_genes",
        description="从单细胞 dataset 提取并校验非空 marker gene 表。",
        prompt_hint=(
            "仅在用户明确要求从已聚类 dataset 重新计算 marker gene 时调用；"
            "只问定义或查看已有 marker table 时不要调用。输入必须具备 Leiden 聚类，"
            "返回的 marker_table 是给定 cluster 标签下的差异表达证据，可继续用于注释，"
            "但不证明因果驱动、cluster 稳健或唯一细胞身份。"
        ),
        request_model=FindMarkerGenesRequest,
        effect=CapabilityEffect.ANALYZE,
        produces=("marker_table", "analysis_metadata"),
        preconditions=("dataset 已具有可用的 Leiden cluster 标签",),
        recipe_id="marker_genes_extractor",
        mode="extract",
        required_features=("leiden",),
    ),
    AtomicToolBinding(
        tool_name="plot_pca_clusters",
        description="从已经具有 PCA 与 Leiden 结果的 dataset 生成 PCA 聚类散点图。",
        prompt_hint=(
            "仅在用户明确要求 PCA 可视化时调用；输入必须已有 X_pca 和 leiden，"
            "若缺失则结构化说明前置条件，只有用户目标同时授权聚类时才另行调用 "
            "cluster_cells；不能让绘图步骤隐式重复预处理。图像不独立证明聚类质量。"
        ),
        request_model=PlotPcaClustersRequest,
        effect=CapabilityEffect.VISUALIZE,
        produces=("image", "analysis_metadata"),
        preconditions=("dataset 已具有 X_pca 和 Leiden cluster 标签",),
        recipe_id="pca_scatter",
        mode="visualize",
        required_features=("pca", "leiden"),
    ),
)


class AtomicAnalysisCapability:
    result_model = AtomicAnalysisResult

    def __init__(
        self,
        binding: AtomicToolBinding,
        recipe: RecipeDefinition,
        *,
        scope_factory: PythonSessionScopeFactory | None = None,
    ) -> None:
        if binding.recipe_id != recipe.recipe_id:
            raise ValueError("Atomic Tool binding 与 RecipeDefinition 不匹配")
        self._binding = binding
        self._recipe = recipe
        self.request_model = binding.request_model
        self.spec = CapabilitySpec(
            name=binding.tool_name,
            mode=CapabilityMode.ATOMIC,
            effect=binding.effect,
            description=binding.description,
            prompt_hint=binding.prompt_hint,
            consumes=("dataset",),
            produces=binding.produces,
            preconditions=binding.preconditions,
            recommended_skills=_recommended_skills(binding.tool_name),
        )
        self._scope_factory = scope_factory or (
            lambda workspace: analysis_python_session_scope(
                host_workspace=str(workspace)
            )
        )

    def invoke(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
    ) -> AtomicAnalysisResult:
        typed = cast(DatasetCapabilityRequest, request)
        parameters = typed.model_dump(
            mode="json",
            exclude={"dataset"},
        )
        try:
            parameters = self._recipe.normalize_parameters(parameters)
        except RecipeCatalogError as exc:
            raise CapabilityInputError(
                f"{self._binding.tool_name} 参数与内部 Recipe 不匹配"
            ) from exc
        raw_data_path = context.artifacts.sandbox_path(
            typed.dataset,
            expected_kind="dataset",
        )
        token = uuid4().hex
        relative_root = context.artifacts.scoped_output_path(
            f"artifacts/atomic/{self._binding.tool_name}/{token}"
        )
        sandbox_root = f"/app/data/{relative_root}"
        dataset_relative = f"{relative_root}/dataset.h5ad"
        marker_relative = f"{relative_root}/markers.json"
        summary_relative = f"{relative_root}/summary.json"
        before = context.artifacts.snapshot_files()
        code = _render_atomic_code(
            self._binding,
            self._recipe,
            raw_data_path=raw_data_path,
            sandbox_root=sandbox_root,
            parameters=parameters,
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
                f"{self._binding.tool_name} 执行失败：{diagnostic[-1_500:]}"
            )

        produced = context.artifacts.publish_new_files(
            before,
            within_output_scope=context.artifacts.output_scope is not None,
        )
        by_uri = {ref.uri: ref for ref in produced}
        summary_uri = f"workspace://{summary_relative}"
        if summary_uri not in by_uri:
            raise CapabilityExecutionError(
                f"{self._binding.tool_name} 未产出受验证的 summary"
            )
        summary_ref = context.artifacts.publish(
            context.artifacts.workspace / summary_relative,
            kind="analysis_metadata",
            media_type="application/json",
            metadata=_provenance(
                self._binding,
                typed.dataset,
                parameters=parameters,
            ),
        )
        with context.artifacts.open_verified(
            summary_ref,
            expected_kind="analysis_metadata",
        ) as stream:
            try:
                metrics = json.load(stream)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise CapabilityExecutionError(
                    f"{self._binding.tool_name} summary 无效"
                ) from exc
        if not isinstance(metrics, dict):
            raise CapabilityExecutionError(
                f"{self._binding.tool_name} summary 必须是 JSON object"
            )

        output_dataset: ArtifactRef | None = None
        dataset_uri = f"workspace://{dataset_relative}"
        if self._binding.mode == "transform":
            if dataset_uri not in by_uri:
                raise CapabilityExecutionError(
                    f"{self._binding.tool_name} 未产出新的 dataset"
                )
            output_dataset = context.artifacts.publish(
                context.artifacts.workspace / dataset_relative,
                kind="dataset",
                media_type="application/x-hdf5",
                metadata=_provenance(
                    self._binding,
                    typed.dataset,
                    parameters=parameters,
                ),
            )

        marker_table: ArtifactRef | None = None
        marker_uri = f"workspace://{marker_relative}"
        if self._binding.mode == "extract":
            if marker_uri not in by_uri:
                raise CapabilityExecutionError(
                    f"{self._binding.tool_name} 未产出 marker table"
                )
            marker_table = context.artifacts.publish(
                context.artifacts.workspace / marker_relative,
                kind="marker_table",
                media_type="application/json",
                metadata=_provenance(
                    self._binding,
                    typed.dataset,
                    parameters=parameters,
                ),
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
                    f"{self._binding.tool_name} 产出的 marker contract 为空"
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
                            self._binding,
                            typed.dataset,
                            parameters=parameters,
                        ),
                    )
                )
        if self._binding.mode == "visualize" and not explicit_artifacts:
            raise CapabilityExecutionError(
                f"{self._binding.tool_name} 未产出图像"
            )

        artifacts = [*explicit_artifacts, summary_ref]
        if output_dataset is not None:
            artifacts.insert(0, output_dataset)
        if marker_table is not None:
            artifacts.insert(0, marker_table)
        return AtomicAnalysisResult(
            status=CapabilityStatus.COMPLETED,
            operation=self._binding.tool_name,
            source_dataset=typed.dataset,
            output_dataset=output_dataset,
            artifacts=artifacts,
            marker_table=marker_table,
            metrics=metrics,
        )


def build_atomic_capabilities(
    *,
    scope_factory: PythonSessionScopeFactory | None = None,
    recipe_catalog: RecipeCatalog | None = None,
) -> tuple[AtomicAnalysisCapability, ...]:
    catalog = recipe_catalog or load_builtin_recipe_catalog()
    return tuple(
        AtomicAnalysisCapability(
            binding,
            catalog.get(binding.recipe_id),
            scope_factory=scope_factory,
        )
        for binding in _ATOMIC_TOOL_BINDINGS
    )


def _provenance(
    binding: AtomicToolBinding,
    source: ArtifactRef,
    *,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    previous_state = source.metadata.get("dataset_state")
    state = {
        str(item)
        for item in (
            previous_state
            if isinstance(previous_state, list)
            else []
        )
        if isinstance(item, str)
    }
    state.update(binding.dataset_state_updates)
    return {
        "operation": binding.tool_name,
        "operation_version": "1.0",
        "source_artifact_id": str(source.artifact_id),
        "parameters": parameters or {},
        "dataset_state": sorted(state),
    }


def _render_atomic_code(
    binding: AtomicToolBinding,
    recipe: RecipeDefinition,
    *,
    raw_data_path: str,
    sandbox_root: str,
    parameters: dict[str, Any],
) -> str:
    try:
        script = recipe.script_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CapabilityExecutionError(
            f"atomic recipe 不可读取：{binding.tool_name}"
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
tool_parameters = {parameters!r}
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
_atomic_required_features = {binding.required_features!r}
if "pca" in _atomic_required_features and "X_pca" not in adata.obsm:
    raise ValueError("ATOMIC_INPUT_ERROR: input requires X_pca")
if "leiden" in _atomic_required_features and "leiden" not in adata.obs:
    raise ValueError("ATOMIC_INPUT_ERROR: input requires leiden")
try:
    exec(
        compile(
            {script!r},
            {"<omnicell-internal-recipe:" + recipe.recipe_id + ">"!r},
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
if {binding.mode!r} == "transform":
    if {binding.tool_name!r} == "cluster_cells" and (
        "X_pca" not in adata.obsm or "leiden" not in adata.obs
    ):
        raise RuntimeError("PCA/clustering postcondition failed")
    adata.write_h5ad({dataset_path!r})
elif {binding.mode!r} == "extract":
    _atomic_marker_path = _AtomicPath(marker_table_path)
    if not _atomic_marker_path.is_file():
        raise RuntimeError("marker output missing")
    with _atomic_marker_path.open("r", encoding="utf-8") as _atomic_marker_stream:
        _atomic_marker_rows = _atomic_json.load(_atomic_marker_stream)
    if not isinstance(_atomic_marker_rows, list) or not _atomic_marker_rows:
        raise RuntimeError("marker output is empty")
    _atomic_metrics["marker_count"] = len(_atomic_marker_rows)
elif {binding.mode!r} == "visualize" and not _atomic_images:
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
    "AtomicToolBinding",
    "build_atomic_capabilities",
]


def _recommended_skills(tool_name: str) -> tuple[str, ...]:
    return {
        "quality_control": ("single-cell-preprocessing",),
        "normalize_expression": ("single-cell-preprocessing",),
        "cluster_cells": (
            "single-cell-preprocessing",
            "cluster-and-marker-analysis",
        ),
        "find_marker_genes": (
            "cluster-and-marker-analysis",
            "cell-type-annotation",
        ),
        "plot_pca_clusters": ("scientific-visualization",),
    }.get(tool_name, ())
