from __future__ import annotations

import io
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from omnicell_agent.capabilities.artifacts import (
    ArtifactBoundaryError,
    ArtifactSizeLimitError,
    ConversationArtifactStore,
)
from omnicell_agent.runtime.output_policy import OutputQuota
from omnicell_agent.capabilities.bootstrap import (
    build_domain_capability_layer,
    validate_skill_tool_references,
)
from omnicell_agent.capabilities.catalog import (
    SkillCatalog,
    SkillDefinition,
    load_builtin_skill_catalog,
)
from omnicell_agent.capabilities.contracts import (
    AnalysisStepSummary,
    ArtifactRef,
    CapabilityKind,
    CapabilityRequest,
    CapabilitySpec,
    MarkerClusterSummary,
)
from omnicell_agent.capabilities.registry import (
    CapabilityContext,
    CapabilityRegistry,
    CapabilityRegistryError,
)


def test_conversation_artifact_store_resolves_only_owned_immutable_reference(
    tmp_path: Path,
) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path / "conversation")
    dataset_path = store.workspace / "input" / "cells.h5ad"
    dataset_path.parent.mkdir()
    dataset_path.write_bytes(b"h5ad-fixture")
    ref = store.publish(
        dataset_path,
        kind="dataset",
        media_type="application/x-hdf5",
    )

    assert store.resolve(ref) == dataset_path
    assert store.sandbox_path(ref) == "/app/data/input/cells.h5ad"
    assert ref.uri == "workspace://input/cells.h5ad"

    dataset_path.write_bytes(b"changed")
    with pytest.raises(ArtifactBoundaryError, match="size|sha256"):
        store.resolve(ref)


def test_conversation_artifact_store_rejects_cross_scope_and_path_escape(
    tmp_path: Path,
) -> None:
    first = ConversationArtifactStore(uuid4(), tmp_path / "first")
    second = ConversationArtifactStore(uuid4(), tmp_path / "second")
    path = first.workspace / "input.json"
    path.write_text("{}", encoding="utf-8")
    ref = first.publish(path, kind="marker_table", media_type="application/json")

    with pytest.raises(ArtifactBoundaryError, match="不属于"):
        second.resolve(ref)
    with pytest.raises(ArtifactBoundaryError, match="相对路径"):
        first.write_text("../outside.txt", "no", kind="text")

    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    symlink = first.workspace / "outside-link"
    symlink.symlink_to(outside)
    with pytest.raises(ArtifactBoundaryError, match="逃逸"):
        first.publish(symlink, kind="text")


def test_verified_artifact_handle_pins_inode_across_path_replacement(
    tmp_path: Path,
) -> None:
    store = ConversationArtifactStore(uuid4(), tmp_path / "conversation")
    path = store.workspace / "result.txt"
    path.write_bytes(b"verified-content")
    ref = store.publish(path, kind="text")

    handle = store.open_verified(ref)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"host-secret")
    path.unlink()
    path.symlink_to(outside)
    try:
        assert handle.read() == b"verified-content"
    finally:
        handle.close()

    with pytest.raises(ArtifactBoundaryError, match="symlink|安全打开"):
        store.open_verified(ref)


def test_invocation_store_rejects_marker_symlink_to_previous_scope(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "conversation"
    previous = workspace / ".omnicell-invocations" / ("a" * 32) / "markers.json"
    previous.parent.mkdir(parents=True)
    previous.write_text("[]", encoding="utf-8")
    current_id = "b" * 32
    store = ConversationArtifactStore(
        uuid4(),
        workspace,
        invocation_id=current_id,
    )
    current = workspace / store.scoped_output_path("markers.json")
    current.parent.mkdir(parents=True)
    current.symlink_to(previous)

    with pytest.raises(ArtifactBoundaryError, match="symlink|安全打开"):
        store.publish(current, kind="marker_table", media_type="application/json")


def test_stream_import_fails_closed_if_parent_is_swapped_after_open(
    tmp_path: Path,
) -> None:
    store = ConversationArtifactStore(uuid4(), tmp_path / "conversation")
    uploads = store.workspace / "uploads"
    uploads.mkdir()
    pinned = store.workspace / "uploads-pinned"
    outside = tmp_path / "outside"
    outside.mkdir()

    class SwapOnRead(io.BytesIO):
        swapped = False

        def read(self, size: int = -1) -> bytes:
            if not self.swapped:
                self.swapped = True
                uploads.rename(pinned)
                uploads.symlink_to(outside, target_is_directory=True)
            return super().read(size)

    with pytest.raises(ArtifactBoundaryError, match="symlink|安全打开"):
        store.import_stream(
            "uploads/input.bin",
            SwapOnRead(b"payload"),
            max_bytes=1024,
            kind="dataset",
        )

    assert list(outside.iterdir()) == []
    assert list(pinned.iterdir()) == []


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({"a.bin": 5}, "单文件|上限"),
        ({"a.bin": 4, "b.bin": 3}, "总字节|上限"),
        ({"a.bin": 1, "b.bin": 1, "c.bin": 1}, "文件数|上限"),
    ],
)
def test_invocation_artifact_scan_has_file_count_size_and_total_hard_limits(
    tmp_path: Path,
    files: dict[str, int],
    message: str,
) -> None:
    invocation_id = "e" * 32
    store = ConversationArtifactStore(
        uuid4(),
        tmp_path / "conversation",
        invocation_id=invocation_id,
        output_quota=OutputQuota(
            max_files=2,
            file_max_bytes=4,
            total_max_bytes=6,
        ),
    )
    output = store.workspace / store.output_scope  # type: ignore[arg-type]
    output.mkdir(parents=True)
    for name, size in files.items():
        with (output / name).open("wb") as handle:
            handle.truncate(size)

    with pytest.raises(ArtifactSizeLimitError, match=message):
        store.snapshot_files()


