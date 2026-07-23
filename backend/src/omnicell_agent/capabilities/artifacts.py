"""Conversation-scoped filesystem artifact adapter."""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
from uuid import UUID, uuid5

from omnicell_agent.runtime.output_policy import (
    OutputQuota,
    OutputQuotaViolation,
    OutputTreeBoundaryViolation,
    scan_output_tree,
)

from .contracts import ArtifactRef


_ARTIFACT_NAMESPACE = UUID("fe65be18-969e-4e68-b289-32961d167e91")


class ArtifactBoundaryError(ValueError):
    pass


class ArtifactSizeLimitError(ArtifactBoundaryError):
    pass


_INVOCATION_ID_PATTERN = r"^[0-9a-f]{32}$"
_INVOCATION_ROOT = ".omnicell-invocations"


class ConversationArtifactStore:
    """Maps opaque workspace references to one owned conversation directory."""

    def __init__(
        self,
        conversation_id: UUID,
        workspace: str | Path,
        *,
        invocation_id: str | None = None,
        output_quota: OutputQuota | None = None,
    ) -> None:
        self.conversation_id = conversation_id
        root = Path(workspace).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        self.workspace = root.resolve(strict=True)
        if invocation_id is not None:
            if re.fullmatch(_INVOCATION_ID_PATTERN, invocation_id) is None:
                raise ValueError("artifact invocation identity 非法")
        self.invocation_id = invocation_id
        self.output_quota = output_quota or OutputQuota()
        self._references: dict[UUID, ArtifactRef] = {}

    @property
    def output_scope(self) -> str | None:
        if self.invocation_id is None:
            return None
        return f"{_INVOCATION_ROOT}/{self.invocation_id}"

    def scoped_output_path(self, relative_path: str) -> str:
        relative = self._validate_relative(relative_path).as_posix()
        scope = self.output_scope
        return f"{scope}/{relative}" if scope is not None else relative

    def resolve(self, ref: ArtifactRef, *, expected_kind: str | None = None) -> Path:
        canonical = self._require_reference(ref, expected_kind=expected_kind)
        relative = self._relative_from_uri(canonical.uri)
        with self.open_verified(canonical, expected_kind=expected_kind):
            pass
        return self.workspace / relative

    def open_verified(
        self,
        ref: ArtifactRef,
        *,
        expected_kind: str | None = None,
    ) -> BinaryIO:
        """Open and pin one verified regular file without following workspace symlinks."""

        canonical = self._require_reference(ref, expected_kind=expected_kind)
        return self._open_reference(canonical)

    def _require_reference(
        self,
        ref: ArtifactRef,
        *,
        expected_kind: str | None,
    ) -> ArtifactRef:
        if ref.conversation_id != self.conversation_id:
            raise ArtifactBoundaryError("artifact 不属于当前 conversation")
        canonical = self._references.get(ref.artifact_id)
        if canonical is None:
            raise ArtifactBoundaryError("artifact 未在当前 conversation store 登记")
        if ref != canonical:
            raise ArtifactBoundaryError("artifact 引用与权威登记不一致")
        if expected_kind is not None and canonical.kind != expected_kind:
            raise ArtifactBoundaryError(
                f"artifact kind 必须为 {expected_kind}，实际为 {canonical.kind}"
            )
        return canonical

    def register_trusted(self, ref: ArtifactRef) -> ArtifactRef:
        """Hydrate a canonical reference previously loaded from application storage."""

        if ref.conversation_id != self.conversation_id:
            raise ArtifactBoundaryError("artifact 不属于当前 conversation")
        with self._open_reference(ref):
            pass
        return self._register_canonical(ref)

    def sandbox_path(self, ref: ArtifactRef, *, expected_kind: str | None = None) -> str:
        path = self.resolve(ref, expected_kind=expected_kind)
        relative = path.relative_to(self.workspace).as_posix()
        return f"/app/data/{relative}"

    def publish(
        self,
        path: str | Path,
        *,
        kind: str,
        media_type: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        relative = self._relative_from_path(path)
        self._require_publish_scope(relative)
        self._validate_invocation_usage()
        with self._open_relative(relative) as handle:
            size_bytes = os.fstat(handle.fileno()).st_size
            if (
                self.invocation_id is not None
                and size_bytes > self.output_quota.file_max_bytes
            ):
                raise ArtifactSizeLimitError(
                    "artifact 单文件超过 invocation 上限"
                )
            digest = self._sha256_handle(handle)
        self._validate_invocation_usage()
        resolved_media_type = media_type or mimetypes.guess_type(relative.name)[0]
        return self._register_canonical(
            self._build_reference(
                relative,
                kind=kind,
                media_type=resolved_media_type,
                metadata=metadata,
                size_bytes=size_bytes,
                digest=digest,
            )
        )

    def import_stream(
        self,
        relative_path: str,
        source: BinaryIO,
        *,
        max_bytes: int,
        kind: str,
        media_type: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        """Stream an upload through pinned directory descriptors into the workspace."""

        if max_bytes <= 0:
            raise ValueError("artifact 上传上限必须为正数")
        relative = self._validate_relative(relative_path)
        self._require_publish_scope(relative)
        try:
            source.seek(0)
        except (AttributeError, OSError):
            pass

        effective_max_bytes = max_bytes
        if self.invocation_id is not None:
            effective_max_bytes = min(
                effective_max_bytes,
                self.output_quota.file_max_bytes,
                self.output_quota.total_max_bytes,
            )
        parent_fd = self._open_parent(relative, create=True)
        temporary_name = f".{relative.name}.{os.getpid()}.{os.urandom(8).hex()}.tmp"
        file_fd = -1
        size_bytes = 0
        digest = hashlib.sha256()
        written_identity: tuple[int, int] | None = None
        committed = False
        try:
            file_fd = os.open(
                temporary_name,
                self._write_flags(exclusive=True),
                0o600,
                dir_fd=parent_fd,
            )
            with os.fdopen(file_fd, "wb", closefd=True) as destination:
                file_fd = -1
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    if size_bytes > effective_max_bytes:
                        raise ArtifactSizeLimitError(
                            f"artifact 超过写入上限 {effective_max_bytes} bytes"
                        )
                    digest.update(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
                file_stat = os.fstat(destination.fileno())
                written_identity = (file_stat.st_dev, file_stat.st_ino)
            os.replace(
                temporary_name,
                relative.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            committed = True
            with self._open_relative(relative) as verified:
                verified_stat = os.fstat(verified.fileno())
                if written_identity != (verified_stat.st_dev, verified_stat.st_ino):
                    raise ArtifactBoundaryError(
                        "artifact 写入目标在提交期间被并发替换"
                    )
            self._validate_invocation_usage()
        except BaseException:
            if file_fd >= 0:
                os.close(file_fd)
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            if committed:
                try:
                    os.unlink(relative.name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            raise
        finally:
            os.close(parent_fd)

        resolved_media_type = media_type or mimetypes.guess_type(relative.name)[0]
        return self._register_canonical(
            self._build_reference(
                relative,
                kind=kind,
                media_type=resolved_media_type,
                metadata=metadata,
                size_bytes=size_bytes,
                digest=digest.hexdigest(),
            )
        )

    def remove(self, ref: ArtifactRef) -> None:
        canonical = self._require_reference(ref, expected_kind=None)
        relative = self._relative_from_uri(canonical.uri)
        parent_fd = self._open_parent(relative, create=False)
        try:
            os.unlink(relative.name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        finally:
            os.close(parent_fd)
        self._references.pop(canonical.artifact_id, None)

    def write_json(
        self,
        relative_path: str,
        payload: Any,
        *,
        kind: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return self.write_bytes(
            relative_path,
            data,
            kind=kind,
            media_type="application/json",
            metadata=metadata,
        )

    def write_text(
        self,
        relative_path: str,
        text: str,
        *,
        kind: str,
        media_type: str = "text/plain",
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        return self.write_bytes(
            relative_path,
            text.encode("utf-8"),
            kind=kind,
            media_type=media_type,
            metadata=metadata,
        )

    def write_bytes(
        self,
        relative_path: str,
        data: bytes,
        *,
        kind: str,
        media_type: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        relative = self._validate_relative(relative_path)
        return self.import_stream(
            relative.as_posix(),
            io.BytesIO(data),
            max_bytes=max(len(data), 1),
            kind=kind,
            media_type=media_type,
            metadata=metadata,
        )

    def snapshot_files(self) -> frozenset[str]:
        if self.invocation_id is not None:
            usage = self._scan_invocation_usage()
            scope = self.output_scope
            assert scope is not None
            return frozenset(
                f"{scope}/{relative}"
                for relative in usage.files
                if not relative.startswith(".runtime/")
            )
        return frozenset(
            path.relative_to(self.workspace).as_posix()
            for path in self.workspace.rglob("*")
            if path.is_file() and not self._is_runtime_private(path)
        )

    def publish_new_files(
        self,
        before: frozenset[str],
        *,
        within_output_scope: bool = False,
    ) -> list[ArtifactRef]:
        refs: list[ArtifactRef] = []
        candidates = self.snapshot_files() - before
        if within_output_scope:
            scope = self.output_scope
            if scope is None:
                raise ArtifactBoundaryError("当前 artifact store 没有 invocation scope")
            candidates = frozenset(
                relative
                for relative in candidates
                if relative.startswith(f"{scope}/")
            )
        for relative in sorted(candidates):
            path = self.workspace / relative
            kind, media_type = self._infer_kind(path)
            refs.append(self.publish(path, kind=kind, media_type=media_type))
        return refs

    def _relative_from_path(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        lexical = Path(os.path.abspath(candidate))
        try:
            relative = lexical.relative_to(self.workspace)
        except ValueError as exc:
            raise ArtifactBoundaryError("artifact 路径逃逸 conversation workspace") from exc
        return self._validate_relative(relative.as_posix())

    def _require_publish_scope(self, relative: Path) -> None:
        scope = self.output_scope
        if scope is None:
            return
        scope_path = self._validate_relative(scope)
        try:
            relative.relative_to(scope_path)
        except ValueError as exc:
            raise ArtifactBoundaryError(
                "capability artifact 必须位于当前 invocation output scope"
            ) from exc

    def _scan_invocation_usage(self):
        scope = self.output_scope
        if scope is None:
            raise ArtifactBoundaryError("当前 artifact store 没有 invocation scope")
        try:
            return scan_output_tree(self.workspace / scope, self.output_quota)
        except OutputTreeBoundaryViolation as exc:
            raise ArtifactBoundaryError(
                "invocation output 包含 symlink、特殊文件或不安全目录"
            ) from exc
        except OutputQuotaViolation as exc:
            raise ArtifactSizeLimitError(
                "invocation output 文件数、单文件或总字节数超过上限"
            ) from exc

    def _validate_invocation_usage(self) -> None:
        if self.invocation_id is not None:
            self._scan_invocation_usage()

    def _open_reference(self, ref: ArtifactRef) -> BinaryIO:
        relative = self._relative_from_uri(ref.uri)
        handle = self._open_relative(relative)
        try:
            file_stat = os.fstat(handle.fileno())
            if file_stat.st_size != ref.size_bytes:
                raise ArtifactBoundaryError("artifact size 与引用不一致")
            if self._sha256_handle(handle) != ref.sha256:
                raise ArtifactBoundaryError("artifact sha256 与引用不一致")
            handle.seek(0)
            return handle
        except BaseException:
            handle.close()
            raise

    def _open_relative(self, relative: Path) -> BinaryIO:
        parent_fd = self._open_parent(relative, create=False)
        file_fd = -1
        try:
            file_fd = os.open(
                relative.name,
                self._read_flags(),
                dir_fd=parent_fd,
            )
            file_stat = os.fstat(file_fd)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ArtifactBoundaryError("artifact 必须指向普通文件")
            handle = os.fdopen(file_fd, "rb", closefd=True)
            file_fd = -1
            return handle
        except ArtifactBoundaryError:
            raise
        except OSError as exc:
            raise ArtifactBoundaryError(
                "artifact 路径不存在、逃逸、包含 symlink 或不可安全打开"
            ) from exc
        finally:
            if file_fd >= 0:
                os.close(file_fd)
            os.close(parent_fd)

    def _open_parent(self, relative: Path, *, create: bool) -> int:
        current_fd = -1
        try:
            current_fd = os.open(self.workspace, self._directory_flags())
            for part in relative.parts[:-1]:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=current_fd)
                    except FileExistsError:
                        pass
                next_fd = os.open(
                    part,
                    self._directory_flags(),
                    dir_fd=current_fd,
                )
                os.close(current_fd)
                current_fd = next_fd
            return current_fd
        except OSError as exc:
            if current_fd >= 0:
                os.close(current_fd)
            raise ArtifactBoundaryError(
                "artifact 父目录不存在、逃逸、包含 symlink 或不可安全打开"
            ) from exc

    @staticmethod
    def _directory_flags() -> int:
        return (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )

    @staticmethod
    def _read_flags() -> int:
        return (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )

    @staticmethod
    def _write_flags(*, exclusive: bool) -> int:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        return flags | os.O_EXCL if exclusive else flags

    def _build_reference(
        self,
        relative: Path,
        *,
        kind: str,
        media_type: str | None,
        metadata: Mapping[str, Any] | None,
        size_bytes: int,
        digest: str,
    ) -> ArtifactRef:
        uri = f"workspace://{relative.as_posix()}"
        canonical_metadata = dict(metadata or {})
        return ArtifactRef(
            artifact_id=uuid5(
                _ARTIFACT_NAMESPACE,
                ":".join(
                    (
                        str(self.conversation_id),
                        kind,
                        uri,
                        media_type or "",
                        digest,
                        json.dumps(
                            canonical_metadata,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    )
                ),
            ),
            conversation_id=self.conversation_id,
            kind=kind,
            uri=uri,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=digest,
            metadata=canonical_metadata,
        )

    def _register_canonical(self, ref: ArtifactRef) -> ArtifactRef:
        canonical = ref.model_copy(deep=True)
        existing = self._references.get(canonical.artifact_id)
        if existing is not None and existing != canonical:
            raise ArtifactBoundaryError("artifact id 与已有权威登记冲突")
        self._references[canonical.artifact_id] = canonical
        return canonical.model_copy(deep=True)

    @staticmethod
    def _sha256_handle(handle: BinaryIO) -> str:
        digest = hashlib.sha256()
        handle.seek(0)
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        handle.seek(0)
        return digest.hexdigest()

    @staticmethod
    def _validate_relative(value: str) -> Path:
        pure = PurePosixPath(value)
        if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
            raise ArtifactBoundaryError("artifact 相对路径非法")
        return Path(*pure.parts)

    @classmethod
    def _relative_from_uri(cls, uri: str) -> Path:
        if not uri.startswith("workspace://"):
            raise ArtifactBoundaryError("artifact uri 必须使用 workspace:// scheme")
        return cls._validate_relative(uri[len("workspace://") :])

    def _is_runtime_private(self, path: Path) -> bool:
        relative = path.relative_to(self.workspace)
        if any(part.startswith(".omnicell-python-requests-") for part in relative.parts):
            return True
        parts = relative.parts
        if parts and parts[0] == _INVOCATION_ROOT:
            if ".runtime" in parts[2:]:
                return True
            return not (
                self.invocation_id is not None
                and len(parts) >= 2
                and parts[1] == self.invocation_id
            )
        return False

    @staticmethod
    def _infer_kind(path: Path) -> tuple[str, str | None]:
        suffix = path.suffix.lower()
        if suffix == ".h5ad":
            return "dataset", "application/x-hdf5"
        if suffix == ".json":
            return "json", "application/json"
        if suffix in {".png", ".jpg", ".jpeg", ".svg"}:
            return "image", mimetypes.guess_type(path.name)[0]
        if suffix in {".md", ".txt"}:
            return "text", mimetypes.guess_type(path.name)[0] or "text/plain"
        return "file", mimetypes.guess_type(path.name)[0]


__all__ = [
    "ArtifactBoundaryError",
    "ArtifactSizeLimitError",
    "ConversationArtifactStore",
]
