from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from uuid import UUID

from omnicell_agent.agent.cancellation import CancellationToken
from omnicell_agent.agent.capability_process import SubprocessCapabilityInvoker
from omnicell_agent.capabilities.artifacts import ConversationArtifactStore
from omnicell_agent.capabilities.contracts import SingleCellAnalysisRequest
from omnicell_agent.capabilities.graph_a import SingleCellAnalysisCapability
from omnicell_agent.capabilities.registry import CapabilityContext, CapabilityRegistry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--conversation-id", required=True)
    parser.add_argument("--workspace", required=True)
    return parser


async def _main() -> None:
    arguments = _parser().parse_args()
    conversation_id = UUID(arguments.conversation_id)
    store = ConversationArtifactStore(conversation_id, arguments.workspace)
    dataset_path = store.workspace / "inputs" / "dataset.h5ad"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_bytes(b"controlled-dataset")
    dataset = store.publish(
        dataset_path,
        kind="dataset",
        media_type="application/x-hdf5",
    )
    registry = CapabilityRegistry()
    registry.register(
        SingleCellAnalysisCapability(
            graph_factory=lambda: None,
            scope_factory=lambda _workspace: None,
        )
    )
    fixture_path = Path(__file__).parent
    inherited_pythonpath = os.environ.get("PYTHONPATH", "")
    invoker = SubprocessCapabilityInvoker(
        registry,
        CapabilityContext(conversation_id, store),
        bootstrap_target="capability_process_fixture:build_blocking_graph_a_docker_layer",
        child_env={
            "PYTHONPATH": os.pathsep.join(
                value
                for value in (str(fixture_path), inherited_pythonpath)
                if value
            ),
            "OMNICELL_TEST_STARTED_FILE": os.environ["OMNICELL_TEST_STARTED_FILE"],
            "OMNICELL_TEST_COUNTER_FILE": os.environ["OMNICELL_TEST_COUNTER_FILE"],
            "OMNICELL_RUNTIME_IMAGE": os.environ.get(
                "OMNICELL_RUNTIME_IMAGE", "omnicell-worker:latest"
            ),
        },
        termination_grace_seconds=0.1,
    )
    token = CancellationToken()
    token.enable_lease_watchdog(timeout_seconds=30)
    await invoker.invoke(
        "single_cell_analysis",
        SingleCellAnalysisRequest(
            dataset=dataset,
            goal="exercise parent hard-loss recovery",
        ).model_dump(mode="json"),
        cancellation=token,
    )


if __name__ == "__main__":
    asyncio.run(_main())
