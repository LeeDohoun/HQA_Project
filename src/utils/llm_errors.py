"""Errors shared by the paid-provider chat models (Luna and Claude)."""


class LLMInputLimitError(ValueError):
    pass


class LLMResponseError(RuntimeError):
    pass
