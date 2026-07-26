"""Composition root for the built-in domain capability layer."""

from __future__ import annotations

from dataclasses import dataclass

from .atomic import build_atomic_capabilities
from .catalog import SkillCatalog, load_builtin_skill_catalog
from .cell_annotation import CellAnnotationCapability, InspectMarkerTableCapability
from .exploratory_analysis import (
    ExploratoryAnalysisCapability,
    InspectDatasetCapability,
)
from .registry import CapabilityRegistry, CapabilityRegistryError


@dataclass(frozen=True)
class DomainCapabilityLayer:
    registry: CapabilityRegistry
    skills: SkillCatalog


def build_domain_capability_layer() -> DomainCapabilityLayer:
    registry = CapabilityRegistry()
    for handler in (
        InspectDatasetCapability(),
        *build_atomic_capabilities(),
        ExploratoryAnalysisCapability(),
        InspectMarkerTableCapability(),
        CellAnnotationCapability(),
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
    skill_names = {skill.name for skill in skills.skills}
    for skill in skills.skills:
        for tool_name in skill.tools:
            if tool_name not in specs:
                raise CapabilityRegistryError(
                    f"skill {skill.name} 引用了未知 Tool：{tool_name}"
                )
    for spec in specs.values():
        for skill_name in (*spec.recommended_skills, *spec.required_skills):
            if skill_name not in skill_names:
                raise CapabilityRegistryError(
                    f"Tool {spec.name} 引用了未知 Skill：{skill_name}"
                )
        for skill_name in spec.required_skills:
            skill = skills.get(skill_name)
            if spec.name not in skill.tools:
                raise CapabilityRegistryError(
                    f"Tool {spec.name} 要求的 Skill {skill_name} 未声明该 Tool"
                )


__all__ = [
    "DomainCapabilityLayer",
    "build_domain_capability_layer",
    "validate_skill_tool_references",
]
