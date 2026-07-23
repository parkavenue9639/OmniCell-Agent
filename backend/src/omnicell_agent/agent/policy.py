"""Tool policy decisions made before a capability is executed."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from omnicell_agent.capabilities.contracts import CapabilitySpec


class ToolPolicyOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_REVIEW = "require_review"


class ToolPolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: ToolPolicyOutcome
    reason: str = Field(min_length=1, max_length=500)


class ToolPolicy(Protocol):
    def evaluate(
        self,
        spec: CapabilitySpec,
        arguments: dict[str, Any],
    ) -> ToolPolicyDecision: ...


class DefaultToolPolicy:
    """Fail closed for unknown handlers; optionally gate selected capabilities."""

    def __init__(self, *, review_capabilities: frozenset[str] = frozenset()) -> None:
        self._review_capabilities = review_capabilities

    def evaluate(
        self,
        spec: CapabilitySpec,
        arguments: dict[str, Any],
    ) -> ToolPolicyDecision:
        del arguments
        if spec.name in self._review_capabilities:
            return ToolPolicyDecision(
                outcome=ToolPolicyOutcome.REQUIRE_REVIEW,
                reason=f"capability {spec.name} 需要人工确认",
            )
        return ToolPolicyDecision(
            outcome=ToolPolicyOutcome.ALLOW,
            reason=f"capability {spec.name} 符合默认执行策略",
        )


__all__ = [
    "DefaultToolPolicy",
    "ToolPolicy",
    "ToolPolicyDecision",
    "ToolPolicyOutcome",
]
