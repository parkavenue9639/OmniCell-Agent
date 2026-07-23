from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from omnicell_agent.agent.cancellation import CancellationToken, RunCancelledError
from omnicell_agent.agent.capability_process import (
    CapabilityProcessError,
    RuntimeCleanupError,
    SubprocessCapabilityInvoker,
    _runtime_claim_path,
    _runtime_claim_root,
    reap_workspace_runtime_claims,
)
from omnicell_agent.capabilities.worker import (
    _ACTIVITY_FRAME_MAX_BYTES,
    _encode_runtime_activity_frame,
)
from omnicell_agent.capabilities.artifacts import ConversationArtifactStore
from omnicell_agent.capabilities.bootstrap import build_domain_capability_layer
from omnicell_agent.capabilities.contracts import (
    DeepCellAnnotationRequest,
    SingleCellAnalysisRequest,
)
from omnicell_agent.capabilities.graph_a import SingleCellAnalysisCapability
from omnicell_agent.capabilities.graph_b import DeepCellAnnotationCapability
from omnicell_agent.capabilities.registry import CapabilityContext, CapabilityRegistry
from omnicell_agent.runtime import DockerCommandResult
from omnicell_agent.schema.contract import MarkerGene, MarkerTableContract


_RUN_DOCKER = os.environ.get("OMNICELL_RUN_DOCKER_TESTS") == "1"


def test_runtime_activity_frame_truncates_json_expansion_without_failure() -> None:
    source = "\\\n\"" * 30_000
    encoded = _encode_runtime_activity_frame(
        {
            "kind": "runtime.command_started",
            "command_id": "a" * 32,
            "backend": "local-docker-cli",
            "command": ["python", "-c", "\\\n\"" * 12_000],
            "script": source,
            "workdir": "/app/data",
            "command_truncated": False,
            "redacted": False,
        }
    )
    activity = json.loads(encoded)

    assert len(encoded) <= _ACTIVITY_FRAME_MAX_BYTES
    assert activity["command_truncated"] is True
    assert activity["script"] == source[: len(activity["script"])]
    assert len(activity["script"]) < len(source)


@pytest.mark.asyncio
async def test_runtime_activity_pipe_forwards_only_valid_typed_frames() -> None:
    read_descriptor, write_descriptor = os.pipe()
    received: list[dict[str, object]] = []
    frame = _encode_runtime_activity_frame(
        {
            "kind": "runtime.output",
            "command_id": "a" * 32,
            "stream": "stdout",
            "index": 0,
            "chunk": "controlled\n",
            "encoding": "utf8",
            "truncated": False,
            "redacted": False,
        }
    )
    os.write(write_descriptor, frame)
    os.close(write_descriptor)

    await SubprocessCapabilityInvoker._forward_runtime_activities(
        read_descriptor,
        on_activity=lambda activity: _record_activity(received, activity),
    )

    assert received == [
        {
            "kind": "runtime.output",
            "command_id": "a" * 32,
            "stream": "stdout",
            "index": 0,
            "chunk": "controlled\n",
            "encoding": "utf8",
            "truncated": False,
            "redacted": False,
        }
    ]


@pytest.mark.asyncio
async def test_runtime_activity_pipe_rejects_malformed_frame() -> None:
    read_descriptor, write_descriptor = os.pipe()
    os.write(write_descriptor, b"{not-json}\n")
    os.close(write_descriptor)

    with pytest.raises(
        CapabilityProcessError,
        match="runtime activity frame 不是合法 JSON",
    ):
        await SubprocessCapabilityInvoker._forward_runtime_activities(
            read_descriptor,
            on_activity=None,
        )


async def _record_activity(
    received: list[dict[str, object]],
    activity,
) -> None:
    received.append(dict(activity))


def _marker_ref(store: ConversationArtifactStore):
    path = store.workspace / "inputs" / "markers.json"
    MarkerTableContract(
        markers=[
            MarkerGene(
                gene_name="IL7R",
                cluster_id="0",
                p_val=0.001,
                p_val_adj=0.01,
                log2FC=2.5,
                pct_1=0.8,
                pct_2=0.1,
            )
        ]
    ).save_to_json(path)
    return store.publish(
        path,
        kind="marker_table",
        media_type="application/json",
    )


def _dataset_ref(store: ConversationArtifactStore):
    path = store.workspace / "inputs" / "dataset.h5ad"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"controlled-dataset")
    return store.publish(
        path,
        kind="dataset",
        media_type="application/x-hdf5",
    )


