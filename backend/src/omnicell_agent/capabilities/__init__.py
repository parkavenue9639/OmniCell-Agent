"""Agent-facing scientific Skills and typed capability handlers."""

from .artifacts import (
    ArtifactBoundaryError,
    ArtifactSizeLimitError,
    ConversationArtifactStore,
)
from .catalog import SkillCatalog, SkillCatalogError, SkillDefinition
from .contracts import (
    ArtifactRef,
    AtomicAnalysisResult,
    CapabilityEffect,
    CapabilityMode,
    CapabilitySpec,
    CapabilityStatus,
    CellAnnotationClusterSummary,
    CellAnnotationRequest,
    CellAnnotationResult,
    ClusterCellsRequest,
    DatasetCapabilityRequest,
    ExploratoryAnalysisRequest,
    ExploratoryAnalysisResult,
    FindMarkerGenesRequest,
    InspectDatasetContextRequest,
    InspectDatasetContextResult,
    InspectMarkerTableRequest,
    InspectMarkerTableResult,
    NormalizeExpressionRequest,
    PlotPcaClustersRequest,
    QualityControlRequest,
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
    "AtomicAnalysisResult",
    "CapabilityEffect",
    "CapabilityMode",
    "CapabilityContext",
    "CapabilityError",
    "CapabilityExecutionError",
    "CapabilityInputError",
    "CapabilityRegistry",
    "CapabilityRegistryError",
    "CapabilitySpec",
    "CapabilityStatus",
    "CellAnnotationClusterSummary",
    "ConversationArtifactStore",
    "CellAnnotationRequest",
    "CellAnnotationResult",
    "ClusterCellsRequest",
    "DatasetCapabilityRequest",
    "ExploratoryAnalysisRequest",
    "ExploratoryAnalysisResult",
    "FindMarkerGenesRequest",
    "InspectDatasetContextRequest",
    "InspectDatasetContextResult",
    "InspectMarkerTableRequest",
    "InspectMarkerTableResult",
    "NormalizeExpressionRequest",
    "PlotPcaClustersRequest",
    "QualityControlRequest",
    "SkillCatalog",
    "SkillCatalogError",
    "SkillDefinition",
    "build_domain_capability_layer",
]
