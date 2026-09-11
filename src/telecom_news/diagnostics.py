"""Publication diagnostics (M7+): why did the pipeline publish nothing?

Read-only analysis over four evidence sources:

1. the SQLite store (article counters, the last publication, the oldest
   ``new``/``processed`` article);
2. ``source_health`` rows written by ``collect``/``doctor``;
3. the scheduler log (``data/logs/pipeline.log``) written by
   ``scripts/run_pipeline.sh`` — one block per run, parsed by
   :func:`parse_pipeline_log`;
4. optional live probes of LM Studio and the Telegram Bot API.

The result is a verdict plus the evidence behind it, so an empty channel can
be explained by one command instead of by reading logs and the database by
hand. Nothing here writes to the database or sends messages.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

# --- scheduler log parsing -------------------------------------------------

_BLOCK_START = re.compile(r"^=====\s*(?P<ts>\S+)\s*=====\s*$")
_COLLECTED = re.compile(r"^Collected (\d+) article\(s\) from '(?P<source>[^']+)'")
_STORED = re.compile(r"^Stored (\d+) new, skipped (\d+) duplicate\(s\)")
_DONE_PROCESS = re.compile(r"^Done: (\d+) processed, (\d+) skipped, (\d+) error\(s\)")
_DONE_PUBLISH = re.compile(r"^Done: (\d+) published, (\d+) error\(s\)")
_EXIT_CODE = re.compile(r"^exit_code=(?P<code>\d+)")
_PROCESSED_ITEM = re.compile(r"^\[\d+\] processed \(")
_SKIPPED_ITEM = re.compile(r"^\[\d+\] skipped \(irrelevant\)")
_PUBLISHED_ITEM = re.compile(r"^\[\d+\] published: ")
_NOTE = re.compile(r"^(?:error|warning): ")

_MAX_NOTES = 4


@dataclass(frozen=True)
class RunSummary:
    """One scheduler run reconstructed from ``pipeline.log``."""

    started_at: str
    collected: int = 0
    stored_new: int = 0
    processed: int = 0
    skipped: int = 0
    published: int = 0
    errors: int = 0
    exit_code: int | None = None
    notes: tuple[str, ...] = ()


def parse_pipeline_log(text: str, limit: int = 5) -> list[RunSummary]:
    """Parse the last ``limit`` run blocks of a ``pipeline.log`` text.

    Tolerant by design: unknown lines are ignored and a truncated final block
    (a run still in progress, or a log cut mid-write) still yields a summary.
    Returns oldest-to-newest order.
    """

    class _Acc:
        __slots__ = (
            "ts",
            "collected",
            "stored_new",
            "processed",
            "skipped",
            "published",
            "errors",
            "exit_code",
            "notes",
        )

        def __init__(self, ts: str) -> None:
            self.ts = ts
            self.collected = 0
            self.stored_new = 0
            self.processed = 0
            self.skipped = 0
            self.published = 0
            self.errors = 0
            self.exit_code: int | None = None
            self.notes: list[str] = []

        def add_note(self, line: str) -> None:
            if len(self.notes) < _MAX_NOTES:
                self.notes.append(line)

        def freeze(self) -> RunSummary:
            return RunSummary(
                started_at=self.ts,
                collected=self.collected,
                stored_new=self.stored_new,
                processed=self.processed,
                skipped=self.skipped,
                published=self.published,
                errors=self.errors,
                exit_code=self.exit_code,
                notes=tuple(self.notes),
            )

    blocks: list[RunSummary] = []
    current: _Acc | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        header = _BLOCK_START.match(line)
        if header:
            if current is not None:
                blocks.append(current.freeze())
            current = _Acc(header.group("ts"))
            continue
        if current is None:
            continue
        match = _COLLECTED.match(line)
        if match:
            current.collected += int(match.group(1))
            continue
        match = _STORED.match(line)
        if match:
            current.stored_new += int(match.group(1))
            continue
        if _PROCESSED_ITEM.match(line):
            current.processed += 1
            continue
        if _SKIPPED_ITEM.match(line):
            current.skipped += 1
            continue
        if _PUBLISHED_ITEM.match(line):
            current.published += 1
            continue
        match = _DONE_PROCESS.match(line)
        if match:
            current.processed = max(current.processed, int(match.group(1)))
            current.skipped = max(current.skipped, int(match.group(2)))
            current.errors += int(match.group(3))
            continue
        match = _DONE_PUBLISH.match(line)
        if match:
            current.published = max(current.published, int(match.group(1)))
            current.errors += int(match.group(2))
            continue
        match = _EXIT_CODE.match(line)
        if match:
            current.exit_code = int(match.group("code"))
            continue
        if _NOTE.match(line):
            current.add_note(line)
    if current is not None:
        blocks.append(current.freeze())
    if limit <= 0:
        return []
    return blocks[-limit:]


# --- evidence and findings --------------------------------------------------


@dataclass(frozen=True)
class Facts:
    """Everything ``diagnose`` measured, gathered by the caller (CLI)."""

    now: datetime
    db_exists: bool = True
    counts: dict[str, int] = field(default_factory=dict)
    last_published_at: datetime | None = None
    last_published_title: str = ""
    oldest_new_at: datetime | None = None
    oldest_processed_at: datetime | None = None
    enabled_sources: int = 0
    failing_sources: tuple[tuple[str, str], ...] = ()
    telegram_configured: bool = False
    log_path: Path | None = None
    log_modified_at: datetime | None = None
    runs: tuple[RunSummary, ...] = ()
    llm_ok: bool | None = None
    llm_detail: str = ""
    telegram_ok: bool | None = None
    telegram_detail: str = ""
    # Error articles that exhausted their retries and stay parked (D-015).
    parked_errors: int = 0
    stale_run_minutes: float = 45.0
    quiet_hours: float = 6.0

    def total(self, *statuses: str) -> int:
        return sum(int(self.counts.get(status, 0)) for status in statuses)


def _hours_since(facts: Facts, moment: datetime | None) -> float | None:
    if moment is None:
        return None
    return (facts.now - moment).total_seconds() / 3600.0


@dataclass(frozen=True)
class Finding:
    """One conclusion with its severity and the suggested next step."""

    code: str
    severity: str  # blocking | warning | info | ok
    message: str
    hint: str = ""

    @property
    def blocking(self) -> bool:
        return self.severity == "blocking"


@dataclass(frozen=True)
class Report:
    """Verdict for the operator."""

    blocking: bool
    verdict: str
    findings: tuple[Finding, ...]

    def to_dict(self) -> dict:
        return {
            "blocking": self.blocking,
            "verdict": self.verdict,
            "findings": [asdict(finding) for finding in self.findings],
        }


def analyze(facts: Facts) -> Report:
    """Turn measured facts into a verdict (pure function, no I/O)."""
    findings: list[Finding] = []
    waiting_new = facts.total("new")
    waiting_processed = facts.total("processed")

    if not facts.db_exists:
        return Report(
            blocking=True,
            verdict="No database yet: the pipeline has never stored a single article.",
            findings=(
                Finding(
                    "no-database",
                    "blocking",
                    "No SQLite database at the configured path (data/news.db).",
                    "Run scripts/run_pipeline.sh once by hand and read the output: the "
                    "collect stage fails first when the sources are unreachable.",
                ),
            ),
        )

    # 1. Is the scheduler alive at all?
    if facts.log_path is None or facts.log_modified_at is None:
        findings.append(
            Finding(
                "no-log",
                "blocking",
                "No scheduler log (data/logs/pipeline.log) was found.",
                "Check that the Task Scheduler / cron entry actually runs "
                "scripts/run_pipeline.sh and that the data directory is the one being read.",
            )
        )
    else:
        log_age_minutes = (facts.now - facts.log_modified_at).total_seconds() / 60.0
        if log_age_minutes > facts.stale_run_minutes:
            findings.append(
                Finding(
                    "stale-runs",
                    "blocking",
                    f"The scheduler log has not changed for {log_age_minutes / 60:.1f} h "
                    f"(last write {facts.log_modified_at.isoformat()}, threshold "
                    f"{facts.stale_run_minutes / 60:.1f} h).",
                    "The job is not running: WSL shut down, the Task Scheduler task expired "
                    "or is waiting for logon/idle, or flock is blocked by a hung run.",
                )
            )

    # 2. Can the run deliver anything?
    if not facts.telegram_configured:
        findings.append(
            Finding(
                "telegram-config",
                "blocking",
                "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not visible to this process.",
                "The scheduler does not inherit your shell: make the env file export both "
                "variables and source it in the same command that calls run_pipeline.sh "
                "(see docs/DEVELOPMENT.md).",
            )
        )
    if facts.llm_ok is False:
        findings.append(
            Finding(
                "llm-down",
                "blocking" if waiting_new else "warning",
                f"LM Studio is unreachable: {facts.llm_detail or 'request failed'}.",
                f"{waiting_new} article(s) stay in status 'new' until it answers; start the "
                "LM Studio server, load a model, and confirm GET /v1/models.",
            )
        )
    if facts.telegram_ok is False:
        findings.append(
            Finding(
                "telegram-down",
                "blocking",
                f"Telegram rejects the request: {facts.telegram_detail or 'probe failed'}.",
                "A revoked or rotated token produces exactly this; check the bot token in "
                "BotFather and re-run doctor.",
            )
        )

    # 3. Queue states: where exactly did the pipeline stop?
    if waiting_processed:
        age = _hours_since(facts, facts.oldest_processed_at)
        age_text = f" (oldest {age:.1f} h old)" if age is not None else ""
        findings.append(
            Finding(
                "stuck-processed",
                "blocking",
                f"{waiting_processed} article(s) are processed but not published{age_text}.",
                "The publish stage fails for these rows: read the error lines of the last "
                "runs below, then check the token, chat_id and network access to "
                "api.telegram.org.",
            )
        )

    if waiting_new:
        age = _hours_since(facts, facts.oldest_new_at)
        age_text = f", oldest {age:.1f} h old" if age is not None else ""
        findings.append(
            Finding(
                "backlog",
                "info",
                f"{waiting_new} article(s) are waiting for LLM processing{age_text}.",
                "Each run processes at most --limit articles (10 in scripts/run_pipeline.sh), "
                "so a large backlog drains gradually.",
            )
        )

    recent = facts.runs[-3:]
    recent_errors = sum(run.errors for run in recent)
    recent_skipped = sum(run.skipped for run in recent)
    recent_stored = sum(run.stored_new for run in recent)

    if facts.parked_errors:
        findings.append(
            Finding(
                "parked-errors",
                "warning",
                f"{facts.parked_errors} article(s) sit in 'error' after exhausting their retries.",
                "They are skipped by 'recover' on purpose so they cannot block the queue "
                "(D-015); inspect the error lines below and, if the LLM can handle them now, "
                "run 'recover --max-attempts 0'.",
            )
        )

    if recent_errors:
        findings.append(
            Finding(
                "recent-errors",
                "warning",
                f"The last {len(recent)} scheduled run(s) reported {recent_errors} error(s).",
                "The error lines below name the stage; a bad model reply marks one article "
                "'error', which is retried by the next run (see 'recover').",
            )
        )

    if facts.failing_sources:
        listed = ", ".join(f"{sid} ({err[:60]})" for sid, err in facts.failing_sources[:4])
        findings.append(
            Finding(
                "sources-failing",
                "warning",
                f"{len(facts.failing_sources)} of {facts.enabled_sources} enabled source(s) "
                f"failed their last check: {listed}.",
                "One dead feed does not stop the pipeline; fix or disable it in config.SOURCES.",
            )
        )

    # 4. Healthy pipeline, quiet channel: the news supply or the relevance filter.
    last_publish_hours = _hours_since(facts, facts.last_published_at)
    if not any(finding.blocking for finding in findings):
        if last_publish_hours is None:
            findings.append(
                Finding(
                    "never-published",
                    "warning",
                    "Nothing has ever been published.",
                    "Check the first runs below; a fresh install publishes only once a "
                    "relevant SMS/messaging article appears in the enabled feeds.",
                )
            )
        elif last_publish_hours > facts.quiet_hours:
            if recent_stored == 0 and waiting_new == 0:
                message = (
                    f"No publication for {last_publish_hours:.1f} h and the last runs stored "
                    "no new articles."
                )
                hint = (
                    "The enabled feeds simply had nothing new (all items were duplicates). "
                    "For 15-minute polling this is normal; widen the source list to raise "
                    "the supply."
                )
            elif recent_skipped:
                message = (
                    f"No publication for {last_publish_hours:.1f} h: the last "
                    f"{len(recent)} run(s) stored {recent_stored} new article(s) and rejected "
                    f"{recent_skipped} as irrelevant."
                )
                hint = (
                    "The deterministic pre-LLM filter drops every article without an explicit "
                    "SMS/messaging signal in the title or feed text. If a targeted source "
                    "(content-review, anti-malware, securitylab) keeps losing SMS stories this "
                    "way, the per-source bypass proposed as D-012 is the next step."
                )
            else:
                message = f"No publication for {last_publish_hours:.1f} h."
                hint = "Nothing new passed the relevance filter; the pipeline itself is idle."
            findings.append(Finding("quiet-channel", "warning", message, hint))
        else:
            findings.append(
                Finding(
                    "healthy",
                    "ok",
                    f"Pipeline is healthy: last publication {last_publish_hours:.1f} h ago"
                    + (f", {waiting_new} article(s) waiting for the LLM." if waiting_new else "."),
                )
            )

    blocking = any(finding.blocking for finding in findings)
    chosen = _pick_finding(findings, blocking=blocking)
    verdict = chosen.message if chosen is not None else "Nothing to report."
    return Report(blocking=blocking, verdict=verdict, findings=tuple(findings))


# Which finding becomes the one-line verdict. A blocking cause always wins over
# a warning; inside the same severity the order below decides, so the verdict
# names the most actionable cause instead of the first one collected.
_VERDICT_PRIORITY = (
    "no-database",
    "stale-runs",
    "no-log",
    "telegram-config",
    "telegram-down",
    "llm-down",
    "stuck-processed",
    "quiet-channel",
    "never-published",
    "parked-errors",
    "recent-errors",
    "sources-failing",
    "backlog",
    "healthy",
)


def _pick_finding(
    findings: tuple[Finding, ...] | list[Finding], *, blocking: bool
) -> Finding | None:
    """Most actionable finding for the verdict line."""
    candidates = [finding for finding in findings if finding.blocking == blocking]
    for code in _VERDICT_PRIORITY:
        for finding in candidates:
            if finding.code == code:
                return finding
    return candidates[0] if candidates else None


def render(report: Report, facts: Facts, *, show_runs: int = 5) -> str:
    """Human-readable report for the CLI (evidence first, then the verdict)."""
    lines: list[str] = [f"telecom-news diagnose — {facts.now.isoformat(timespec='seconds')}"]
    if facts.db_exists:
        counts = ", ".join(
            f"{key}={facts.counts.get(key, 0)}"
            for key in (
                "published",
                "processed",
                "new",
                "skipped",
                "error",
            )
        )
        lines.append(f"Articles: {counts}")
        if facts.last_published_at is not None:
            title = facts.last_published_title.strip()
            suffix = f" — {title[:70]!r}" if title else ""
            lines.append(f"Last publication: {facts.last_published_at.isoformat()}{suffix}")
        else:
            lines.append("Last publication: never")
        for label, moment in (
            ("Oldest 'new'", facts.oldest_new_at),
            ("Oldest 'processed'", facts.oldest_processed_at),
        ):
            if moment is not None:
                hours = (facts.now - moment).total_seconds() / 3600.0
                lines.append(f"{label}: {moment.isoformat()} ({hours:.1f} h ago)")
    else:
        lines.append("Articles: no database")
    if facts.failing_sources:
        lines.append(f"Failing sources: {len(facts.failing_sources)}/{facts.enabled_sources}")
    if facts.parked_errors:
        lines.append(f"Parked errors (retries exhausted): {facts.parked_errors}")
    if facts.llm_ok is not None:
        lines.append(f"LM Studio: {'OK' if facts.llm_ok else 'FAIL'} — {facts.llm_detail}")
    if facts.telegram_ok is not None:
        lines.append(f"Telegram: {'OK' if facts.telegram_ok else 'FAIL'} — {facts.telegram_detail}")

    if facts.runs:
        lines.append(f"Last {len(facts.runs)} scheduled run(s) from {facts.log_path}:")
        for run in facts.runs[-show_runs:]:
            summary = (
                f"  {run.started_at}  collected={run.collected} new={run.stored_new} "
                f"processed={run.processed} skipped={run.skipped} published={run.published} "
                f"errors={run.errors} exit={run.exit_code}"
            )
            lines.append(summary)
            for note in run.notes:
                lines.append(f"      {note[:110]}")
    elif facts.log_path is not None:
        lines.append(f"No run blocks found in {facts.log_path}")

    lines.append("")
    for finding in report.findings:
        marker = {
            "blocking": "[BLOCK]",
            "warning": "[WARN ]",
            "info": "[INFO ]",
            "ok": "[OK   ]",
        }.get(finding.severity, "[     ]")
        lines.append(f"{marker} {finding.message}")
        if finding.hint:
            lines.append(f"        → {finding.hint}")
    lines.append("")
    lines.append(f"DIAGNOSIS: {report.verdict}")
    return "\n".join(lines)


def report_json(report: Report) -> str:
    """Machine-readable variant (``diagnose --json``)."""
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)


# --- live probes ------------------------------------------------------------


def probe_llm(base_url: str, *, timeout: float = 5.0) -> tuple[bool, str]:
    """Check LM Studio ``GET /models`` without raising. Returns (ok, detail)."""
    import httpx

    url = base_url.rstrip("/") + "/models"
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        return False, f"{type(exc).__name__} at {url}"
    if not response.is_success:
        return False, f"HTTP {response.status_code} at {url}"
    try:
        models = response.json().get("data") or []
    except ValueError:
        return False, f"invalid JSON at {url}"
    if not models:
        return False, f"no model loaded at {url}"
    return True, f"{models[0].get('id', 'unknown model')} at {url}"


def probe_telegram(token: str, *, timeout: float = 10.0) -> tuple[bool, str]:
    """Check the bot token with ``getMe``. Returns (ok, detail).

    The token itself is never included in the returned detail.
    """
    import httpx

    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        return False, f"{type(exc).__name__} reaching api.telegram.org"
    try:
        data = response.json()
    except ValueError:
        return False, f"invalid JSON (HTTP {response.status_code})"
    if response.is_success and data.get("ok") is True:
        username = data.get("result", {}).get("username")
        return True, f"bot @{username}" if username else "Bot API reachable"
    return False, str(data.get("description") or f"HTTP {response.status_code}")
