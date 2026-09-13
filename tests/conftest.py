"""Test isolation: never read the operator's live data directory or environment.

The suite used to run against the real ``data/`` folder, so whatever the control
panel had last written decided whether tests passed. That bit twice on
2026-09-13: a hand-edited ``ai_rules.json`` failed the relevance tests, and a
language chosen in the panel failed seven configuration tests. Neither was a
defect in the code under test.

Every test therefore gets its own data directory and a clean environment: the
project's own variables are removed, so a test that asserts a default really
tests the default, and one that needs a value sets it itself. That is also what
made ``git push`` need ``env -u TELECOM_NEWS_DISABLED_SOURCES`` — running the
suite in a shell that had sourced the operator's env file failed eleven tests
that had nothing wrong with them.
"""

from __future__ import annotations

import os

import pytest

from telecom_news import config as config_mod

# Everything `config.Config` and the CLI read from the environment. Prefixes
# cover the project's own namespaces; the rest are named one by one because they
# are generic words that another program could legitimately own.
_ENV_PREFIXES: tuple[str, ...] = ("TELECOM_NEWS_", "TELEGRAM_", "LMSTUDIO_")
_ENV_NAMES: tuple[str, ...] = (
    "LOG_LEVEL",
    "SUBSCRIBER_MAX_AGE_HOURS",
    "SUBSCRIBER_MAX_PER_CYCLE",
    "SUBSCRIBER_MAX_ATTEMPTS",
    "PUBLISH_MAX_PER_CYCLE",
    "PUBLISH_MAX_AGE_HOURS",
)


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    """Point the data directory at a fresh folder and drop operator env overrides."""
    for name in list(os.environ):
        if name.startswith(_ENV_PREFIXES) or name in _ENV_NAMES:
            monkeypatch.delenv(name, raising=False)
    # The folder is still called "data" so that tests asserting the shape of the
    # default paths keep describing something true. It is deliberately not
    # created: the code makes it when it first writes, and a test that creates it
    # itself must not trip over a directory the fixture already made.
    data_dir = tmp_path / "data"
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(data_dir))

    # The registry is process-global and the env var was applied at import time.
    config_mod.reset_sources_to_baseline()
    try:
        yield data_dir
    finally:
        # A test may have left an intentionally invalid kill switch behind, and
        # rebuilding the registry replays it. Clear it first so the cleanup of
        # one test cannot fail the next.
        os.environ.pop("TELECOM_NEWS_DISABLED_SOURCES", None)
        config_mod.reset_sources_to_baseline()