def test_invocation_runtime_state_counts_toward_quota_but_is_not_published(
    tmp_path: Path,
) -> None:
    invocation_id = "f" * 32
    store = ConversationArtifactStore(
        uuid4(),
        tmp_path / "conversation",
        invocation_id=invocation_id,
        output_quota=OutputQuota(
            max_files=2,
            file_max_bytes=4,
            total_max_bytes=6,
        ),
    )
    output = store.workspace / store.output_scope  # type: ignore[arg-type]
    runtime = output / ".runtime"
    runtime.mkdir(parents=True)
    (runtime / "state.bin").write_bytes(b"1234")
    (output / "result.txt").write_bytes(b"12")

    assert store.snapshot_files() == frozenset(
        {f"{store.output_scope}/result.txt"}
    )
    (output / "overflow.txt").write_bytes(b"1")
    with pytest.raises(ArtifactSizeLimitError, match="文件数|上限"):
        store.snapshot_files()


def test_artifact_reference_requires_canonical_kind_size_and_hash(
    tmp_path: Path,
) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path / "conversation")
    path = store.workspace / "input.h5ad"
    path.write_bytes(b"dataset")
    ref = store.publish(path, kind="dataset", metadata={"source": "upload"})

    weak_payload = ref.model_dump()
    weak_payload.pop("size_bytes")
    weak_payload.pop("sha256")
    with pytest.raises(ValidationError):
        ArtifactRef.model_validate(weak_payload)

    relabeled = ref.model_copy(update={"kind": "marker_table"})
    with pytest.raises(ArtifactBoundaryError, match="权威登记"):
        store.resolve(relabeled, expected_kind="marker_table")

    ref.metadata["source"] = "rewritten"
    with pytest.raises(ArtifactBoundaryError, match="权威登记"):
        store.resolve(ref)


def test_artifact_reference_tool_schema_requires_nullable_identity_fields() -> None:
    schema = ArtifactRef.model_json_schema()

    assert set(schema["required"]) == {
        "artifact_id",
        "conversation_id",
        "kind",
        "uri",
        "media_type",
        "size_bytes",
        "sha256",
        "metadata",
    }

    with pytest.raises(ValidationError):
        ArtifactRef.model_validate(
            {
                "artifact_id": str(uuid4()),
                "conversation_id": str(uuid4()),
                "kind": "dataset",
                "uri": "workspace://input.h5ad",
                "size_bytes": 1,
                "sha256": "0" * 64,
            }
        )


def test_artifact_store_requires_explicit_trusted_hydration_after_restart(
    tmp_path: Path,
) -> None:
    conversation_id = uuid4()
    workspace = tmp_path / "conversation"
    first = ConversationArtifactStore(conversation_id, workspace)
    path = workspace / "input.json"
    path.write_text("{}", encoding="utf-8")
    ref = first.publish(path, kind="marker_table")

    restarted = ConversationArtifactStore(conversation_id, workspace)
    with pytest.raises(ArtifactBoundaryError, match="未在"):
        restarted.resolve(ref)

    hydrated = restarted.register_trusted(ref)
    assert restarted.resolve(hydrated) == path


def test_builtin_agent_skills_are_separate_and_reference_registered_names() -> None:
    catalog = load_builtin_skill_catalog()

    assert [skill.name for skill in catalog.skills] == [
        "deep-cell-annotation",
        "single-cell-analysis",
    ]
    assert catalog.get("single-cell-analysis").tools == (
        "inspect_single_cell_context",
        "run_qc_and_filter",
        "run_normalize_log",
        "run_pca_clustering",
        "extract_marker_genes",
        "generate_pca_scatter",
        "single_cell_analysis",
    )
    assert catalog.get("deep-cell-annotation").tools == (
        "inspect_marker_contract",
        "extract_marker_genes",
        "deep_cell_annotation",
    )


