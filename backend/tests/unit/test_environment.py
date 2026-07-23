from __future__ import annotations

import os

from omnicell_agent.core.environment import load_project_environment


def test_project_environment_loads_dotenv_without_overriding_process_env(
    tmp_path,
    monkeypatch,
) -> None:
    (tmp_path / ".env").write_text(
        "OMNICELL_FROM_FILE=file-value\nOMNICELL_EXPLICIT=file-value\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OMNICELL_FROM_FILE", raising=False)
    monkeypatch.setenv("OMNICELL_EXPLICIT", "process-value")

    loaded = load_project_environment()

    assert loaded == tmp_path / ".env"
    assert os.environ["OMNICELL_FROM_FILE"] == "file-value"
    assert os.environ["OMNICELL_EXPLICIT"] == "process-value"


def test_project_environment_is_optional(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert load_project_environment() is None
