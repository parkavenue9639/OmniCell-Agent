"""Stable, body-free errors for the cross-conversation Memory Plane."""

from __future__ import annotations


class MemoryError(RuntimeError):
    """Base error safe to project into a public error envelope.

    Error messages deliberately never interpolate memory content, provider
    responses, host paths, or arbitrary persistence exceptions.
    """

    error_code = "memory_error"
    retryable = False
    recovery_hint = "刷新记忆状态后重试；若问题持续，请保持记忆关闭。"
    default_summary = "跨会话记忆操作失败。"

    def __init__(self, summary: str | None = None) -> None:
        self.summary = summary or self.default_summary
        super().__init__(self.summary)


class MemoryNotFoundError(MemoryError, LookupError):
    error_code = "memory_not_found"
    recovery_hint = "刷新记忆列表，并选择仍然存在的记忆。"
    default_summary = "指定的记忆或版本不存在。"


class MemoryConflictError(MemoryError):
    error_code = "memory_version_conflict"
    retryable = True
    recovery_hint = "刷新记忆详情，基于最新版本重新提交。"
    default_summary = "记忆已被其他操作更新。"


class MemoryProposalLimitError(MemoryError):
    error_code = "memory_proposal_limit_reached"
    retryable = False
    recovery_hint = "本 Run 不再创建候选；继续或结束当前请求。"
    default_summary = "当前 Run 已经创建过一条记忆候选。"


class MemoryStateError(MemoryError):
    error_code = "memory_state_invalid"
    recovery_hint = "刷新记忆状态，并选择当前状态允许的操作。"
    default_summary = "当前记忆状态不允许该操作。"


class MemoryContentRejectedError(MemoryError, ValueError):
    error_code = "memory_content_rejected"
    recovery_hint = "移除敏感、可识别或原始执行内容后重新提交。"
    default_summary = "记忆内容不符合服务端安全边界。"


class MemoryDisabledError(MemoryError):
    error_code = "memory_disabled"
    recovery_hint = "在记忆设置中明确开启对应能力后重试。"
    default_summary = "跨会话记忆能力当前未开启。"


class MemoryProviderConsentRequiredError(MemoryError):
    error_code = "memory_provider_consent_required"
    recovery_hint = "确认披露说明并持久化 provider consent 后重试。"
    default_summary = "发送记忆正文前需要明确的 provider consent。"


class MemorySuppressedError(MemoryError):
    error_code = "memory_suppressed"
    recovery_hint = "不要从旧会话重新生成已清除的内容。"
    default_summary = "该内容已被遗忘策略抑制，不能重新写入。"


class MemorySelectionInvalidError(MemoryError):
    error_code = "memory_selection_invalid"
    recovery_hint = "刷新记忆列表，并重新选择精确且仍有效的版本。"
    default_summary = "显式选择的记忆身份已失效。"


class MemoryContextLimitError(MemoryError):
    error_code = "memory_context_limit_exceeded"
    recovery_hint = "减少显式选择的记忆数量或缩短记忆正文。"
    default_summary = "选择的记忆超过本次模型上下文上限。"


class MemorySnapshotConflictError(MemoryError):
    error_code = "memory_snapshot_conflict"
    recovery_hint = "使用原 run 请求恢复，或新建 run 使用新的记忆选择。"
    default_summary = "当前 run 已冻结不同的记忆快照。"


class MemoryAttemptFenceError(MemoryError):
    error_code = "memory_attempt_fence_lost"
    retryable = False
    recovery_hint = "停止当前旧 attempt；由 Run coordinator 的当前 owner 恢复。"
    default_summary = "当前执行已失去写入记忆快照的 attempt fence。"


class MemorySourceInvalidError(MemoryError):
    error_code = "memory_source_invalid"
    recovery_hint = "仅引用当前 run 中仍存在的 message identity。"
    default_summary = "记忆来源身份无效。"


__all__ = [
    "MemoryAttemptFenceError",
    "MemoryConflictError",
    "MemoryContentRejectedError",
    "MemoryContextLimitError",
    "MemoryDisabledError",
    "MemoryError",
    "MemoryNotFoundError",
    "MemoryProviderConsentRequiredError",
    "MemoryProposalLimitError",
    "MemorySelectionInvalidError",
    "MemorySnapshotConflictError",
    "MemorySourceInvalidError",
    "MemoryStateError",
    "MemorySuppressedError",
]
