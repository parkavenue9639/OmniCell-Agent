from __future__ import annotations

import json
import os
import signal
import time
from contextlib import nullcontext
from pathlib import Path

from omnicell_agent.capabilities.bootstrap import DomainCapabilityLayer
from omnicell_agent.capabilities.catalog import SkillCatalog
from omnicell_agent.capabilities.graph_b import DeepCellAnnotationCapability
from omnicell_agent.capabilities.graph_a import SingleCellAnalysisCapability
from omnicell_agent.capabilities.graph_b import InspectMarkerContractCapability
from omnicell_agent.capabilities.registry import CapabilityRegistry


class BlockingGraphB:
    def invoke(self, state):
        del state
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        workspace = Path(os.environ["OMNICELL_CONVERSATION_WORKSPACE"])
        invocation_id = os.environ["OMNICELL_CAPABILITY_INVOCATION_ID"]
        partial = (
            workspace
            / ".omnicell-invocations"
            / invocation_id
            / "partial.txt"
        )
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_text("partial", encoding="utf-8")
        Path(os.environ["OMNICELL_TEST_STARTED_FILE"]).write_text(
            invocation_id, encoding="utf-8"
        )
        counter = Path(os.environ["OMNICELL_TEST_COUNTER_FILE"])
        while True:
            with counter.open("ab") as handle:
                handle.write(b"x")
                handle.flush()
            time.sleep(0.01)


class BlockingGraphA:
    def invoke(self, state):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        marker_relative = state["marker_table_path"][len("/app/data/") :]
        marker = (
            Path(os.environ["OMNICELL_CONVERSATION_WORKSPACE"])
            / marker_relative
        )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("partial", encoding="utf-8")
        Path(os.environ["OMNICELL_TEST_STARTED_FILE"]).write_text(
            os.environ["OMNICELL_CAPABILITY_INVOCATION_ID"], encoding="utf-8"
        )
        counter = Path(os.environ["OMNICELL_TEST_COUNTER_FILE"])
        while True:
            with counter.open("ab") as handle:
                handle.write(b"x")
                handle.flush()
            time.sleep(0.01)


def build_blocking_graph_b_layer() -> DomainCapabilityLayer:
    registry = CapabilityRegistry()
    registry.register(
        DeepCellAnnotationCapability(graph_factory=lambda: BlockingGraphB())
    )
    return DomainCapabilityLayer(registry=registry, skills=SkillCatalog())


def build_blocking_graph_a_layer() -> DomainCapabilityLayer:
    registry = CapabilityRegistry()
    registry.register(
        SingleCellAnalysisCapability(
            graph_factory=lambda: BlockingGraphA(),
            scope_factory=lambda _workspace: nullcontext(),
        )
    )
    return DomainCapabilityLayer(registry=registry, skills=SkillCatalog())


def build_blocking_graph_a_docker_layer() -> DomainCapabilityLayer:
    registry = CapabilityRegistry()
    registry.register(
        SingleCellAnalysisCapability(graph_factory=lambda: BlockingGraphA())
    )
    return DomainCapabilityLayer(registry=registry, skills=SkillCatalog())


class SecretFailureCapability(InspectMarkerContractCapability):
    def invoke(self, request, context):
        del request, context
        raise RuntimeError(os.environ["OMNICELL_TEST_SECRET_FAILURE"])


def build_secret_failure_layer() -> DomainCapabilityLayer:
    registry = CapabilityRegistry()
    registry.register(SecretFailureCapability())
    return DomainCapabilityLayer(registry=registry, skills=SkillCatalog())


class NoisyInspectCapability(InspectMarkerContractCapability):
    def invoke(self, request, context):
        chunk = b"x" * (64 * 1024)
        for _ in range(64):
            os.write(1, chunk)
            os.write(2, chunk)
        return super().invoke(request, context)


def build_noisy_inspect_layer() -> DomainCapabilityLayer:
    registry = CapabilityRegistry()
    registry.register(NoisyInspectCapability())
    return DomainCapabilityLayer(registry=registry, skills=SkillCatalog())


class ProvisionalFailureCapability(InspectMarkerContractCapability):
    def invoke(self, request, context):
        del request, context
        invocation_id = os.environ["OMNICELL_CAPABILITY_INVOCATION_ID"]
        Path(os.environ["OMNICELL_RUNTIME_OWNERSHIP_FILE"]).write_text(
            json.dumps(
                {
                    "invocation_id": invocation_id,
                    "container_id": "delayed-provisional-container",
                    "state": "provisional",
                }
            ),
            encoding="utf-8",
        )
        raise RuntimeError("controlled child failure after provisional claim")


def build_provisional_failure_layer() -> DomainCapabilityLayer:
    registry = CapabilityRegistry()
    registry.register(ProvisionalFailureCapability())
    return DomainCapabilityLayer(registry=registry, skills=SkillCatalog())
