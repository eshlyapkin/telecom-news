"""Read-only ops snapshots for the M9b control panel (queue, status, diagnose)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import load_config
from ..diagnostics import inspect_bot_lock, summarize_subscribers
from ..projects import ProjectRegistry
from ..source_overrides import list_sources_for_api


def _db(path: Path):
    from ..storage.database import Database

    return Database(path)


def project_queue(
    registry: ProjectRegistry,
    project_id: str,
    *,
    data_dir: Path,
    limit: int = 40,
) -> dict[str, Any]:
    """Queued / processed articles for one project DB (read-only)."""
    project = registry.get(project_id)
    db_path = registry.resolve_db_path(project, data_dir)
    if not db_path.exists():
        return {
            "project_id": project_id,
            "db_exists": False,
            "counts": {},
            "new": [],
            "processed": [],
        }
    db = _db(db_path)
    counts = db.count_by_status()
    new_rows = db.get_unprocessed(status="new", limit=limit)
    processed_rows = db.get_unprocessed(status="processed", limit=limit)

    def _brief(article) -> dict[str, Any]:
        return {
            "id": article.id,
            "title": (article.title or "")[:120],
            "source_id": article.source_id,
            "status": article.status,
            "published_at": article.published_at.isoformat() if article.published_at else None,
            "url": article.url,
        }

    return {
        "project_id": project_id,
        "db_exists": True,
        "counts": counts,
        "new": [_brief(a) for a in new_rows],
        "processed": [_brief(a) for a in processed_rows],
    }


def project_published(
    registry: ProjectRegistry,
    project_id: str,
    *,
    data_dir: Path,
    limit: int = 30,
) -> dict[str, Any]:
    """Published articles with the exact post text that went out, per language.

    Read-only: the preview is rebuilt from the stored rendition, so it shows what
    the channel received rather than a re-rendering of the article. Fields are
    returned separately (headline, summary, category) — the GUI must not inject
    feed-derived HTML into the page, and the Telegram markup is passed along as
    plain text for inspection.
    """
    from ..delivery.telegram import format_post

    project = registry.get(project_id)
    db_path = registry.resolve_db_path(project, data_dir)
    if not db_path.exists():
        return {"project_id": project_id, "db_exists": False, "posts": [], "count": 0}

    config = load_config()
    channel_ids = {chat_id for _lang, chat_id in config.channel_chat_ids}
    db = _db(db_path)
    rows: list[dict[str, Any]] = []
    for article in db.published_articles(limit=limit):
        article_id = article.id
        if article_id is None:
            continue
        deliveries = db.deliveries_of(article_id)
        renditions: list[dict[str, Any]] = []
        for lang in sorted({str(row["lang"]) for row in deliveries} | set(config.target_langs)):
            stored = db.get_rendition(article_id, lang)
            legacy = (article.llm_result or {}).get("summary")
            legacy_lang = (article.llm_result or {}).get("summary_language") or config.target_lang
            if stored is not None:
                headline = str(stored.get("title") or "") or article.title
                summary = str(stored.get("summary") or "")
            elif legacy and legacy_lang == lang:
                headline, summary = article.title, str(legacy)
            else:
                continue
            channel = [
                row
                for row in deliveries
                if row["lang"] == lang and str(row["chat_id"]) in channel_ids
            ]
            private = [
                row
                for row in deliveries
                if row["lang"] == lang and str(row["chat_id"]) not in channel_ids
            ]
            renditions.append(
                {
                    "lang": lang,
                    "headline": headline,
                    "summary": summary,
                    "telegram_html": format_post(
                        article, lang=lang, summary=summary, title=headline, show_flag=True
                    ),
                    "channel_sent": bool(channel),
                    "message_id": channel[0]["message_id"] if channel else None,
                    "sent_at": channel[0]["sent_at"] if channel else None,
                    "subscriber_sends": len(private),
                    "failed": [row["chat_id"] for row in deliveries if row["status"] == "failed"],
                }
            )
        rows.append(
            {
                "id": article_id,
                "title": article.title,
                "url": article.url,
                "source_id": article.source_id,
                "category": article.category,
                "published_at": (
                    article.published_at.isoformat() if article.published_at else None
                ),
                "posted_at": (
                    article.published_at_telegram.isoformat()
                    if article.published_at_telegram
                    else None
                ),
                "renditions": renditions,
            }
        )
    return {
        "project_id": project_id,
        "db_exists": True,
        "posts": rows,
        "count": len(rows),
        "target_langs": list(config.target_langs),
    }


def ops_status(
    registry: ProjectRegistry,
    *,
    data_dir: Path,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Bot lock, subscribers, last publish, pause flags — control-panel header."""
    config = load_config()
    bot_running, bot_stale, bot_detail = inspect_bot_lock(data_dir / "bot.lock")
    global_paused = registry.global_publish_paused()

    pid = project_id
    if pid is None:
        from ..projects import DEFAULT_PROJECT_ID

        pid = DEFAULT_PROJECT_ID

    project_paused = False
    last_published = None
    counts: dict[str, int] = {}
    active_sub = 0
    sub_without = 0
    suspicious: tuple[str, ...] = ()
    try:
        project = registry.get(pid)
        project_paused = bool(project.publish_paused)
        db_path = registry.resolve_db_path(project, data_dir)
        if db_path.exists():
            db = _db(db_path)
            counts = db.count_by_status()
            last = db.last_published()
            if last is not None:
                last_published = {
                    "id": last.id,
                    "title": (last.title or "")[:100],
                    "at": (
                        (last.published_at_telegram or last.fetched_at).isoformat()
                        if (last.published_at_telegram or last.fetched_at)
                        else None
                    ),
                }
            active_sub, sub_without, suspicious = summarize_subscribers(
                db.list_subscribers(),
                db.subscribers_with_languages(status="active"),
            )
    except KeyError:
        pass

    log_path = config.logs_dir / "pipeline.log"
    log_mtime = None
    if log_path.exists():
        log_mtime = datetime.fromtimestamp(log_path.stat().st_mtime, tz=timezone.utc).isoformat()

    sources = list_sources_for_api()
    enabled_n = sum(1 for row in sources if row["enabled"])

    return {
        "now": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_id": pid,
        "global_publish_paused": global_paused,
        "project_publish_paused": project_paused,
        "publish_effectively_paused": global_paused or project_paused,
        "bot_running": bot_running,
        "bot_lock_stale": bot_stale,
        "bot_detail": bot_detail,
        "subscribers_active_with_lang": active_sub,
        "subscribers_without_lang": sub_without,
        "suspicious_subscribers": list(suspicious),
        "counts": counts,
        "last_published": last_published,
        "pipeline_log_mtime": log_mtime,
        "sources_enabled": enabled_n,
        "sources_total": len(sources),
        "telegram_configured": bool(config.telegram_bot_token and config.telegram_chat_id),
        "target_langs": list(config.target_langs),
    }
