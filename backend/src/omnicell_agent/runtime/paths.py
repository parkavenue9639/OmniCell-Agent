"""Conversation workspace 内的路径解析。"""

from __future__ import annotations

from pathlib import PurePosixPath

from .errors import RuntimePathError


class WorkspacePathResolver:
    """将 agent 路径限制在一个非根容器 workspace 中。"""

    def __init__(self, workspace_root: str):
        root = PurePosixPath(workspace_root)
        if not root.is_absolute() or root == PurePosixPath("/") or ".." in root.parts:
            raise RuntimePathError("workspace_root 必须是非根目录的绝对 POSIX 路径")
        self._root = root

    @property
    def root(self) -> str:
        return str(self._root)

    def resolve(self, path: str | PurePosixPath = ".") -> str:
        """解析逻辑或绝对路径；显式拒绝任何 ``..`` 词元。"""

        candidate = PurePosixPath(path)
        if ".." in candidate.parts:
            raise RuntimePathError(f"路径包含禁止的上级跳转：{path}")
        if candidate.is_absolute():
            resolved = candidate
        else:
            resolved = self._root / candidate
        if resolved != self._root and self._root not in resolved.parents:
            raise RuntimePathError(f"路径越过 conversation workspace：{path}")
        return str(resolved)

    def relative(self, path: str | PurePosixPath) -> str:
        """将安全绝对路径转回 workspace 相对标识。"""

        return str(PurePosixPath(self.resolve(path)).relative_to(self._root)) or "."

    @staticmethod
    def validate_glob(pattern: str) -> str:
        candidate = PurePosixPath(pattern)
        if not pattern or candidate.is_absolute() or ".." in candidate.parts:
            raise RuntimePathError("glob pattern 必须是非空 workspace 相对模式且不能包含 '..'")
        return pattern
