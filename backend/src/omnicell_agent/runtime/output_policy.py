"""Untrusted invocation output quotas shared by runtime and artifact acceptance."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


DEFAULT_OUTPUT_MAX_FILES = 512
DEFAULT_OUTPUT_FILE_MAX_BYTES = 512 * 1024 * 1024
DEFAULT_OUTPUT_TOTAL_MAX_BYTES = 1024 * 1024 * 1024


class OutputQuotaViolation(RuntimeError):
    """The invocation output tree cannot be accepted within its hard bounds."""


class OutputTreeBoundaryViolation(OutputQuotaViolation):
    """The output tree contains a symlink, special file, or unsafe directory."""


@dataclass(frozen=True, slots=True)
class OutputQuota:
    max_files: int = DEFAULT_OUTPUT_MAX_FILES
    file_max_bytes: int = DEFAULT_OUTPUT_FILE_MAX_BYTES
    total_max_bytes: int = DEFAULT_OUTPUT_TOTAL_MAX_BYTES

    def __post_init__(self) -> None:
        for name, value in (
            ("max_files", self.max_files),
            ("file_max_bytes", self.file_max_bytes),
            ("total_max_bytes", self.total_max_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} 必须是正整数")
        if self.file_max_bytes > self.total_max_bytes:
            raise ValueError("单文件上限不能超过 invocation 总字节上限")


@dataclass(frozen=True, slots=True)
class OutputUsage:
    files: tuple[str, ...]
    file_count: int
    total_bytes: int


def scan_output_tree(root: str | Path, quota: OutputQuota) -> OutputUsage:
    """Bounded, non-symlink-following scan of one invocation output tree."""

    output_root = Path(root)
    try:
        root_stat = os.lstat(output_root)
    except FileNotFoundError:
        return OutputUsage((), 0, 0)
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        raise OutputTreeBoundaryViolation("invocation output root 必须是普通目录")

    # Directories are bounded separately so a directory-only inode flood cannot
    # make validation unbounded before the file-count limit is reached.
    max_entries = quota.max_files * 4 + 32
    entry_count = 0
    file_count = 0
    total_bytes = 0
    files: list[str] = []
    pending: list[tuple[Path, str]] = [(output_root, "")]
    while pending:
        directory, prefix = pending.pop()
        directory_fd = -1
        try:
            directory_fd = os.open(
                directory,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            entries = os.scandir(directory_fd)
        except OSError as exc:
            raise OutputTreeBoundaryViolation(
                "invocation output 无法安全遍历"
            ) from exc
        try:
            with entries:
                for entry in entries:
                    entry_count += 1
                    if entry_count > max_entries:
                        raise OutputQuotaViolation(
                            "invocation output 条目数超过上限"
                        )
                    relative = f"{prefix}/{entry.name}" if prefix else entry.name
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise OutputTreeBoundaryViolation(
                            "invocation output 条目无法安全读取"
                        ) from exc
                    if stat.S_ISLNK(entry_stat.st_mode):
                        raise OutputTreeBoundaryViolation(
                            "invocation output 禁止 symlink"
                        )
                    if stat.S_ISDIR(entry_stat.st_mode):
                        pending.append((directory / entry.name, relative))
                        continue
                    if not stat.S_ISREG(entry_stat.st_mode):
                        raise OutputTreeBoundaryViolation(
                            "invocation output 只允许普通文件和目录"
                        )
                    file_count += 1
                    if file_count > quota.max_files:
                        raise OutputQuotaViolation(
                            "invocation output 文件数超过上限"
                        )
                    if entry_stat.st_size > quota.file_max_bytes:
                        raise OutputQuotaViolation(
                            "invocation output 单文件超过上限"
                        )
                    total_bytes += entry_stat.st_size
                    if total_bytes > quota.total_max_bytes:
                        raise OutputQuotaViolation(
                            "invocation output 总字节数超过上限"
                        )
                    files.append(relative)
        finally:
            os.close(directory_fd)
    files.sort()
    return OutputUsage(tuple(files), file_count, total_bytes)


__all__ = [
    "DEFAULT_OUTPUT_FILE_MAX_BYTES",
    "DEFAULT_OUTPUT_MAX_FILES",
    "DEFAULT_OUTPUT_TOTAL_MAX_BYTES",
    "OutputQuota",
    "OutputQuotaViolation",
    "OutputTreeBoundaryViolation",
    "OutputUsage",
    "scan_output_tree",
]
