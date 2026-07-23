"""Stable capability-layer error categories for the run coordinator."""


PUBLIC_CAPABILITY_FAILURE_SUMMARY = (
    "能力执行失败；详细诊断仅保留在服务端日志。"
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
]
