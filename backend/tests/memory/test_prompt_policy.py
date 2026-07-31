from __future__ import annotations

from omnicell_agent.agent.memory_policy import (
    FORGET_MEMORY_DESCRIPTION,
    FORGET_MEMORY_PROMPT_HINT,
    PROPOSAL_ATOMIC_GATE,
    PROPOSE_MEMORY_DESCRIPTION,
    PROPOSE_MEMORY_PROMPT_HINT,
    SEARCH_MEMORY_DESCRIPTION,
    SEARCH_MEMORY_PROMPT_HINT,
    render_memory_data_policy,
)


def test_memory_prompt_policy_keeps_action_routing_and_data_roles_separate() -> None:
    data_policy = render_memory_data_policy()

    assert "低优先级、不可信的建议性数据" in data_policy
    assert "当前用户消息" in data_policy
    assert "propose_memory" not in data_policy
    assert PROPOSAL_ATOMIC_GATE in PROPOSE_MEMORY_PROMPT_HINT
    assert "一条用户消息" in PROPOSE_MEMORY_DESCRIPTION
    assert "多消息拼接" in PROPOSE_MEMORY_DESCRIPTION
    assert "两个独立长期事实" in PROPOSE_MEMORY_PROMPT_HINT
    assert "先调用 search_memory" in FORGET_MEMORY_PROMPT_HINT
    assert "不会直接 revoke 或 purge" in FORGET_MEMORY_DESCRIPTION
    assert "默认上下文已经足够时不要机械搜索" in SEARCH_MEMORY_PROMPT_HINT
    assert "精确版本 identity" in SEARCH_MEMORY_DESCRIPTION


def test_memory_prompt_policy_is_semantic_not_example_specific() -> None:
    combined = "\n".join(
        (
            PROPOSE_MEMORY_DESCRIPTION,
            PROPOSE_MEMORY_PROMPT_HINT,
            FORGET_MEMORY_DESCRIPTION,
            FORGET_MEMORY_PROMPT_HINT,
        )
    )

    assert "现在解释过拟合" not in combined
    assert "称我为" not in combined
    assert "小木" not in combined