async def _wait_for_file(path: Path, *, timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not path.is_file():
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_builtin_worker_round_trip_uses_bounded_json_protocol(tmp_path) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path / "workspace")
    marker_ref = _marker_ref(store)
    layer = build_domain_capability_layer()
    invoker = SubprocessCapabilityInvoker(
        layer.registry,
        CapabilityContext(conversation_id, store),
    )
    shadow_sentinel = tmp_path / "workspace-shadow-executed"
    shadow_package = store.workspace / "omnicell_agent" / "capabilities"
    shadow_package.mkdir(parents=True)
    (shadow_package.parent / "__init__.py").write_text("", encoding="utf-8")
    (shadow_package / "__init__.py").write_text("", encoding="utf-8")
    (shadow_package / "worker.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(shadow_sentinel)!r}).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )

    result = await invoker.invoke(
        "inspect_marker_contract",
        {"marker_table": marker_ref.model_dump(mode="json")},
        cancellation=CancellationToken(),
    )

    assert result.marker_count == 1
    assert result.cluster_count == 1
    assert result.source_marker_table == marker_ref
    assert not shadow_sentinel.exists()


@pytest.mark.asyncio
async def test_unknown_child_failure_does_not_expose_secret_or_host_path(tmp_path) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path / "workspace")
    marker_ref = _marker_ref(store)
    layer = build_domain_capability_layer()
    fixture_path = Path(__file__).parents[1] / "fixtures"
    inherited_pythonpath = os.environ.get("PYTHONPATH", "")
    secret = "super-secret-token"
    host_path = str(tmp_path / "private-host-path")
    invoker = SubprocessCapabilityInvoker(
        layer.registry,
        CapabilityContext(conversation_id, store),
        bootstrap_target="capability_process_fixture:build_secret_failure_layer",
        child_env={
            "PYTHONPATH": os.pathsep.join(
                value
                for value in (str(fixture_path), inherited_pythonpath)
                if value
            ),
            "OMNICELL_TEST_SECRET_FAILURE": f"{secret}:{host_path}",
        },
    )

    with pytest.raises(CapabilityProcessError) as captured:
        await invoker.invoke(
            "inspect_marker_contract",
            {"marker_table": marker_ref.model_dump(mode="json")},
            cancellation=CancellationToken(),
        )

    public_error = str(captured.value)
    assert "isolated capability failed" in public_error
    assert secret not in public_error
    assert host_path not in public_error


@pytest.mark.asyncio
async def test_child_stdout_and_stderr_flood_is_discarded_without_blocking(tmp_path) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path / "workspace")
    marker_ref = _marker_ref(store)
    layer = build_domain_capability_layer()
    fixture_path = Path(__file__).parents[1] / "fixtures"
    inherited_pythonpath = os.environ.get("PYTHONPATH", "")
    invoker = SubprocessCapabilityInvoker(
        layer.registry,
        CapabilityContext(conversation_id, store),
        bootstrap_target="capability_process_fixture:build_noisy_inspect_layer",
        child_env={
            "PYTHONPATH": os.pathsep.join(
                value
                for value in (str(fixture_path), inherited_pythonpath)
                if value
            )
        },
    )

    result = await invoker.invoke(
        "inspect_marker_contract",
        {"marker_table": marker_ref.model_dump(mode="json")},
        cancellation=CancellationToken(),
    )

    assert result.marker_count == 1


@pytest.mark.asyncio
async def test_child_failure_cannot_bypass_unresolved_runtime_cleanup(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path / "workspace")
    marker_ref = _marker_ref(store)
    layer = build_domain_capability_layer()
    fixture_path = Path(__file__).parents[1] / "fixtures"
    inherited_pythonpath = os.environ.get("PYTHONPATH", "")
    fake = _ClaimDockerCLI(
        invocation_id="f" * 32,
        matching_label=True,
        missing=True,
    )
    monkeypatch.setattr(
        "omnicell_agent.agent.capability_process.DockerCLI", lambda: fake
    )
    monkeypatch.setattr(
        "omnicell_agent.agent.capability_process._PROVISIONAL_CLEANUP_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        "omnicell_agent.agent.capability_process._PROVISIONAL_CLEANUP_POLL_SECONDS",
        0.005,
    )
    invoker = SubprocessCapabilityInvoker(
        layer.registry,
        CapabilityContext(conversation_id, store),
        bootstrap_target="capability_process_fixture:build_provisional_failure_layer",
        child_env={
            "PYTHONPATH": os.pathsep.join(
                value
                for value in (str(fixture_path), inherited_pythonpath)
                if value
            )
        },
    )

    with pytest.raises(RuntimeCleanupError, match="尚未确认"):
        await invoker.invoke(
            "inspect_marker_contract",
            {"marker_table": marker_ref.model_dump(mode="json")},
            cancellation=CancellationToken(),
        )

    claims = list(_runtime_claim_root(store.workspace, create=False).glob("*.json"))
    assert len(claims) == 1
    assert json.loads(claims[0].read_text(encoding="utf-8"))["state"] == "provisional"


