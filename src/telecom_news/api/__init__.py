"""Optional HTTP API + minimal GUI (M9a).

Install with ``pip install -e '.[api]'`` (fastapi, uvicorn). The CLI command
``python -m telecom_news serve`` starts the app. There is **no authentication**
in M9a — bind to localhost only.
"""

from __future__ import annotations

__all__ = ["create_app"]


def create_app(*args, **kwargs):  # noqa: ANN001, ANN003
    """Lazy wrapper so importing ``telecom_news.api`` does not require FastAPI."""
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)
