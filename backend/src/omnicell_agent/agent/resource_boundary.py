"""Shared publication guard for internal resource locators."""

from __future__ import annotations

import re


RUNTIME_CONTROL_ROOT = ".omnicell-runtime-control"
INVOCATION_ROOT = ".omnicell-invocations"

_BOUNDARY_PREFIX = r"""(?:^|[\s"'`(\[])"""
_PATH_TOKEN = r"""[^\s"'`<>()\[\]{}]+"""
_INTERNAL_URI = re.compile(r"(?i)\b(?:workspace|file)://")
_POSIX_ABSOLUTE_PATH = re.compile(
    rf"""(?x){_BOUNDARY_PREFIX}/(?!/){_PATH_TOKEN}"""
)
_WINDOWS_DRIVE_PATH = re.compile(
    rf"""(?ix){_BOUNDARY_PREFIX}[a-z]:[\\/]{_PATH_TOKEN}"""
)
_WINDOWS_UNC_PATH = re.compile(
    rf"""(?x){_BOUNDARY_PREFIX}(?:\\\\|//)
    [^\\/\s"'`<>()\[\]{{}}]+[\\/]{_PATH_TOKEN}"""
)
_HOME_RELATIVE_PATH = re.compile(
    rf"""(?x){_BOUNDARY_PREFIX}~[\\/]{_PATH_TOKEN}"""
)
_INTERNAL_PATH_MARKERS = (
    RUNTIME_CONTROL_ROOT,
    INVOCATION_ROOT,
    ".omnicell-python-requests-",
)


def contains_internal_resource_locator(value: str) -> bool:
    """Return whether public text contains a private URI or filesystem path."""

    lowered = value.lower()
    if any(marker in lowered for marker in _INTERNAL_PATH_MARKERS):
        return True
    return any(
        pattern.search(value)
        for pattern in (
            _INTERNAL_URI,
            _POSIX_ABSOLUTE_PATH,
            _WINDOWS_DRIVE_PATH,
            _WINDOWS_UNC_PATH,
            _HOME_RELATIVE_PATH,
        )
    )
