"""Editable AI relevance rules for the control panel (M9c).

Persists operator overrides under ``data/ai_rules.json``. Empty / missing file
means built-in defaults from :mod:`telecom_news.processors.relevance` stay in
force. The next ``process`` / ``run`` cycle picks up changes (no serve restart
required for in-process runs; external cron processes re-import on start).
"""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import load_config
from .processors.relevance import (
    DEFAULT_MESSAGING_TERMS,
    DEFAULT_OFF_TOPIC_TERMS,
    DEFAULT_RELEVANCE_SYSTEM_PROMPT,
)

_LOCK = threading.RLock()
_CACHE: dict[str, AiRules] = {}


@dataclass
class AiRules:
    """Operator-editable relevance policy."""

    system_prompt: str = DEFAULT_RELEVANCE_SYSTEM_PROMPT
    messaging_terms: list[str] = field(default_factory=lambda: list(DEFAULT_MESSAGING_TERMS))
    off_topic_terms: list[str] = field(default_factory=lambda: list(DEFAULT_OFF_TOPIC_TERMS))
    # When True, process always calls the LLM (D-012 style) regardless of source gate.
    force_llm_gate: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def defaults(cls) -> AiRules:
        return cls()

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AiRules:
        base = cls.defaults()
        if not data:
            return base
        prompt = data.get("system_prompt")
        if isinstance(prompt, str) and prompt.strip():
            base.system_prompt = prompt.strip()
        for key, attr in (
            ("messaging_terms", "messaging_terms"),
            ("off_topic_terms", "off_topic_terms"),
        ):
            raw = data.get(key)
            if isinstance(raw, list):
                cleaned = [str(item).strip() for item in raw if str(item).strip()]
                if cleaned:
                    setattr(base, attr, cleaned)
            elif isinstance(raw, str) and raw.strip():
                cleaned = [
                    line.strip() for line in raw.replace(",", "\n").splitlines() if line.strip()
                ]
                if cleaned:
                    setattr(base, attr, cleaned)
        if "force_llm_gate" in data:
            base.force_llm_gate = bool(data["force_llm_gate"])
        if isinstance(data.get("notes"), str):
            base.notes = data["notes"]
        return base


def rules_path(data_dir: Path | None = None) -> Path:
    root = Path(data_dir) if data_dir is not None else load_config().data_dir
    return root / "ai_rules.json"


def _cache_key(data_dir: Path | None) -> str:
    return str(rules_path(data_dir).resolve())


def invalidate_cache(data_dir: Path | None = None) -> None:
    with _LOCK:
        if data_dir is None:
            _CACHE.clear()
        else:
            _CACHE.pop(_cache_key(data_dir), None)


def load_rules(data_dir: Path | None = None) -> AiRules:
    """Load rules from disk (or defaults). Result is cached per path."""
    path = rules_path(data_dir)
    key = str(path.resolve())
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None and not path.is_file():
            # File removed → drop cache back to defaults next read.
            _CACHE.pop(key, None)
            cached = None
        if cached is not None:
            return deepcopy(cached)
        if not path.is_file():
            rules = AiRules.defaults()
            _CACHE[key] = deepcopy(rules)
            return rules
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            rules = AiRules.defaults()
            _CACHE[key] = deepcopy(rules)
            return rules
        if not isinstance(raw, dict):
            rules = AiRules.defaults()
        else:
            rules = AiRules.from_dict(raw)
        _CACHE[key] = deepcopy(rules)
        return deepcopy(rules)


def save_rules(rules: AiRules | dict[str, Any], data_dir: Path | None = None) -> AiRules:
    """Persist rules and refresh cache. Creates parent dirs as needed."""
    if isinstance(rules, dict):
        parsed = AiRules.from_dict(rules)
    else:
        parsed = rules
    # Re-validate via from_dict so empty lists fall back to defaults intentionally
    # only when keys omitted; explicit empty list means "use defaults".
    path = rules_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = parsed.to_dict()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with _LOCK:
        _CACHE[str(path.resolve())] = deepcopy(parsed)
    return deepcopy(parsed)


def reset_rules(data_dir: Path | None = None) -> AiRules:
    """Delete override file and return built-in defaults."""
    path = rules_path(data_dir)
    if path.is_file():
        path.unlink()
    invalidate_cache(data_dir)
    return load_rules(data_dir)


def rules_for_api(data_dir: Path | None = None) -> dict[str, Any]:
    """API payload with defaults side-by-side for the editor."""
    current = load_rules(data_dir)
    defaults = AiRules.defaults()
    path = rules_path(data_dir)
    return {
        "rules": current.to_dict(),
        "defaults": defaults.to_dict(),
        "path": str(path),
        "overridden": path.is_file(),
    }
