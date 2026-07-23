from __future__ import annotations

import os

import pytest

from omnicell_agent.llm import (
    AliasSpec,
    CapabilityMismatchError,
    LLMConfigurationError,
    LLMFactory,
    LLMRole,
    UnknownProviderTypeError,
    build_factory_from_env,
    configure_default_factory,
    get_default_factory,
    get_llm_by_alias,
)
from omnicell_agent.llm import facade

from .conftest import FakeProvider


def test_env_bootstrap_supports_default_and_first_slash_model_parsing() -> None:
    factory = build_factory_from_env(
        {
            "ONEROUTER_API_KEY": "one-secret",
            "OPENROUTER_API_KEY": "open-secret",
            "OMNICELL_LLM_DEFAULT": "onerouter/default",
            "OMNICELL_LLM_CODE_GENERATION": "openrouter/openai/gpt-4.1",
            "DEFAULT_ONEROUTER_MODEL": "gemini-custom",
            "DEFAULT_OPENROUTER_MODEL": "openai/fallback",
        }
    )

    assert factory.resolve("default").provider == "onerouter"
    assert factory.resolve("default").model == "gemini-custom"
    code = factory.resolve(LLMRole.CODE_GENERATION)
    assert code.provider == "openrouter"
    assert code.model == "openai/gpt-4.1"
    assert factory.resolve(LLMRole.SUMMARY).provider == "onerouter"
    assert factory.capabilities(LLMRole.VISION).input_modalities == {"text", "image"}
    assert set(factory.aliases) == {"default", *(role.value for role in LLMRole)}


def test_env_bootstrap_only_requires_referenced_provider_credentials() -> None:
    factory = build_factory_from_env(
        {
            "OPENROUTER_API_KEY": "open-secret",
            "OMNICELL_LLM_DEFAULT": "openrouter/default",
        }
    )
    assert set(factory.providers) == {"openrouter"}
    assert factory.resolve(LLMRole.ANNOTATION).model == "openai/gpt-4o-mini"


def test_default_bootstrap_loads_dotenv_from_working_tree(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in (
        "ONEROUTER_API_KEY",
        "OMNICELL_LLM_DEFAULT",
        "DEFAULT_ONEROUTER_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        "ONEROUTER_API_KEY=dotenv-secret\n"
        "OMNICELL_LLM_DEFAULT=onerouter/default\n"
        "DEFAULT_ONEROUTER_MODEL=dotenv-model\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    factory = build_factory_from_env()

    assert factory.resolve(LLMRole.AGENT_PRIMARY).model == "dotenv-model"
    assert "dotenv-secret" not in repr(factory)
    assert os.environ["ONEROUTER_API_KEY"] == "dotenv-secret"


@pytest.mark.parametrize(
    ("environ", "error_type", "message"),
    [
        ({}, LLMConfigurationError, "ONEROUTER_API_KEY"),
        (
            {"OMNICELL_LLM_DEFAULT": "unknown/model", "UNKNOWN_API_KEY": "x"},
            UnknownProviderTypeError,
            "unknown",
        ),
        (
            {"OMNICELL_LLM_DEFAULT": "onerouter", "ONEROUTER_API_KEY": "x"},
            LLMConfigurationError,
            "provider/model",
        ),
        (
            {"OMNICELL_LLM_DEFAULT": "", "ONEROUTER_API_KEY": "x"},
            LLMConfigurationError,
            "OMNICELL_LLM_DEFAULT",
        ),
        (
            {
                "OMNICELL_LLM_DEFAULT": "onerouter/default",
                "OMNICELL_LLM_VISION": "broken",
                "ONEROUTER_API_KEY": "x",
            },
            LLMConfigurationError,
            "VISION",
        ),
    ],
)
def test_env_bootstrap_errors_are_diagnostic_without_secret(
    environ: dict[str, str], error_type: type[Exception], message: str
) -> None:
    with pytest.raises(error_type) as caught:
        build_factory_from_env(environ)
    assert message in str(caught.value)
    assert "one-secret" not in str(caught.value)


def test_default_facade_builds_lazily_once(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeProvider("fake")
    expected = LLMFactory(
        {"fake": provider},
        {"default": AliasSpec("fake")},
    )
    builds = 0

    def build() -> LLMFactory:
        nonlocal builds
        builds += 1
        return expected

    monkeypatch.setattr(facade, "build_factory_from_env", build)
    assert builds == 0
    assert get_default_factory() is expected
    assert get_default_factory() is expected
    assert builds == 1


def test_empty_role_target_falls_back_to_default_target() -> None:
    factory = build_factory_from_env(
        {
            "ONEROUTER_API_KEY": "one-secret",
            "OMNICELL_LLM_DEFAULT": "onerouter/default",
            "OMNICELL_LLM_SUMMARY": "",
        }
    )

    assert factory.resolve(LLMRole.SUMMARY).provider == "onerouter"


def test_vision_role_fails_when_declared_modalities_are_text_only() -> None:
    with pytest.raises(CapabilityMismatchError, match="vision"):
        build_factory_from_env(
            {
                "ONEROUTER_API_KEY": "one-secret",
                "OMNICELL_LLM_DEFAULT": "onerouter/default",
                "OMNICELL_LLM_VISION_INPUT_MODALITIES": "text",
            }
        )


def test_configured_facade_delegates_alias_without_environment() -> None:
    provider = FakeProvider("fake")
    factory = LLMFactory(
        {"fake": provider},
        {LLMRole.SUMMARY: AliasSpec("fake", "summary-model")},
    )
    configure_default_factory(factory)

    model = get_llm_by_alias(LLMRole.SUMMARY, temperature=0.8)
    assert model.provider == "fake"
    assert model.model == "summary-model"
    assert model.options == {"temperature": 0.8}
