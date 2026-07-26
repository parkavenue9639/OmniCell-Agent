"""Stable capability-layer error categories for the run coordinator."""


PUBLIC_CAPABILITY_FAILURE_SUMMARY = (
    "能力执行失败；详细诊断仅保留在服务端日志。"
)
PUBLIC_CAPABILITY_NOT_COMPLETED_SUMMARY = (
    "能力调用已结束，但没有达到可作为完成证据的科学终态。"
)


class CapabilityError(RuntimeError):
    pass


class CapabilityInputError(CapabilityError):
    pass


class CapabilityExecutionError(CapabilityError):
    pass


__all__ = [
    "CapabilityError",
    "CapabilityExecutionError",
    "CapabilityInputError",
    "PUBLIC_CAPABILITY_FAILURE_SUMMARY",
    "PUBLIC_CAPABILITY_NOT_COMPLETED_SUMMARY",
]
