from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from omnicell_agent.runtime import (
    InvalidRuntimeProfileError,
    PullPolicy,
    RuntimePathError,
    RuntimeProfile,
    WorkspacePathResolver,
)


def test_profile_is_immutable_and_metadata_hides_env_value() -> None:
    profile = RuntimeProfile(name="worker", image="repo/image:tag", env={"SAFE_VALUE": "do-not-print"})

    with pytest.raises(FrozenInstanceError):
        profile.image = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        profile.env["NEW"] = "value"  # type: ignore[index]

    rendered = json.dumps(profile.safe_metadata())
    assert "SAFE_VALUE" in rendered
    assert "do-not-print" not in rendered
    assert profile.pull_policy is PullPolicy.IF_NOT_PRESENT


@pytest.mark.parametrize(
    "key",
    [
        "API_KEY",
        "AUTHORIZATION",
        "BEARER",
        "COOKIE",
        "DATABASE_PASSWORD",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "CLIENT_CREDENTIAL",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "DATABASE_URL",
        "SESSION_TOKEN",
        "SSH_KEY",
    ],
)
def test_profile_rejects_secret_like_env_keys(key: str) -> None:
    with pytest.raises(InvalidRuntimeProfileError, match="secret"):
        RuntimeProfile(name="worker", image="image", env={key: "sensitive"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workspace_root", "/"),
        ("workspace_root", "relative"),
        ("network", "host"),
        ("memory_bytes", 0),
        ("cpus", -1),
        ("pids_limit", 0),
        ("stdout_max_bytes", 0),
        ("stderr_max_bytes", -1),
        ("command_max_bytes", 0),
        ("write_max_bytes", 0),
        ("read_max_bytes", 0),
        ("output_max_files", 0),
        ("output_file_max_bytes", 0),
        ("output_total_max_bytes", 0),
        ("tmpfs", ("/app/data:rw",)),
    ],
)
def test_profile_rejects_invalid_boundaries(field: str, value: object) -> None:
    with pytest.raises(InvalidRuntimeProfileError):
        RuntimeProfile(name="worker", image="image", **{field: value})


def test_profile_rejects_output_file_limit_above_total_limit() -> None:
    with pytest.raises(InvalidRuntimeProfileError, match="不能超过"):
        RuntimeProfile(
            name="worker",
            image="image",
            output_file_max_bytes=2,
            output_total_max_bytes=1,
        )


def test_profile_requires_explicit_shell_command_classification() -> None:
    with pytest.raises(InvalidRuntimeProfileError, match="显式分类"):
        RuntimeProfile(
            name="ambiguous-shell",
            image="image",
            allowed_commands=("python", "dash"),
        )

    profile = RuntimeProfile(
        name="classified-shell",
        image="image",
        allowed_commands=("python", "dash"),
        shell_commands=("dash",),
    )
    assert profile.shell_commands == ("dash",)


def test_path_resolver_accepts_logical_and_in_root_absolute_paths() -> None:
    resolver = WorkspacePathResolver("/app/data")

    assert resolver.resolve("results/a.csv") == "/app/data/results/a.csv"
    assert resolver.resolve("/app/data/results") == "/app/data/results"
    assert resolver.relative("/app/data/results/a.csv") == "results/a.csv"


@pytest.mark.parametrize("path", ["../etc/passwd", "results/../../etc", "/etc/passwd"])
def test_path_resolver_rejects_escape(path: str) -> None:
    with pytest.raises(RuntimePathError):
        WorkspacePathResolver("/app/data").resolve(path)


@pytest.mark.parametrize("pattern", ["", "/tmp/*", "../*", "results/../../*"])
def test_glob_rejects_escape(pattern: str) -> None:
    with pytest.raises(RuntimePathError):
        WorkspacePathResolver.validate_glob(pattern)
