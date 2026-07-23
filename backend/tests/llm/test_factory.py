from __future__ import annotations

import logging

import pytest

from omnicell_agent.llm import (
    AliasSpec,
    CapabilityMismatchError,
    LLMConfigurationError,
    LLMFactory,
    LLMRole,
    ModelCapabilities,
    OpenAICompatibleProvider,
    UnknownAliasError,
    UnknownProviderError,
)

from .conftest import FakeProvider


def test_roles_are_stable_string_values() -> None:
    assert {role.value for role in LLMRole} == {
        "agent_primary",
        "fast_router",
        "code_generation",
        "annotation",
        "validation",
        "summary",
        "vision",
    }
    assert str(LLMRole.FAST_ROUTER) == "fast_router"


def test_alias_only_configuration_switches_provider_and_model() -> None:
    alpha = FakeProvider("alpha")
    beta = FakeProvider("beta")

    def unchanged_domain_call(factory: LLMFactory):
        return factory.create(LLMRole.ANNOTATION, temperature=0.2)

    first = LLMFactory(
        {"alpha": alpha, "beta": beta},
        {LLMRole.ANNOTATION: AliasSpec("alpha", "model-a")},
    ).validate()
    second = LLMFactory(
        {"alpha": alpha, "beta": beta},
        {LLMRole.ANNOTATION: AliasSpec("beta", "model-b")},
    ).validate()

    assert unchanged_domain_call(first).provider == "alpha"
    assert unchanged_domain_call(second).provider == "beta"
    assert unchanged_domain_call(second).model == "model-b"


def test_call_overrides_win_over_alias_overrides_and_values_are_immutable() -> None:
    provider = FakeProvider("fake")
    alias = AliasSpec(
        "fake",
        "model-a",
        overrides={"temperature": 0.1, "streaming": False},
    )
    factory = LLMFactory({"fake": provider}, {"summary": alias})

    resolved = factory.resolve("summary", temperature=0.7)
    model = factory.create("summary", temperature=0.7)

    assert resolved.options == {"temperature": 0.7, "streaming": False}
    assert model.options == {"temperature": 0.7, "streaming": False}
    with pytest.raises(TypeError):
        resolved.options["temperature"] = 1.0  # type: ignore[index]
    with pytest.raises(TypeError):
        alias.overrides["temperature"] = 1.0  # type: ignore[index]


def test_default_model_is_resolved_and_created_through_alias() -> None:
    provider = FakeProvider("fake", default_model="provider-default")
    factory = LLMFactory(
        {"fake": provider},
        {"default": AliasSpec("fake", "default")},
    ).validate()

    assert factory.resolve("default").model == "provider-default"
    model = factory.create("default", temperature=0.4)
    assert model.model == "provider-default"
    assert model.options == {"temperature": 0.4}


def test_capability_query_support_and_required_capability_validation() -> None:
    available = ModelCapabilities(
        input_modalities=frozenset({"text", "image"}),
        structured_output=True,
        streaming=True,
        tool_calling=False,
    )
    provider = FakeProvider("fake", capabilities=available)
    required = ModelCapabilities(
        input_modalities=frozenset({"image"}),
        structured_output=True,
    )
    factory = LLMFactory(
        {"fake": provider},
        {"vision": AliasSpec("fake", "vision-model", required_capabilities=required)},
    ).validate()

    assert factory.capabilities("vision") is available
    assert factory.supports("vision", required)
    assert not factory.supports(
        "vision", ModelCapabilities(tool_calling=True)
    )

    insufficient = FakeProvider("text", capabilities=ModelCapabilities())
    broken = LLMFactory(
        {"text": insufficient},
        {"vision": AliasSpec("text", "text-only", required_capabilities=required)},
    )
    with pytest.raises(CapabilityMismatchError, match="vision"):
        broken.validate()


def test_static_alias_options_are_validated_before_factory_is_usable() -> None:
    provider = OpenAICompatibleProvider(
        name="gateway",
        api_key="provider-secret",
        base_url="https://example.test/v1",
        default_model="model-a",
    )
    factory = LLMFactory(
        {"gateway": provider},
        {
            "bad": AliasSpec(
                "gateway",
                overrides={"api_key": "alias-marker-secret"},
            )
        },
    )

    with pytest.raises(LLMConfigurationError, match="api_key") as caught:
        factory.validate()
    assert "alias-marker-secret" not in str(caught.value)


