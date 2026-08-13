from app.runtime.context import get_runtime_context


def load_settings() -> dict:
    """Compatibility wrapper for the immutable runtime context."""
    return dict(get_runtime_context().settings)
