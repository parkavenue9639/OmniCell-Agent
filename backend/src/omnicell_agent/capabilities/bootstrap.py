"""Composition root for the built-in domain capability layer."""

from __future__ import annotations

from dataclasses import dataclass

from .atomic import build_atomic_capabilities
from .catalog import SkillCatalog, load_builtin_skill_catalog
from .graph_a import InspectSingleCellContextCapability, SingleCellAnalysisCapability
from .graph_b import DeepCellAnnotationCapability, InspectMarkerContractCapability
from .registry import CapabilityRegistry, CapabilityRegistryError


@dataclass(frozen=True)
class DomainCapabilityLayer:
    registry: CapabilityRegistry
    skills: SkillCatalog


def build_domain_capability_layer() -> DomainCapabilityLayer:
    registry = CapabilityRegistry()
    for handler in (
        InspectSingleCellContextCapability(),
        *build_atomic_capabilities(),
        SingleCellAnalysisCapability(),
        InspectMarkerContractCapability(),
        DeepCellAnnotationCapability(),
    ):
        registry.register(handler)
    skills = load_builtin_skill_catalog()
    validate_skill_tool_references(registry, skills)
    return DomainCapabilityLayer(registry=registry, skills=skills)


def validate_skill_tool_references(
    registry: CapabilityRegistry,
    skills: SkillCatalog,
) -> None:
    specs = {spec.name: spec for spec in registry.specs}
    for skill in skills.skills:
        for tool_name in skill.tools:
            if tool_name not in specs:
                raise CapabilityRegistryError(
                    f"skill {skill.name} 引用了未知 Tool：{tool_name}"
                )


__all__ = [
    "DomainCapabilityLayer",
    "build_domain_capability_layer",
    "validate_skill_tool_references",
]
