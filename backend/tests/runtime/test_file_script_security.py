from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from omnicell_agent.runtime.local_docker import _FILE_SCRIPT


def _run_file_script(
    operation: str,
    root: Path,
    target: Path,
    *extra: str,
    input_data: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            _FILE_SCRIPT,
            operation,
            str(root),
            str(target),
            *extra,
        ],
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_file_script_uses_dirfd_and_no_follow_for_atomic_path_access() -> None:
    assert "dir_fd=" in _FILE_SCRIPT
    assert "os.O_NOFOLLOW" in _FILE_SCRIPT
    assert "with open(target" not in _FILE_SCRIPT


def test_file_script_rejects_final_and_intermediate_symlink_escape(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_bytes(b"outside-secret")

    (workspace / "read-link").symlink_to(secret)
    read = _run_file_script("read", workspace, workspace / "read-link", "64")
    assert read.returncode != 0
    assert b"outside-secret" not in read.stdout

    (workspace / "dir-link").symlink_to(outside, target_is_directory=True)
    escaped = outside / "escaped.txt"
    write = _run_file_script(
        "write",
        workspace,
        workspace / "dir-link" / "escaped.txt",
        input_data=b"must-not-escape",
    )
    assert write.returncode != 0
    assert not escaped.exists()


def test_file_script_reads_and_writes_regular_workspace_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "nested" / "payload.bin"

    written = _run_file_script(
        "write",
        workspace,
        target,
        input_data=b"payload",
    )
    assert written.returncode == 0, written.stderr.decode(errors="replace")

    read = _run_file_script("read", workspace, target, "64")
    assert read.returncode == 0, read.stderr.decode(errors="replace")
    assert read.stdout == b"payload"
