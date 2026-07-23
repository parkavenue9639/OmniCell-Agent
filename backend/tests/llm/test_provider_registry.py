from __future__ import annotations

import pytest
from langchain_openai import ChatOpenAI

from omnicell_agent.llm import (
    LLMConfigurationError,
    ModelCapabilities,
    ModelNotAllowedError,
    OpenAICompatibleProvider,
    ProviderRegistry,
    UnknownProviderTypeError,
    build_default_provider_registry,
)

from .conftest import FakeProvider
from omnicell_agent.llm.providers import _ALLOWED_OPTIONS, _PROTECTED_OPTIONS


def test_registry_constructs_registered_provider_and_rejects_unknown_or_duplicate() -> None:
    registry = ProviderRegistry()
    registry.register("fake", lambda **config: FakeProvider(**config))

    provider = registry.create("FAKE", name="instance")
    assert provider.name == "instance"
    assert registry.registered_types == ("fake",)

    with pytest.raises(LLMConfigurationError, match="已注册"):
        registry.register("fake", lambda: FakeProvider("other"))
    with pytest.raises(UnknownProviderTypeError, match="unknown"):
        registry.create("unknown")


def test_default_registry_exposes_openai_compatible_type() -> None:
    assert build_default_provider_registry().registered_types == ("openai_compatible",)


def test_openai_compatible_provider_is_offline_safe_and_redacts_credentials() -> None:
    secret = "sk-private-value"
    header_secret = "header-private-value"
    provider = OpenAICompatibleProvider(
        name="gateway",
        api_key=secret,
        base_url="https://user:password@example.test/v1?token=hidden",
        default_model="vendor/model-a",
        default_headers={"Authorization": header_secret, "X-Title": "OmniCell"},
        model_allowlist={"vendor/model-a", "vendor/model-b"},
        model_capabilities={
            "vendor/model-b": ModelCapabilities(
                input_modalities=frozenset({"text", "image"}),
                structured_output=True,
                streaming=True,
                tool_calling=True,
            )
        },
    )

    model = provider.create_model("default", temperature=0.2)
    assert model.model_name == "vendor/model-a"
    assert provider.capabilities_for("vendor/model-b").input_modalities == {
        "text",
        "image",
    }

    rendered = repr(provider) + repr(provider.safe_info()) + repr(model)
    assert secret not in rendered
    assert header_secret not in rendered
    assert "password" not in rendered
    assert "token=hidden" not in rendered
    assert "https://example.test/v1" in rendered
    assert provider.safe_info()["header_names"] == ["Authorization", "X-Title"]


@pytest.mark.parametrize(
    ("option", "payload"),
    [
        (
            "extra_body",
            {"provider": {"sort": "latency"}, "opaque": "repr-marker-secret"},
        ),
        ("metadata", {"opaque": "repr-marker-secret"}),
    ],
)
def test_openai_model_repr_never_displays_free_form_option_values(
    option: str,
    payload: dict[str, str],
) -> None:
    provider = OpenAICompatibleProvider(
        name="gateway",
        api_key="api-marker-secret",
        base_url="https://user:password@example.test/v1?token=query-marker-secret",
        default_model="vendor/model-a",
    )

    model = provider.create_model("default", **{option: payload})
    rendered = repr(model)

    assert "repr-marker-secret" not in rendered
    assert "api-marker-secret" not in rendered
    assert "password" not in rendered
    assert "query-marker-secret" not in rendered
    assert "vendor/model-a" in rendered


def test_openai_option_policy_covers_current_constructor_and_request_fields() -> None:
    audited = _ALLOWED_OPTIONS | _PROTECTED_OPTIONS
    assert set(ChatOpenAI.model_fields).issubset(audited)
    assert _ALLOWED_OPTIONS - set(ChatOpenAI.model_fields) == {
        "response_format",
        "timeout",
    }
    assert {
        "extra_headers",
        "extra_query",
        "model_kwargs",
        "openai_proxy",
    }.issubset(_PROTECTED_OPTIONS)


