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
    AtomicScientificEvidence,
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
from .marker_validation import validate_marker_selection
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
    required_features: tuple[Literal["pca", "leiden"], ...] = ()
    requires_log_expression: bool = False
    random_seed: int | None = None

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
    ),
    AtomicToolBinding(
        tool_name="normalize_expression",
        description="执行总量归一化与 log1p 变换，并生成新的单细胞数据集。",
        prompt_hint=(
            "仅在用户明确要求归一化或 log1p 变换时调用；不要为概念问答调用，"
            "也不要把归一化描述为批次校正；实现会识别并明确报告已存在的 log1p 空间，"
            "不会静默重复变换，"
            "结果中的 output_dataset 是后续步骤的新输入。"
        ),
        request_model=NormalizeExpressionRequest,
        effect=CapabilityEffect.TRANSFORM,
        produces=("dataset", "analysis_metadata"),
        preconditions=("输入是可读取的单细胞 dataset",),
        recipe_id="normalize_log",
        mode="transform",
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
        requires_log_expression=True,
        random_seed=0,
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
        requires_log_expression=True,
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
            source_artifact_id=str(typed.dataset.artifact_id),
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
        provisional_summary_ref = by_uri[summary_uri]
        with context.artifacts.open_verified(
            provisional_summary_ref,
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
        try:
            scientific_evidence = AtomicScientificEvidence.model_validate(
                metrics.get("scientific_evidence")
            )
        except Exception as exc:
            raise CapabilityExecutionError(
                f"{self._binding.tool_name} scientific evidence 无效"
            ) from exc
        if (
            scientific_evidence.operation != self._binding.tool_name
            or scientific_evidence.source_artifact_id != typed.dataset.artifact_id
            or scientific_evidence.parameters != parameters
            or scientific_evidence.random_seed != self._binding.random_seed
        ):
            raise CapabilityExecutionError(
                f"{self._binding.tool_name} scientific evidence 与当前调用不一致"
            )
        summary_ref = context.artifacts.publish(
            context.artifacts.workspace / summary_relative,
            kind="analysis_metadata",
            media_type="application/json",
            metadata=_provenance(
                self._binding,
                typed.dataset,
                parameters=parameters,
                evidence=scientific_evidence,
            ),
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
                    evidence=scientific_evidence,
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
                    evidence=scientific_evidence,
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
            _validate_marker_evidence(
                marker_contract,
                scientific_evidence,
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
                            evidence=scientific_evidence,
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
            scientific_evidence=scientific_evidence,
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
    evidence: AtomicScientificEvidence,
) -> dict[str, Any]:
    output_state = evidence.output_state
    state: set[str] = set()
    if output_state.expression_space in {
        "normalized_log1p",
        "log1p_detected",
    }:
        state.add("normalized_log1p")
    if output_state.quality_control_signature is not None:
        state.add("quality_controlled")
    if output_state.has_pca:
        state.add("pca")
    if output_state.has_neighbors:
        state.add("neighbors")
    if output_state.has_leiden:
        state.add("leiden_clusters")
    return {
        "operation": binding.tool_name,
        "operation_version": "1.0",
        "source_artifact_id": str(source.artifact_id),
        "parameters": parameters or {},
        "dataset_state": sorted(state),
        "scientific_state": output_state.model_dump(mode="json"),
        "operation_disposition": evidence.disposition.value,
        "scientific_validation": {
            "status": evidence.validation_status,
            "checks": evidence.validation_checks,
        },
    }


def _validate_marker_evidence(
    marker_contract: MarkerTableContract,
    evidence: AtomicScientificEvidence,
) -> None:
    selection = evidence.marker_selection
    if selection is None:
        raise CapabilityExecutionError("marker scientific evidence 缺失")
    try:
        validate_marker_selection(marker_contract, selection)
    except ValueError as exc:
        raise CapabilityExecutionError(str(exc)) from exc


def _render_atomic_code(
    binding: AtomicToolBinding,
    recipe: RecipeDefinition,
    *,
    raw_data_path: str,
    sandbox_root: str,
    parameters: dict[str, Any],
    source_artifact_id: str,
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
import hashlib as _atomic_hashlib
import json as _atomic_json
from pathlib import Path as _AtomicPath
import anndata as _atomic_anndata
import numpy as _atomic_np
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
_atomic_source_artifact_id = {source_artifact_id!r}
_atomic_random_seed = {binding.random_seed!r}
_atomic_output_root = _AtomicPath(artifact_output_root)
_atomic_output_root.mkdir(parents=True, exist_ok=True)


def _atomic_sample_values(_adata, _max_values=200000):
    _matrix = _adata.X
    _values = (
        _atomic_np.asarray(_matrix.data)
        if hasattr(_matrix, "data") and not isinstance(_matrix, _atomic_np.ndarray)
        else _atomic_np.asarray(_matrix).ravel()
    )
    _values = _values[_atomic_np.isfinite(_values)]
    if _values.size > _max_values:
        _values = _values[:_max_values]
    return _values


def _atomic_detect_expression_space(_adata):
    _values = _atomic_sample_values(_adata)
    _positive = _values[_values > 0]
    _matrix_is_log_like = False
    if _positive.size:
        _maximum = float(_atomic_np.max(_positive))
        _non_integer_fraction = float(
            _atomic_np.mean(
                _atomic_np.abs(_positive - _atomic_np.round(_positive)) > 1e-3
            )
        )
        _matrix_is_log_like = (
            _maximum <= 30.0
            and _non_integer_fraction >= 0.1
        )
    _recorded = _adata.uns.get("omnicell_scientific_state")
    if isinstance(_recorded, dict):
        _space = str(_recorded.get("expression_space") or "")
        if (
            _space == "normalized_log1p"
            and _atomic_signature(_recorded, "normalization_signature")
            and _matrix_is_log_like
        ):
            return "normalized_log1p", "omnicell_lineage_and_matrix"
        if _space == "log1p_detected" and _matrix_is_log_like:
            return "log1p_detected", "omnicell_hint_and_matrix"
    if "log1p" in _adata.uns and _matrix_is_log_like:
        return "log1p_detected", "anndata_metadata_and_matrix"
    if _matrix_is_log_like:
        return "log1p_detected", "bounded_value_detection"
    return "unknown", "none"


def _atomic_cluster_ids(_adata):
    if "leiden" not in _adata.obs:
        return []
    _values = {{
        str(value)
        for value in _adata.obs["leiden"].dropna().tolist()
        if str(value).strip()
    }}
    return sorted(
        _values,
        key=lambda value: (
            0,
            int(value),
        ) if value.lstrip("-").isdigit() else (1, value),
    )


def _atomic_signature(_state, _key):
    _value = _state.get(_key)
    if (
        isinstance(_value, str)
        and len(_value) == 64
        and all(char in "0123456789abcdef" for char in _value)
    ):
        return _value
    return None


def _atomic_dataset_state(_adata):
    _recorded = _adata.uns.get("omnicell_scientific_state")
    _recorded = _recorded if isinstance(_recorded, dict) else {{}}
    _space, _space_basis = _atomic_detect_expression_space(_adata)
    _clusters = _atomic_cluster_ids(_adata)
    _has_pca = "X_pca" in _adata.obsm
    _has_neighbors = (
        "neighbors" in _adata.uns
        and "connectivities" in _adata.obsp
        and "distances" in _adata.obsp
    )
    return {{
        "n_obs": int(_adata.n_obs),
        "n_vars": int(_adata.n_vars),
        "expression_space": _space,
        "expression_space_basis": _space_basis,
        "has_pca": bool(_has_pca),
        "has_neighbors": bool(_has_neighbors),
        "has_leiden": bool(_clusters),
        "cluster_ids": _clusters,
        "quality_control_signature": _atomic_signature(
            _recorded,
            "quality_control_signature",
        ),
        "normalization_signature": _atomic_signature(
            _recorded,
            "normalization_signature",
        ),
        "pca_signature": (
            _atomic_signature(_recorded, "pca_signature")
            if _has_pca
            else None
        ),
        "clustering_signature": (
            _atomic_signature(_recorded, "clustering_signature")
            if _clusters
            else None
        ),
    }}


adata = sc.read_h5ad(raw_data_path)
_atomic_before = _atomic_dataset_state(adata)
_atomic_parameter_signature = _atomic_hashlib.sha256(
    _atomic_json.dumps(
        tool_parameters,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
_atomic_operation_disposition = "executed"
_atomic_validation_checks = []
_atomic_required_features = {binding.required_features!r}
if "pca" in _atomic_required_features and "X_pca" not in adata.obsm:
    raise ValueError("ATOMIC_INPUT_ERROR: input requires X_pca")
if "leiden" in _atomic_required_features and "leiden" not in adata.obs:
    raise ValueError("ATOMIC_INPUT_ERROR: input requires leiden")
if {binding.requires_log_expression!r} and (
    _atomic_before["expression_space"] == "unknown"
):
    raise ValueError(
        "ATOMIC_INPUT_ERROR: input expression space must be log-normalized"
    )
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

_atomic_after = _atomic_dataset_state(adata)
if _atomic_after["n_obs"] <= 0 or _atomic_after["n_vars"] <= 0:
    raise RuntimeError("scientific postcondition failed: dataset is empty")
_atomic_validation_checks.append("dataset_nonempty")

if {binding.tool_name!r} == "quality_control":
    if (
        _atomic_after["n_obs"] > _atomic_before["n_obs"]
        or _atomic_after["n_vars"] > _atomic_before["n_vars"]
    ):
        raise RuntimeError("quality-control postcondition expanded the dataset")
    if (
        _atomic_after["quality_control_signature"]
        != _atomic_parameter_signature
    ):
        raise RuntimeError("quality-control signature postcondition failed")
    _atomic_validation_checks.extend(
        ["cell_gene_counts_nonincreasing", "quality_control_signature_verified"]
    )
elif {binding.tool_name!r} == "normalize_expression":
    if _atomic_after["expression_space"] == "unknown":
        raise RuntimeError("normalization postcondition failed")
    _values = _atomic_sample_values(adata)
    if _values.size and (
        not bool(_atomic_np.all(_atomic_np.isfinite(_values)))
        or bool(_atomic_np.any(_values < 0))
    ):
        raise RuntimeError("normalized expression contains invalid values")
    _atomic_validation_checks.append("log_expression_values_verified")
    if _atomic_operation_disposition == "executed":
        if (
            _atomic_after["expression_space"] != "normalized_log1p"
            or _atomic_after["normalization_signature"]
            != _atomic_parameter_signature
        ):
            raise RuntimeError("normalization operation signature mismatch")
        _sample = adata.X[: min(int(adata.n_obs), 256)]
        _dense = (
            _sample.toarray()
            if hasattr(_sample, "toarray")
            else _atomic_np.asarray(_sample)
        )
        _row_sums = _atomic_np.expm1(_dense).sum(axis=1)
        _nonzero_sums = _row_sums[_row_sums > 0]
        if (
            not _nonzero_sums.size
            or not bool(
                _atomic_np.allclose(
                    _nonzero_sums,
                    float(tool_parameters["target_sum"]),
                    rtol=5e-3,
                    atol=1e-2,
                )
            )
        ):
            raise RuntimeError("normalization target_sum postcondition failed")
        _atomic_validation_checks.extend(
            ["normalization_signature_verified", "target_sum_verified"]
        )
    elif _atomic_operation_disposition == "reused":
        _atomic_validation_checks.append("existing_log_space_verified")
    else:
        raise RuntimeError("normalization disposition is invalid")
elif {binding.tool_name!r} == "cluster_cells":
    if not (
        _atomic_after["has_pca"]
        and _atomic_after["has_neighbors"]
        and _atomic_after["has_leiden"]
    ):
        raise RuntimeError("PCA/clustering postcondition failed")
    if (
        _atomic_after["pca_signature"] != _atomic_parameter_signature
        or _atomic_after["clustering_signature"] != _atomic_parameter_signature
    ):
        raise RuntimeError("PCA/clustering signature postcondition failed")
    _pca = _atomic_np.asarray(adata.obsm["X_pca"])
    if (
        _pca.ndim != 2
        or _pca.shape[0] != int(adata.n_obs)
        or _pca.shape[1] < 2
        or not bool(_atomic_np.all(_atomic_np.isfinite(_pca)))
    ):
        raise RuntimeError("PCA matrix postcondition failed")
    if _atomic_operation_disposition not in {{"executed", "reused"}}:
        raise RuntimeError("clustering disposition is invalid")
    _atomic_validation_checks.extend(
        [
            "pca_matrix_verified",
            "neighbor_graph_verified",
            "cluster_labels_verified",
            "clustering_signature_verified",
        ]
    )

_atomic_images = sorted(
    str(path.name)
    for path in _atomic_output_root.iterdir()
    if path.is_file()
    and path.suffix.lower() in {{".png", ".jpg", ".jpeg", ".svg"}}
)
_atomic_marker_selection = None
_atomic_marker_count = 0
if {binding.mode!r} == "transform":
    adata.write_h5ad({dataset_path!r})
    _atomic_validation_checks.append("dataset_artifact_written")
elif {binding.mode!r} == "extract":
    _atomic_marker_path = _AtomicPath(marker_table_path)
    if not _atomic_marker_path.is_file():
        raise RuntimeError("marker output missing")
    with _atomic_marker_path.open("r", encoding="utf-8") as _atomic_marker_stream:
        _atomic_marker_payload = _atomic_json.load(_atomic_marker_stream)
    if not isinstance(_atomic_marker_payload, dict):
        raise RuntimeError("marker output must use the versioned envelope")
    _atomic_marker_rows = _atomic_marker_payload.get("markers")
    _atomic_marker_selection = (
        _atomic_marker_payload.get("metadata") or {{}}
    ).get("selection")
    if (
        not isinstance(_atomic_marker_rows, list)
        or not _atomic_marker_rows
        or not isinstance(_atomic_marker_selection, dict)
    ):
        raise RuntimeError("marker output or selection evidence is empty")
    _atomic_marker_count = len(_atomic_marker_rows)
    if _atomic_marker_selection.get("marker_count") != _atomic_marker_count:
        raise RuntimeError("marker selection count postcondition failed")
    _atomic_validation_checks.extend(
        ["marker_thresholds_verified", "marker_cluster_coverage_verified"]
    )
elif {binding.mode!r} == "visualize":
    if not _atomic_images:
        raise RuntimeError("visualization output missing")
    if _atomic_after != _atomic_before:
        raise RuntimeError("visualization unexpectedly changed dataset state")
    _atomic_validation_checks.append("image_artifact_verified")

_atomic_scientific_evidence = {{
    "schema_version": 1,
    "evidence_level": "validated_observation",
    "operation": {binding.tool_name!r},
    "source_artifact_id": _atomic_source_artifact_id,
    "disposition": _atomic_operation_disposition,
    "parameters": tool_parameters,
    "random_seed": _atomic_random_seed,
    "input_state": _atomic_before,
    "output_state": _atomic_after,
    "validation_status": "verified",
    "validation_checks": _atomic_validation_checks,
}}
if _atomic_marker_selection is not None:
    _atomic_scientific_evidence["marker_selection"] = _atomic_marker_selection

_atomic_metrics = {{
    "n_obs_before": _atomic_before["n_obs"],
    "n_obs_after": _atomic_after["n_obs"],
    "n_vars_before": _atomic_before["n_vars"],
    "n_vars_after": _atomic_after["n_vars"],
    "pca_reused": bool(
        {binding.tool_name!r} == "cluster_cells"
        and _atomic_operation_disposition == "reused"
    ),
    "clustering_reused": bool(
        {binding.tool_name!r} == "cluster_cells"
        and _atomic_operation_disposition == "reused"
    ),
    "normalization_applied": bool(
        {binding.tool_name!r} == "normalize_expression"
        and _atomic_operation_disposition == "executed"
    ),
    "has_pca": _atomic_after["has_pca"],
    "has_leiden": _atomic_after["has_leiden"],
    "cluster_count": len(_atomic_after["cluster_ids"]),
    "marker_count": _atomic_marker_count,
    "images": _atomic_images,
    "scientific_evidence": _atomic_scientific_evidence,
}}
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