def test_builtin_skill_and_handler_registry_have_closed_bindings() -> None:
    layer = build_domain_capability_layer()

    assert [spec.name for spec in layer.registry.specs] == [
        "inspect_single_cell_context",
        "run_qc_and_filter",
        "run_normalize_log",
        "run_pca_clustering",
        "extract_marker_genes",
        "generate_pca_scatter",
        "single_cell_analysis",
        "inspect_marker_contract",
        "deep_cell_annotation",
    ]
    assert {
        tool
        for skill in layer.skills.skills
        for tool in skill.tools
    } <= {spec.name for spec in layer.registry.specs}
    assert sum(
        "extract_marker_genes" in skill.tools
        for skill in layer.skills.skills
    ) == 2


class _EchoRequest(CapabilityRequest):
    value: int


class _EchoResult(BaseModel):
    doubled: int


class _EchoCapability:
    spec = CapabilitySpec(
        name="echo_value",
        kind=CapabilityKind.ATOMIC,
        description="测试 capability",
        prompt_hint="仅在测试要求回显数值时调用。",
    )
    request_model = _EchoRequest
    result_model = _EchoResult

    def invoke(self, request, context):
        assert context.conversation_id == context.artifacts.conversation_id
        return {"doubled": request.value * 2}


class _UnreferencedCapability(_EchoCapability):
    spec = CapabilitySpec(
        name="unreferenced_value",
        kind=CapabilityKind.ATOMIC,
        description="测试未被 Skill 引用的独立 Tool",
        prompt_hint="仅用于验证 Tool 可以独立于 Skill 注册。",
    )


def test_skill_tool_references_allow_sharing_and_unreferenced_tools() -> None:
    registry = CapabilityRegistry()
    registry.register(_EchoCapability())
    registry.register(_UnreferencedCapability())
    skills = SkillCatalog()
    for name in ("first-skill", "second-skill"):
        skills.register(
            SkillDefinition(
                name=name,
                description=f"{name} description",
                tools=("echo_value",),
                content=f"{name} body",
            )
        )

    validate_skill_tool_references(registry, skills)

    unknown = SkillCatalog()
    unknown.register(
        SkillDefinition(
            name="unknown-skill",
            description="unknown",
            tools=("missing_tool",),
            content="unknown body",
        )
    )
    with pytest.raises(CapabilityRegistryError, match="未知 Tool"):
        validate_skill_tool_references(registry, unknown)


def test_capability_registry_is_instance_owned_and_validates_contracts(
    tmp_path: Path,
) -> None:
    conversation_id = uuid4()
    context = CapabilityContext(
        conversation_id,
        ConversationArtifactStore(conversation_id, tmp_path),
    )
    registry = CapabilityRegistry()
    registry.register(_EchoCapability())

    assert registry.invoke("echo_value", {"value": 4}, context) == _EchoResult(
        doubled=8
    )
    with pytest.raises(CapabilityRegistryError, match="已注册"):
        registry.register(_EchoCapability())
    with pytest.raises(CapabilityRegistryError, match="未知"):
        registry.invoke("missing", {}, context)


def test_capability_context_rejects_mismatched_artifact_scope(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="conversation"):
        CapabilityContext(
            uuid4(),
            ConversationArtifactStore(uuid4(), tmp_path),
        )


@pytest.mark.parametrize(
    "model,payload",
    [
        (
            AnalysisStepSummary,
            {
                "index": 0,
                "step_type": "x" * 129,
                "instruction": "ok",
                "status": "completed",
            },
        ),
        (
            AnalysisStepSummary,
            {
                "index": 0,
                "step_type": "skill_call",
                "instruction": "x" * 2_001,
                "status": "completed",
            },
        ),
        (
            MarkerClusterSummary,
            {
                "cluster_id": "x" * 257,
                "marker_count": 1,
                "top_markers": ["IL7R"],
            },
        ),
        (
            MarkerClusterSummary,
            {
                "cluster_id": "0",
                "marker_count": 1,
                "top_markers": ["x" * 257],
            },
        ),
    ],
)
def test_agent_facing_summary_strings_have_hard_bounds(model, payload) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_agent_facing_version_metadata_has_hard_bounds() -> None:
    oversized = f"{'1' * 100}.0"
    with pytest.raises(ValidationError):
        CapabilitySpec(
            name="bounded_version",
            kind=CapabilityKind.ATOMIC,
            description="test",
            version=oversized,
            prompt_hint="test hint",
        )
    with pytest.raises(ValidationError):
        SkillDefinition(
            name="bounded-version",
            description="test",
            version=oversized,
            tools=("bounded_version",),
            content="test",
        )
