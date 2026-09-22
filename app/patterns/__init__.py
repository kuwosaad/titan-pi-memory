"""Durable pattern layer for Titan memory."""

__all__ = [
    "Pattern",
    "PatternApplication",
    "PatternEvidence",
    "PatternMiningRun",
    "PatternProcessingLedger",
    "PatternStore",
    "PatternMemory",
    "PatternError",
    "PatternDisabled",
    "PatternNotFound",
    "PatternValidation",
    "PatternStorageUnavailable",
]


def __getattr__(name: str):
    if name in {"Pattern", "PatternApplication", "PatternEvidence", "PatternMiningRun"}:
        from . import models

        value = getattr(models, name)
    elif name == "PatternStore":
        from .store import PatternStore

        value = PatternStore
    elif name == "PatternProcessingLedger":
        from .processing import PatternProcessingLedger

        value = PatternProcessingLedger
    elif name == "PatternMemory":
        from .memory import PatternMemory

        value = PatternMemory
    elif name in {
        "PatternDisabled",
        "PatternError",
        "PatternNotFound",
        "PatternStorageUnavailable",
        "PatternValidation",
    }:
        from . import errors

        value = getattr(errors, name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