def test_dynamic_options_use_the_same_pre_creation_validation() -> None:
    provider = OpenAICompatibleProvider(
        name="gateway",
        api_key="provider-secret",
        base_url="https://example.test/v1",
        default_model="model-a",
    )
    factory = LLMFactory(
        {"gateway": provider},
        {"default": AliasSpec("gateway")},
    ).validate()

    with pytest.raises(LLMConfigurationError, match="extra_headers") as caught:
        factory.resolve(
            "default",
            extra_headers={"Authorization": "call-marker-secret"},
        )
    assert "call-marker-secret" not in str(caught.value)


def test_alias_and_call_options_cannot_use_request_connection_bypasses() -> None:
    provider = OpenAICompatibleProvider(
        name="gateway",
        api_key="provider-secret",
        base_url="https://example.test/v1",
        default_model="model-a",
    )
    unsafe_alias = LLMFactory(
        {"gateway": provider},
        {
            "default": AliasSpec(
                "gateway",
                overrides={"extra_query": {"token": "alias-request-marker-secret"}},
            )
        },
    )
    with pytest.raises(LLMConfigurationError, match="extra_query") as alias_error:
        unsafe_alias.validate()
    assert "alias-request-marker-secret" not in str(alias_error.value)

    safe_factory = LLMFactory(
        {"gateway": provider},
        {"default": AliasSpec("gateway")},
    ).validate()
    with pytest.raises(LLMConfigurationError, match="model_kwargs") as call_error:
        safe_factory.resolve(
            "default",
            model_kwargs={
                "extra_headers": {"Authorization": "call-request-marker-secret"}
            },
        )
    assert "call-request-marker-secret" not in str(call_error.value)


def test_factory_rejects_noncanonical_alias_and_call_option_names() -> None:
    provider = OpenAICompatibleProvider(
        name="gateway",
        api_key="provider-secret",
        base_url="https://example.test/v1",
        default_model="model-a",
    )
    unsafe_alias = LLMFactory(
        {"gateway": provider},
        {"default": AliasSpec("gateway", overrides={"TEMPERATURE": 0.2})},
    )
    with pytest.raises(LLMConfigurationError, match="非规范"):
        unsafe_alias.validate()

    safe_factory = LLMFactory(
        {"gateway": provider},
        {"default": AliasSpec("gateway")},
    ).validate()
    with pytest.raises(LLMConfigurationError, match="非规范"):
        safe_factory.resolve("default", TEMPERATURE=0.2)


@pytest.mark.parametrize("alias", ["", "   "])
def test_empty_alias_fails_fast(alias: str) -> None:
    factory = LLMFactory(
        {"fake": FakeProvider("fake")},
        {"default": AliasSpec("fake")},
    )
    with pytest.raises(UnknownAliasError, match="不能为空"):
        factory.resolve(alias)


def test_unknown_alias_provider_and_mapping_name_fail_fast() -> None:
    provider = FakeProvider("actual")
    with pytest.raises(LLMConfigurationError, match="不一致"):
        LLMFactory(
            {"configured": provider},
            {"default": AliasSpec("configured")},
        ).validate()

    valid = LLMFactory(
        {"actual": provider},
        {"default": AliasSpec("actual")},
    )
    with pytest.raises(UnknownAliasError, match="missing"):
        valid.resolve("missing")

    dangling = LLMFactory(
        {"actual": provider},
        {"default": AliasSpec("missing")},
    )
    with pytest.raises(UnknownProviderError, match="missing"):
        dangling.validate()


def test_safe_diagnostics_and_creation_log_exclude_override_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "do-not-log-this"
    provider = FakeProvider("fake")
    factory = LLMFactory(
        {"fake": provider},
        {"default": AliasSpec("fake", overrides={"opaque": secret})},
    ).validate()

    caplog.set_level(logging.INFO, logger="omnicell_agent.llm.factory")
    resolved = factory.resolve("default", prompt=secret)
    factory.create("default", prompt=secret)

    rendered = " ".join(
        [repr(factory), repr(factory.safe_info()), repr(resolved), caplog.text]
    )
    assert secret not in rendered
    assert "fake" in rendered
    assert "model-default" in rendered
    assert "capabilities" in rendered
