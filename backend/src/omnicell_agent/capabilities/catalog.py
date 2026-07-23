"""Agent-facing progressive Skill metadata, separate from Graph A script skills."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class SkillCatalogError(ValueError):
    pass


class SkillDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(max_length=128, pattern=r"^[a-z][a-z0-9-]*$")
    description: str = Field(min_length=1, max_length=500)
    version: str = Field(
        default="1.0",
        max_length=32,
        pattern=r"^[0-9]+\.[0-9]+$",
    )
    tools: tuple[
        Annotated[str, Field(max_length=128, pattern=r"^[a-z][a-z0-9_]*$")], ...
    ] = Field(min_length=1, max_length=20)
    content: str | None = Field(default=None, min_length=1, max_length=64 * 1024)
    source_path: Path | None = Field(default=None, exclude=True)

    def load_body(self) -> str:
        if self.content is not None:
            return self.content
        if self.source_path is None:
            raise SkillCatalogError(f"skill {self.name} 没有可加载正文")
        text = self.source_path.read_text(encoding="utf-8")
        _, body = _parse_skill_text(self.source_path, text)
        return body

    def load_subdocument(
        self,
        kind: Literal["references", "examples"],
        name: str,
    ) -> str:
        if self.source_path is None:
            raise SkillCatalogError(f"skill {self.name} 没有可加载子文档")
        normalized = _normalize_subdocument_name(name)
        root = (self.source_path.parent / kind).resolve()
        target = (root / f"{normalized}.md").resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise SkillCatalogError("skill 子文档路径逃逸") from exc
        if not target.is_file():
            available = (
                sorted(path.stem for path in root.glob("*.md"))
                if root.is_dir()
                else []
            )
            raise SkillCatalogError(
                f"skill {self.name} 的 {kind}/{normalized}.md 不存在；"
                f"可用项：{', '.join(available) or '(none)'}"
            )
        text = target.read_text(encoding="utf-8")
        if len(text.encode("utf-8")) > 64 * 1024:
            raise SkillCatalogError("skill 子文档超过 64 KiB")
        return text


class SkillCatalog:
    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}

    def register(self, skill: SkillDefinition) -> None:
        if skill.name in self._skills:
            raise SkillCatalogError(f"skill 已注册：{skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> SkillDefinition:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise SkillCatalogError(f"未知 skill：{name}") from exc

    @property
    def skills(self) -> tuple[SkillDefinition, ...]:
        return tuple(self._skills.values())

    def summaries(self) -> str:
        return "\n".join(
            f"- {skill.name}: {skill.description}" for skill in self._skills.values()
        )

    def load(
        self,
        name: str,
        *,
        reference: str | None = None,
        example: str | None = None,
    ) -> str:
        if reference and example:
            raise SkillCatalogError("reference 与 example 不能同时指定")
        skill = self.get(name)
        if reference:
            return skill.load_subdocument("references", reference)
        if example:
            return skill.load_subdocument("examples", example)
        return skill.load_body()

    @classmethod
    def load_from_directory(cls, path: str | Path) -> "SkillCatalog":
        catalog = cls()
        root = Path(path)
        for skill_path in sorted(root.glob("*/SKILL.md")):
            catalog.register(_parse_skill(skill_path))
        return catalog


def load_builtin_skill_catalog() -> SkillCatalog:
    return SkillCatalog.load_from_directory(Path(__file__).with_name("skill_definitions"))


def _parse_skill_text(path: Path, text: str) -> tuple[dict[str, str | list[str]], str]:
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        raise SkillCatalogError(f"{path} 缺少 frontmatter")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise SkillCatalogError(f"{path} frontmatter 未闭合") from exc

    scalar: dict[str, str] = {}
    tools: list[str] = []
    active_list: str | None = None
    for raw_line in lines[1:end]:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("-"):
            if active_list != "tools":
                raise SkillCatalogError(f"{path} 包含不支持的 frontmatter list")
            tools.append(stripped[1:].strip())
            continue
        if ":" not in stripped:
            raise SkillCatalogError(f"{path} frontmatter 行格式错误")
        key, value = (part.strip() for part in stripped.split(":", 1))
        if key == "tools":
            active_list = key
            if value:
                raise SkillCatalogError(f"{path} tools 必须使用列表")
            continue
        active_list = None
        scalar[key] = value.strip("\"'")

    required = {"name", "description"}
    missing = sorted(required - scalar.keys())
    if missing or not tools:
        raise SkillCatalogError(f"{path} 缺少字段：{missing or ['tools']}")
    content = "\n".join(lines[end + 1 :]).strip()
    if not content:
        raise SkillCatalogError(f"{path} skill 正文不能为空")
    if len(content.encode("utf-8")) > 64 * 1024:
        raise SkillCatalogError(f"{path} skill 正文超过 64 KiB")
    return {
        **scalar,
        "tools": tools,
    }, content


def _parse_skill(path: Path) -> SkillDefinition:
    metadata, _ = _parse_skill_text(path, path.read_text(encoding="utf-8"))
    return SkillDefinition(
        name=str(metadata["name"]),
        description=str(metadata["description"]),
        version=str(metadata.get("version", "1.0")),
        tools=tuple(str(name) for name in metadata["tools"]),
        source_path=path.resolve(),
    )


def _normalize_subdocument_name(name: str) -> str:
    raw = str(name or "").strip()
    for prefix in ("references/", "examples/"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
    if raw.endswith(".md"):
        raw = raw[:-3]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", raw):
        raise SkillCatalogError("skill 子文档名称非法")
    return raw


__all__ = [
    "SkillCatalog",
    "SkillCatalogError",
    "SkillDefinition",
    "load_builtin_skill_catalog",
]
