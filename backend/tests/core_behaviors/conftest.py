"""Graph A/B 核心行为测试使用的受控模型替身。"""

from typing import Any

import pytest

from omnicell_agent.annotation.nodes.annotator import AnnotationOutput
from omnicell_agent.annotation.nodes.validator import ValidatorOutput
from omnicell_agent.pipeline.nodes.context_resolver import ContextProfile
from omnicell_agent.schema.state import AnalysisPlan, PlanStep


class ControlledStructuredModel:
    def __init__(self, schema: type, calls: list[str]):
        self._schema = schema
        self._calls = calls

    def invoke(self, messages: list[Any]) -> Any:
        schema_name = self._schema.__name__
        self._calls.append(schema_name)

        if self._schema is ContextProfile:
            return ContextProfile(
                species="Human",
                tissue="PBMC",
                disease_state="Healthy",
                goal_type="immune_profiling",
            )
        if self._schema is AnalysisPlan:
            return AnalysisPlan(
                steps=[
                    PlanStep(
                        step_type="skill_call",
                        skill_name="normalize_log",
                        instruction="执行标准归一化与对数变换",
                    )
                ]
            )
        if self._schema is AnnotationOutput:
            prompt = "\n".join(str(getattr(message, "content", "")) for message in messages)
            if "Cluster 1" in prompt:
                subtype = "B cells"
                evidence = ["MS4A1 -> B cell lineage"]
            else:
                subtype = "CD4 T cells"
                evidence = ["IL7R -> CD4 T cell lineage"]
            return AnnotationOutput(
                reasoning_chain=f"Controlled reasoning for {subtype}",
                general_type="Immune cells",
                sub_type=subtype,
                marker_evidence=evidence,
            )
        if self._schema is ValidatorOutput:
            return ValidatorOutput(
                is_supported=True,
                confidence_penalty=5,
                critique="Controlled evidence is sufficient.",
            )
        raise AssertionError(f"未注册的结构化模型契约: {schema_name}")


class ControlledChatModel:
    def __init__(self, calls: list[str]):
        self._calls = calls

    def with_structured_output(self, schema: type) -> ControlledStructuredModel:
        return ControlledStructuredModel(schema, self._calls)


@pytest.fixture
def controlled_llm_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from omnicell_agent import llm

    calls: list[str] = []
    monkeypatch.setattr(
        llm,
        "get_llm_by_alias",
        lambda *args, **kwargs: ControlledChatModel(calls),
    )
    return calls
