"""Agent-facing Graph A/B skills and typed capability handlers."""

from .artifacts import (
    ArtifactBoundaryError,
    ArtifactSizeLimitError,
    ConversationArtifactStore,
)
from .catalog import SkillCatalog, SkillCatalogError, SkillDefinition
from .contracts import (
    ArtifactRef,
    AtomicAnalysisRequest,
    AtomicAnalysisResult,
    CapabilityKind,
    CapabilitySpec,
    CapabilityStatus,
    DeepCellAnnotationRequest,
    DeepCellAnnotationResult,
    InspectDatasetContextRequest,
    InspectDatasetContextResult,
    InspectMarkerContractRequest,
    InspectMarkerContractResult,
    SingleCellAnalysisRequest,
    SingleCellAnalysisResult,
)
from .errors import CapabilityError, CapabilityExecutionError, CapabilityInputError
from .registry import (
    CapabilityContext,
    CapabilityRegistry,
    CapabilityRegistryError,
)


def build_domain_capability_layer():
    """Build lazily so importing DTOs or artifact helpers has no graph side effects."""

    from .bootstrap import build_domain_capability_layer as build

    return build()

__all__ = [
    "ArtifactBoundaryError",
    "ArtifactSizeLimitError",
    "ArtifactRef",
    "AtomicAnalysisRequest",
    "AtomicAnalysisResult",
    "CapabilityContext",
    "CapabilityError",
    "CapabilityExecutionError",
    "CapabilityInputError",
    "CapabilityKind",
    "CapabilityRegistry",
    "CapabilityRegistryError",
    "CapabilitySpec",
    "CapabilityStatus",
    "ConversationArtifactStore",
    "DeepCellAnnotationRequest",
    "DeepCellAnnotationResult",
    "InspectDatasetContextRequest",
    "InspectDatasetContextResult",
    "InspectMarkerContractRequest",
    "InspectMarkerContractResult",
    "SingleCellAnalysisRequest",
    "SingleCellAnalysisResult",
    "SkillCatalog",
    "SkillCatalogError",
    "SkillDefinition",
    "build_domain_capability_layer",
]