@pytest.mark.parametrize(
    "options",
    [
        {"openai_proxy": "http://proxy.test:8080"},
        {"extra_query": {"token": "request-marker-secret"}},
        {
            "model_kwargs": {
                "extra_headers": {"Authorization": "request-marker-secret"}
            }
        },
        {
            "extra_body": {
                "transport": {
                    "extra_headers": {"Authorization": "request-marker-secret"}
                }
            }
        },
    ],
)
def test_openai_provider_rejects_connection_fields_and_nested_bypasses(
    options: dict[str, object],
) -> None:
    provider = OpenAICompatibleProvider(
        name="gateway",
        api_key="test-key",
        base_url="https://example.test/v1",
        default_model="model",
    )

    with pytest.raises(LLMConfigurationError) as caught:
        provider.create_model("default", **options)
    assert "request-marker-secret" not in str(caught.value)


def test_openai_provider_rejects_unreviewed_top_level_options() -> None:
    provider = OpenAICompatibleProvider(
        name="gateway",
        api_key="test-key",
        base_url="https://example.test/v1",
        default_model="model",
    )
    with pytest.raises(LLMConfigurationError, match="未审核"):
        provider.create_model("default", future_connection_knob="unsafe")


def test_openai_provider_requires_exact_option_names_without_echoing_them() -> None:
    marker = "NONCANONICAL-MARKER-SECRET"
    with pytest.raises(LLMConfigurationError, match="非规范") as caught:
        OpenAICompatibleProvider(
            name="gateway",
            api_key="test-key",
            base_url="https://example.test/v1",
            default_model="model",
            default_options={marker: 0.2},
        )
    assert marker not in str(caught.value)

    provider = OpenAICompatibleProvider(
        name="gateway",
        api_key="test-key",
        base_url="https://example.test/v1",
        default_model="model",
    )
    with pytest.raises(LLMConfigurationError, match="非规范"):
        provider.create_model("default", TEMPERATURE=0.2)


def test_openai_provider_allowlist_and_protected_options_fail_fast() -> None:
    provider = OpenAICompatibleProvider(
        name="gateway",
        api_key="test-key",
        base_url="https://example.test/v1",
        default_model="allowed",
        model_allowlist={"allowed"},
    )
    with pytest.raises(ModelNotAllowedError, match="blocked"):
        provider.create_model("blocked")
    with pytest.raises(LLMConfigurationError, match="api_key"):
        provider.create_model("allowed", api_key="override")
    with pytest.raises(LLMConfigurationError, match="default_query"):
        provider.create_model("allowed", default_query={"token": "override"})
    with pytest.raises(LLMConfigurationError, match="extra_headers") as caught:
        provider.create_model(
            "allowed",
            extra_headers={"Authorization": "header-marker-secret"},
        )
    assert "header-marker-secret" not in str(caught.value)

    with pytest.raises(LLMConfigurationError, match="缺少 API key"):
        OpenAICompatibleProvider(
            name="missing-key",
            api_key="",
            base_url="https://example.test/v1",
            default_model="model",
        )


@pytest.mark.parametrize(
    "default_options",
    [
        {"default_query": {"token": "default-option-marker-secret"}},
        {"openai_proxy": "http://default-option-marker-secret.test"},
    ],
)
def test_openai_provider_rejects_protected_default_options_at_startup(
    default_options: dict[str, object],
) -> None:
    with pytest.raises(LLMConfigurationError) as caught:
        OpenAICompatibleProvider(
            name="unsafe-default",
            api_key="test-key",
            base_url="https://example.test/v1",
            default_model="model",
            default_options=default_options,
        )
    assert "default-option-marker-secret" not in str(caught.value)


@pytest.mark.parametrize(
    "base_url",
    [
        "example.test/v1",
        "ftp://example.test/v1",
        "https://example.test:not-a-port/v1",
    ],
)
def test_openai_provider_rejects_invalid_base_url(base_url: str) -> None:
    with pytest.raises(LLMConfigurationError, match="base URL"):
        OpenAICompatibleProvider(
            name="invalid-url",
            api_key="test-key",
            base_url=base_url,
            default_model="model",
        )
