"""Single registry for deterministic internal Recipe definitions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class RecipeCatalogError(ValueError):
    pass


class _RecipeParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _NoParameters(_RecipeParameters):
    pass


class _QualityControlParameters(_RecipeParameters):
    min_genes_per_cell: int = Field(default=200, ge=1, le=20_000)
    min_cells_per_gene: int = Field(default=3, ge=1, le=10_000)
    max_mito_percent: float = Field(default=20.0, gt=0, le=100)


class _NormalizeLogParameters(_RecipeParameters):
    target_sum: float = Field(default=10_000.0, gt=0, le=10_000_000)


class _PcaClusteringParameters(_RecipeParameters):
    n_top_genes: int = Field(default=2_000, ge=100, le=20_000)
    n_pcs: int = Field(default=40, ge=2, le=200)
    n_neighbors: int = Field(default=10, ge=2, le=200)
    resolution: float = Field(default=1.0, gt=0, le=10)


class _MarkerGeneParameters(_RecipeParameters):
    method: Literal["wilcoxon", "t-test", "logreg"] = "wilcoxon"
    top_n_per_cluster: int = Field(default=50, ge=1, le=500)
    adjusted_p_value_max: float = Field(default=0.05, gt=0, le=1)
    min_log2_fold_change: float = Field(default=1.0, ge=0, le=100)


class _PcaScatterParameters(_RecipeParameters):
    dpi: int = Field(default=300, ge=72, le=600)
    point_size: float = Field(default=50.0, gt=0, le=500)
    palette: str = Field(default="Set2", min_length=1, max_length=64)


_PARAMETER_MODELS: dict[str, type[_RecipeParameters]] = {
    "qc_and_filter": _QualityControlParameters,
    "normalize_log": _NormalizeLogParameters,
    "pca_clustering": _PcaClusteringParameters,
    "marker_genes_extractor": _MarkerGeneParameters,
    "pca_scatter": _PcaScatterParameters,
}


@dataclass(frozen=True, slots=True)
class RecipeDefinition:
    recipe_id: str
    version: str
    description: str
    source_path: Path
    script_path: Path
    parameters_model: type[_RecipeParameters]

    def normalize_parameters(
        self,
        values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return self.parameters_model.model_validate(
                values or {}
            ).model_dump(mode="json")
        except ValidationError as exc:
            raise RecipeCatalogError(
                f"Recipe {self.recipe_id} 参数不符合类型契约"
            ) from exc

    @property
    def default_parameters(self) -> dict[str, Any]:
        return self.normalize_parameters()


class RecipeCatalog:
    def __init__(self, definitions: tuple[RecipeDefinition, ...]) -> None:
        self._definitions = {
            definition.recipe_id: definition for definition in definitions
        }
        if len(self._definitions) != len(definitions):
            raise RecipeCatalogError("Recipe ID 重复")

    @property
    def definitions(self) -> tuple[RecipeDefinition, ...]:
        return tuple(self._definitions.values())

    def get(self, recipe_id: str) -> RecipeDefinition:
        try:
            return self._definitions[recipe_id]
        except KeyError as exc:
            raise RecipeCatalogError(f"未知 Recipe：{recipe_id}") from exc

    def planner_inventory(self) -> str:
        rows: list[str] = []
        for definition in self.definitions:
            defaults = definition.default_parameters
            parameter_text = (
                json.dumps(defaults, ensure_ascii=False, sort_keys=True)
                if defaults
                else "{}"
            )
            rows.append(
                f"- 【{definition.recipe_id}】{definition.description}；"
                f"参数默认值：{parameter_text}"
            )
        return "\n".join(rows)

    @classmethod
    def load_from_directory(cls, root: str | Path) -> "RecipeCatalog":
        definitions: list[RecipeDefinition] = []
        for metadata_path in sorted(Path(root).glob("*/RECIPE.md")):
            recipe_id = metadata_path.parent.name
            metadata = _parse_frontmatter(metadata_path)
            declared_name = metadata.get("name")
            if declared_name != recipe_id:
                raise RecipeCatalogError(
                    f"{metadata_path} 的 name 必须与目录 ID 一致"
                )
            script_path = metadata_path.parent / "scripts" / "execute.py"
            if not script_path.is_file():
                raise RecipeCatalogError(
                    f"Recipe {recipe_id} 缺少 scripts/execute.py"
                )
            definitions.append(
                RecipeDefinition(
                    recipe_id=recipe_id,
                    version=metadata.get("version", "1.0"),
                    description=metadata.get("description", "").strip(),
                    source_path=metadata_path.resolve(),
                    script_path=script_path.resolve(),
                    parameters_model=_PARAMETER_MODELS.get(
                        recipe_id,
                        _NoParameters,
                    ),
                )
            )
        if not definitions:
            raise RecipeCatalogError("未发现任何 Recipe")
        return cls(tuple(definitions))


def load_builtin_recipe_catalog() -> RecipeCatalog:
    return RecipeCatalog.load_from_directory(Path(__file__).resolve().parent)


def _parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        raise RecipeCatalogError(f"{path} 缺少 frontmatter")
    try:
        end = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as exc:
        raise RecipeCatalogError(f"{path} frontmatter 未闭合") from exc
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise RecipeCatalogError(f"{path} frontmatter 行格式错误")
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip("\"'")
    if not metadata.get("name") or not metadata.get("description"):
        raise RecipeCatalogError(f"{path} 缺少 name 或 description")
    return metadata


__all__ = [
    "RecipeCatalog",
    "RecipeCatalogError",
    "RecipeDefinition",
    "load_builtin_recipe_catalog",
]
