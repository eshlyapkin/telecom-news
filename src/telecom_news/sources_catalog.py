"""Источники-«статусники» (M8+, D-016).

Каталог источников проекта: новостные RSS/Atom-ленты и отдельно —
operational status endpoints, которые не являются новостями.

Почему два вида:

* ``kind="news"`` — редакционные ленты. Только они попадают в
  ``config.SOURCES`` и участвуют в ``collect``/``run``/``doctor``.
* ``kind="status"`` — машинные JSON-эндпоинты статус-страниц
  (``/history.rss``, ``*.statuspage.io``). ``feedparser`` их не разбирает, а
  содержимое — «все системы работают» / «инцидент закрыт», то есть не новости.
  Они объявлены, но не публикуются: это материал для будущего мониторинга
  доступности, а не для канала.

Данные каталога можно полностью заменить импортом из таблицы:

```bash
python -m telecom_news sources import --csv sources.csv --dry-run
python -m telecom_news sources import --csv sources.csv
python -m telecom_news sources --kind all        # что получилось
python -m telecom_news sources verify            # живая проверка эндпоинтов
```

Импорт классифицирует строки по колонке MIME: ``application/json`` → status,
``application/rss+xml`` / ``application/xml`` / ``text/xml`` → news, а строки
со статусом «исключён / 404 / timeout» не попадают в каталог.
"""

from __future__ import annotations

from dataclasses import dataclass

KIND_NEWS = "news"
KIND_STATUS = "status"
KINDS = (KIND_NEWS, KIND_STATUS)

GRADES = ("A+", "A", "B+", "B")


@dataclass(frozen=True)
class CatalogEntry:
    """Одна запись каталога источников.

    ``id`` должен быть уникален среди всех источников (он же ключ в
    ``config.SOURCES`` для news). ``gate`` — политика релевантности:
    ``"strict"`` (детерминированный keyword-guard, D-011) или ``"llm"``
    (сразу модель, D-012) для узких лент.
    """

    id: str
    url: str
    kind: str = KIND_NEWS
    language: str = "en"
    grade: str = "A"
    topic: str = ""
    gate: str = "strict"
    enabled: bool = True
    verified: str = ""
    note: str = ""


# Каталог. Массовый импорт из таблицы заменяет этот кортеж целиком;
# правки вручную тоже допустимы — формат простой и проверяется тестами.
CATALOG: tuple[CatalogEntry, ...] = (
    # --- messaging-фокус, проверено вручную 2026-09-11 ---
    CatalogEntry(
        id="mef-news",
        url="https://mobileecosystemforum.com/feed/",
        language="en",
        grade="A+",
        topic="Industry association / messaging",
        verified="2026-09-11",
        note="MEF: недельные дайджесты, RCS/A2P/OTP; добавлен в D-014",
    ),
    CatalogEntry(
        id="mobilesquared",
        url="https://www.mobilesquared.co.uk/feed/",
        language="en",
        grade="A+",
        topic="A2P SMS / RCS / WhatsApp market research",
        verified="2026-09-11",
        note="Mobilesquared: только рынок сообщений; низкая частота, высокая точность",
    ),
    # --- из проверенной таблицы пользователя (лист «Дополнительные») ---
    CatalogEntry(
        id="total-telecom",
        url="https://totaltele.com/category/technology/feed/",
        language="en",
        grade="A",
        topic="Telecom technology / messaging infrastructure",
        verified="2026-09-11",
        note="user verified: carrier technology, API, messaging infrastructure",
    ),
    CatalogEntry(
        id="simpletexting",
        url="https://simpletexting.com/feed/",
        language="en",
        grade="A+",
        topic="SMS vendor / marketing",
        verified="2026-09-11",
        note="user verified: business SMS, MMS, 10DLC, compliance",
    ),
    CatalogEntry(
        id="textmagic",
        url="https://www.textmagic.com/blog/feed/",
        language="en",
        grade="A+",
        topic="SMS vendor / CPaaS",
        verified="2026-09-11",
        note="user verified: SMS API, delivery, compliance, messaging",
    ),
)


def catalog_issues(entries: tuple[CatalogEntry, ...] | list[CatalogEntry] = CATALOG) -> list[str]:
    """Вернуть список проблем каталога (пустой список = всё в порядке).

    Проверяются уникальность ``id`` и ``url``, допустимость ``kind``/``gate``
    и схема URL. Используется тестом и командой ``sources``.
    """
    issues: list[str] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    for entry in entries:
        if entry.kind not in KINDS:
            issues.append(f"{entry.id}: unknown kind {entry.kind!r}")
        if entry.gate not in ("strict", "llm"):
            issues.append(f"{entry.id}: unknown gate {entry.gate!r}")
        if not entry.url.startswith(("http://", "https://")):
            issues.append(f"{entry.id}: url must be http(s): {entry.url!r}")
        if entry.id in seen_ids:
            issues.append(f"duplicate id: {entry.id}")
        if entry.url in seen_urls:
            issues.append(f"duplicate url: {entry.url}")
        seen_ids.add(entry.id)
        seen_urls.add(entry.url)
    return issues


def catalog_counts(
    entries: tuple[CatalogEntry, ...] | list[CatalogEntry] = CATALOG,
) -> dict[str, dict[str, int]]:
    """Счётчики каталога по видам, языкам и классам (для ``sources``)."""
    counts: dict[str, dict[str, int]] = {"kind": {}, "language": {}, "grade": {}, "enabled": {}}
    for entry in entries:
        counts["kind"][entry.kind] = counts["kind"].get(entry.kind, 0) + 1
        counts["language"][entry.language] = counts["language"].get(entry.language, 0) + 1
        counts["grade"][entry.grade] = counts["grade"].get(entry.grade, 0) + 1
        state = "enabled" if entry.enabled else "disabled"
        counts["enabled"][state] = counts["enabled"].get(state, 0) + 1
    return counts