@pytest.mark.parametrize(
    ("ok", "returncode"),
    [(True, 1), (False, 0)],
)
def test_protocol_response_must_match_child_returncode(
    tmp_path, ok: bool, returncode: int
) -> None:
    response_path = tmp_path / "response.json"
    response_path.write_text(
        json.dumps(
            {
                "protocol_version": 1,
                "ok": ok,
                "result": {},
                "error_type": "RuntimeError",
                "message": "isolated capability failed",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(CapabilityProcessError, match="退出状态冲突"):
        SubprocessCapabilityInvoker._read_response(
            response_path,
            returncode=returncode,
        )


class _ClaimDockerCLI:
    def __init__(
        self,
        *,
        invocation_id: str,
        matching_label: bool,
        missing: bool = False,
    ) -> None:
        self.invocation_id = invocation_id
        self.matching_label = matching_label
        self.missing = missing
        self.calls: list[tuple[str, ...]] = []
        self.immutable_id = "a" * 64

    async def run(self, args, **_kwargs) -> DockerCommandResult:
        argv = tuple(args)
        self.calls.append(argv)
        if argv[:2] == ("container", "inspect"):
            if self.missing:
                return DockerCommandResult(argv, 1, b"", b"No such container")
            label = self.invocation_id if self.matching_label else "b" * 32
            return DockerCommandResult(
                argv,
                0,
                json.dumps(
                    [
                        {
                            "Id": self.immutable_id,
                            "Config": {
                                "Labels": {"omnicell.runtime.invocation": label}
                            },
                        }
                    ]
                ).encode(),
                b"",
            )
        if argv[:2] == ("rm", "--force"):
            return DockerCommandResult(argv, 0, b"", b"")
        raise AssertionError(argv)


@pytest.mark.asyncio
async def test_runtime_reaper_removes_only_label_verified_immutable_container(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    invocation_id = "1" * 32
    claim = _runtime_claim_path(workspace, invocation_id)
    claim.write_text(
        json.dumps(
            {
                "invocation_id": invocation_id,
                "container_id": "user-controlled-name",
                "state": "confirmed",
            }
        ),
        encoding="utf-8",
    )
    scope = workspace / ".omnicell-invocations" / invocation_id
    scope.mkdir(parents=True)
    (scope / "partial.txt").write_text("partial", encoding="utf-8")
    fake = _ClaimDockerCLI(invocation_id=invocation_id, matching_label=True)
    monkeypatch.setattr(
        "omnicell_agent.agent.capability_process.DockerCLI", lambda: fake
    )

    assert await reap_workspace_runtime_claims(workspace) == (invocation_id,)
    assert fake.calls == [
        ("container", "inspect", "user-controlled-name"),
        ("rm", "--force", fake.immutable_id),
    ]
    assert not claim.exists()
    assert not scope.exists()


@pytest.mark.asyncio
async def test_runtime_reaper_rejects_forged_claim_without_removing_container(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    invocation_id = "2" * 32
    claim = _runtime_claim_path(workspace, invocation_id)
    claim.write_text(
        json.dumps(
            {
                "invocation_id": invocation_id,
                "container_id": "foreign-container",
                "state": "confirmed",
            }
        ),
        encoding="utf-8",
    )
    fake = _ClaimDockerCLI(invocation_id=invocation_id, matching_label=False)
    monkeypatch.setattr(
        "omnicell_agent.agent.capability_process.DockerCLI", lambda: fake
    )

    with pytest.raises(RuntimeCleanupError, match="回收校验失败"):
        await reap_workspace_runtime_claims(workspace)

    assert fake.calls == [("container", "inspect", "foreign-container")]
    assert claim.exists()


@pytest.mark.asyncio
async def test_runtime_reaper_preserves_missing_provisional_claim_for_retry(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    invocation_id = "3" * 32
    claim = _runtime_claim_path(workspace, invocation_id)
    claim.write_text(
        json.dumps(
            {
                "invocation_id": invocation_id,
                "container_id": "provisional-name",
                "state": "provisional",
            }
        ),
        encoding="utf-8",
    )
    fake = _ClaimDockerCLI(
        invocation_id=invocation_id,
        matching_label=True,
        missing=True,
    )
    monkeypatch.setattr(
        "omnicell_agent.agent.capability_process.DockerCLI", lambda: fake
    )
    monkeypatch.setattr(
        "omnicell_agent.agent.capability_process._PROVISIONAL_CLEANUP_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        "omnicell_agent.agent.capability_process._PROVISIONAL_CLEANUP_POLL_SECONDS",
        0.005,
    )

    with pytest.raises(RuntimeCleanupError, match="尚未确认"):
        await reap_workspace_runtime_claims(workspace)
    assert fake.calls
    assert all(
        call == ("container", "inspect", "provisional-name")
        for call in fake.calls
    )
    assert claim.exists()


@pytest.mark.asyncio
async def test_runtime_reaper_retries_until_provisional_container_appears(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    invocation_id = "4" * 32
    claim = _runtime_claim_path(workspace, invocation_id)
    claim.write_text(
        json.dumps(
            {
                "invocation_id": invocation_id,
                "container_id": "delayed-container",
                "state": "provisional",
            }
        ),
        encoding="utf-8",
    )
    fake = _ClaimDockerCLI(
        invocation_id=invocation_id,
        matching_label=True,
        missing=True,
    )
    monkeypatch.setattr(
        "omnicell_agent.agent.capability_process.DockerCLI", lambda: fake
    )
    monkeypatch.setattr(
        "omnicell_agent.agent.capability_process._PROVISIONAL_CLEANUP_POLL_SECONDS",
        0.01,
    )

    cleanup = asyncio.create_task(reap_workspace_runtime_claims(workspace))
    await asyncio.sleep(0.02)
    fake.missing = False

    assert await cleanup == (invocation_id,)
    assert fake.calls[-1] == ("rm", "--force", fake.immutable_id)
    assert not claim.exists()


@pytest.mark.asyncio
async def test_graph_b_process_is_killed_and_partial_scope_is_discarded(tmp_path) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path / "workspace")
    marker_ref = _marker_ref(store)
    registry = CapabilityRegistry()
    registry.register(DeepCellAnnotationCapability(graph_factory=lambda: None))
    fixture_path = Path(__file__).parents[1] / "fixtures"
    inherited_pythonpath = os.environ.get("PYTHONPATH", "")
    child_pythonpath = os.pathsep.join(
        value for value in (str(fixture_path), inherited_pythonpath) if value
    )
    started = tmp_path / "started"
    counter = tmp_path / "counter"
    invoker = SubprocessCapabilityInvoker(
        registry,
        CapabilityContext(conversation_id, store),
        bootstrap_target=(
            "capability_process_fixture:build_blocking_graph_b_layer"
        ),
        child_env={
            "PYTHONPATH": child_pythonpath,
            "OMNICELL_TEST_STARTED_FILE": str(started),
            "OMNICELL_TEST_COUNTER_FILE": str(counter),
        },
        termination_grace_seconds=0.1,
    )
    token = CancellationToken()
    invocation = asyncio.create_task(
        invoker.invoke(
            "deep_cell_annotation",
            DeepCellAnnotationRequest(
                marker_table=marker_ref,
                species="Human",
                tissue="PBMC",
            ).model_dump(mode="json"),
            cancellation=token,
        )
    )
    await _wait_for_file(started)
    token.cancel("controlled Graph B cancellation")

    with pytest.raises(RunCancelledError):
        await asyncio.wait_for(invocation, timeout=3)

    stopped_size = counter.stat().st_size
    await asyncio.sleep(0.2)
    assert counter.stat().st_size == stopped_size
    invocation_root = store.workspace / ".omnicell-invocations"
    assert not invocation_root.exists() or list(invocation_root.iterdir()) == []


@pytest.mark.asyncio
async def test_graph_a_process_is_killed_and_partial_scope_is_discarded(tmp_path) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path / "workspace")
    dataset_ref = _dataset_ref(store)
    registry = CapabilityRegistry()
    registry.register(
        SingleCellAnalysisCapability(
            graph_factory=lambda: None,
            scope_factory=lambda _workspace: None,
        )
    )
    fixture_path = Path(__file__).parents[1] / "fixtures"
    inherited_pythonpath = os.environ.get("PYTHONPATH", "")
    started = tmp_path / "graph-a-started"
    counter = tmp_path / "graph-a-counter"
    invoker = SubprocessCapabilityInvoker(
        registry,
        CapabilityContext(conversation_id, store),
        bootstrap_target=(
            "capability_process_fixture:build_blocking_graph_a_layer"
        ),
        child_env={
            "PYTHONPATH": os.pathsep.join(
                value
                for value in (str(fixture_path), inherited_pythonpath)
                if value
            ),
            "OMNICELL_TEST_STARTED_FILE": str(started),
            "OMNICELL_TEST_COUNTER_FILE": str(counter),
        },
        termination_grace_seconds=0.1,
    )
    token = CancellationToken()
    invocation = asyncio.create_task(
        invoker.invoke(
            "single_cell_analysis",
            SingleCellAnalysisRequest(
                dataset=dataset_ref,
                goal="controlled cancellation",
            ).model_dump(mode="json"),
            cancellation=token,
        )
    )
    await _wait_for_file(started)
    token.cancel("controlled Graph A cancellation")

    with pytest.raises(RunCancelledError):
        await asyncio.wait_for(invocation, timeout=3)

    stopped_size = counter.stat().st_size
    await asyncio.sleep(0.2)
    assert counter.stat().st_size == stopped_size
    invocation_root = store.workspace / ".omnicell-invocations"
    assert not invocation_root.exists() or list(invocation_root.iterdir()) == []


@pytest.mark.asyncio
async def test_lease_watchdog_kills_graph_b_without_parent_renewal(tmp_path) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path / "workspace")
    marker_ref = _marker_ref(store)
    registry = CapabilityRegistry()
    registry.register(DeepCellAnnotationCapability(graph_factory=lambda: None))
    fixture_path = Path(__file__).parents[1] / "fixtures"
    inherited_pythonpath = os.environ.get("PYTHONPATH", "")
    started = tmp_path / "watchdog-started"
    counter = tmp_path / "watchdog-counter"
    invoker = SubprocessCapabilityInvoker(
        registry,
        CapabilityContext(conversation_id, store),
        bootstrap_target=(
            "capability_process_fixture:build_blocking_graph_b_layer"
        ),
        child_env={
            "PYTHONPATH": os.pathsep.join(
                value
                for value in (str(fixture_path), inherited_pythonpath)
                if value
            ),
            "OMNICELL_TEST_STARTED_FILE": str(started),
            "OMNICELL_TEST_COUNTER_FILE": str(counter),
        },
        termination_grace_seconds=0.1,
    )
    token = CancellationToken()
    token.enable_lease_watchdog(timeout_seconds=1.0)

    with pytest.raises(RunCancelledError, match="watchdog"):
        await asyncio.wait_for(
            invoker.invoke(
                "deep_cell_annotation",
                DeepCellAnnotationRequest(
                    marker_table=marker_ref,
                    species="Human",
                    tissue="PBMC",
                ).model_dump(mode="json"),
                cancellation=token,
            ),
            timeout=4,
        )

    stopped_size = counter.stat().st_size
    await asyncio.sleep(0.2)
    assert counter.stat().st_size == stopped_size


@pytest.mark.docker
@pytest.mark.skipif(
    not _RUN_DOCKER,
    reason="设置 OMNICELL_RUN_DOCKER_TESTS=1 后验证 Graph A worker 的真实容器回收",
)
@pytest.mark.asyncio
async def test_graph_a_forced_kill_reaps_exact_owned_docker_container(tmp_path) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path / "workspace")
    dataset_ref = _dataset_ref(store)
    registry = CapabilityRegistry()
    registry.register(
        SingleCellAnalysisCapability(
            graph_factory=lambda: None,
            scope_factory=lambda _workspace: None,
        )
    )
    fixture_path = Path(__file__).parents[1] / "fixtures"
    inherited_pythonpath = os.environ.get("PYTHONPATH", "")
    started = tmp_path / "docker-graph-a-started"
    counter = tmp_path / "docker-graph-a-counter"
    invoker = SubprocessCapabilityInvoker(
        registry,
        CapabilityContext(conversation_id, store),
        bootstrap_target=(
            "capability_process_fixture:build_blocking_graph_a_docker_layer"
        ),
        child_env={
            "PYTHONPATH": os.pathsep.join(
                value
                for value in (str(fixture_path), inherited_pythonpath)
                if value
            ),
            "OMNICELL_TEST_STARTED_FILE": str(started),
            "OMNICELL_TEST_COUNTER_FILE": str(counter),
            "OMNICELL_RUNTIME_IMAGE": "omnicell-worker:latest",
        },
        termination_grace_seconds=0.1,
    )
    token = CancellationToken()
    invocation = asyncio.create_task(
        invoker.invoke(
            "single_cell_analysis",
            SingleCellAnalysisRequest(
                dataset=dataset_ref,
                goal="force kill after Docker startup",
            ).model_dump(mode="json"),
            cancellation=token,
        )
    )
    await _wait_for_file(started, timeout=15)
    invocation_id = started.read_text(encoding="utf-8")
    filter_value = f"label=omnicell.runtime.invocation={invocation_id}"
    before = subprocess.check_output(
        ["docker", "ps", "--all", "--quiet", "--filter", filter_value],
        text=True,
    ).strip()
    assert before

    token.cancel("forced Graph A Docker cancellation")
    with pytest.raises(RunCancelledError):
        await asyncio.wait_for(invocation, timeout=15)

    after = subprocess.check_output(
        ["docker", "ps", "--all", "--quiet", "--filter", filter_value],
        text=True,
    ).strip()
    assert after == ""
    stopped_size = counter.stat().st_size
    await asyncio.sleep(0.2)
    assert counter.stat().st_size == stopped_size


@pytest.mark.docker
@pytest.mark.skipif(
    not _RUN_DOCKER,
    reason="设置 OMNICELL_RUN_DOCKER_TESTS=1 后验证父进程硬退出后的 durable 回收",
)
@pytest.mark.asyncio
async def test_parent_hard_loss_is_recovered_from_durable_docker_claim(
    tmp_path,
) -> None:
    conversation_id = uuid4()
    workspace = tmp_path / "workspace"
    started = tmp_path / "hard-loss-started"
    counter = tmp_path / "hard-loss-counter"
    fixture_path = Path(__file__).parents[1] / "fixtures"
    source_path = Path(__file__).parents[2] / "src"
    inherited_pythonpath = os.environ.get("PYTHONPATH", "")
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": os.pathsep.join(
                value
                for value in (
                    str(source_path),
                    str(fixture_path),
                    inherited_pythonpath,
                )
                if value
            ),
            "OMNICELL_TEST_STARTED_FILE": str(started),
            "OMNICELL_TEST_COUNTER_FILE": str(counter),
            "OMNICELL_RUNTIME_IMAGE": "omnicell-worker:latest",
        }
    )
    supervisor = await asyncio.create_subprocess_exec(
        sys.executable,
        str(fixture_path / "capability_process_supervisor.py"),
        "--conversation-id",
        str(conversation_id),
        "--workspace",
        str(workspace),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
        start_new_session=True,
    )
    owned_container_ids: list[str] = []
    try:
        await _wait_for_file(started, timeout=20)
        invocation_id = started.read_text(encoding="utf-8")
        claim = _runtime_claim_path(workspace, invocation_id)
        await _wait_for_file(claim)
        assert json.loads(claim.read_text(encoding="utf-8"))["state"] == "confirmed"
        filter_value = f"label=omnicell.runtime.invocation={invocation_id}"
        before = subprocess.check_output(
            ["docker", "ps", "--all", "--quiet", "--filter", filter_value],
            text=True,
        ).split()
        assert before
        owned_container_ids.extend(before)

        supervisor.kill()
        await asyncio.wait_for(supervisor.wait(), timeout=5)
        await asyncio.sleep(1.5)
        stopped_size = counter.stat().st_size
        await asyncio.sleep(0.2)
        assert counter.stat().st_size == stopped_size
        assert claim.is_file()

        assert await reap_workspace_runtime_claims(workspace) == (invocation_id,)
        after = subprocess.check_output(
            ["docker", "ps", "--all", "--quiet", "--filter", filter_value],
            text=True,
        ).strip()
        assert after == ""
        assert not claim.exists()
        assert not (
            workspace / ".omnicell-invocations" / invocation_id
        ).exists()
        owned_container_ids.clear()
    finally:
        if supervisor.returncode is None:
            supervisor.kill()
            await supervisor.wait()
        for container_id in owned_container_ids:
            subprocess.run(
                ["docker", "rm", "--force", container_id],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
