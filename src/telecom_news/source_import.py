"""Импорт каталога источников из таблицы (D-016).

Принимает CSV/TSV (основной путь) или XLSX, если установлен ``openpyxl``, и
превращает строки исследовательской таблицы в записи :class:`CatalogEntry` и
готовый модуль ``sources_catalog.py``.

Правила, выведенные из таблицы пользователя:

* вид источника решают колонка «Тип источника» и URL, а не MIME: 60
  status-эндпоинтов в таблице помечены ``application/rss+xml``, поэтому MIME
  как единственный признак дал бы 60 лент инцидентов в новостях;
* строки со статусом «исключён», «не подтверждён», кодами 403/404/410/502 или
  timeout не попадают в каталог (они остаются в исходной таблице);
* язык берётся из первой колонки (RU/EN), при её отсутствии — из домена
  (``.ru``) или кириллицы в тематике;
* идентификатор — латинский слаг издателя с уточнением по домену/пути, чтобы
  ``Twilio`` и ``Twilio — Status`` не столкнулись.
"""

from __future__ import annotations

import csv
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

from .sources_catalog import KIND_NEWS, KIND_STATUS, CatalogEntry

# ruff config: line-length = 100; генератор держит исходник каталога в этом лимите
MAX_LINE = 100

# Синонимы заголовков колонок (русские — из таблицы, английские — на случай экспорта).
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "language": ("класс/категория", "язык", "language", "lang", "категория"),
    "publisher": ("источник / издатель", "источник", "издатель", "candidate", "кандидат", "brand"),
    "url": ("rss / atom endpoint", "endpoint", "rss", "url", "feed"),
    "mime": ("формат / mime", "mime", "формат", "content-type", "тип содержимого"),
    "source_type": ("тип источника", "source type", "тип"),
    "grade": ("релевантность тематике", "релевантность", "класс", "grade", "relevance"),
    "topic": ("тематика", "topic", "тема"),
    "verified": ("дата проверки", "проверено", "date", "verified"),
    "status": ("результат проверки", "статус", "status", "result"),
    "note": ("комментарий", "причина", "comment", "note"),
}

_EXCLUDED_MARKERS = (
    "не вошёл",
    "не вошел",
    "исключён",
    "исключен",
    "не подтвержд",
    "не работает",
    "timeout",
    "таймаут",
    "404",
    "403",
    "410",
    "502",
    "html вместо",
)
# A status endpoint is recognised by its own markers, never by MIME alone: the
# research table labels 40 `status.<vendor>.com/history.rss` rows as
# application/rss+xml, and they must not be collected as editorial news (D-016).
_STATUS_URL_MARKERS = ("statuspage.io", "/history.rss", "/history.global.rss", "status.")
_STATUS_TYPE_MARKERS = (
    "operational status",
    "status page",
    "statuspage",
    "incident history",
    "мониторинг доступности",
)

_CYRILLIC_MAP = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ImportReport:
    """Результат разбора таблицы: годные записи и отброшенные строки."""

    entries: tuple[CatalogEntry, ...]
    skipped: tuple[tuple[str, str], ...]  # (url-or-publisher, reason)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


def _normalize(header: str) -> str:
    return re.sub(r"\s+", " ", (header or "").strip().lower().replace("\ufeff", ""))


def map_columns(headers: list[str]) -> dict[str, str]:
    """Сопоставить заголовки таблицы внутренним именам полей."""
    normalized = {_normalize(header): header for header in headers if header}
    mapping: dict[str, str] = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            for norm, original in normalized.items():
                if norm == alias or norm.startswith(alias):
                    mapping[field] = original
                    break
            if field in mapping:
                break
    return mapping


def transliterate(value: str) -> str:
    """Латинский слаг из произвольной строки (кириллица транслитерируется)."""
    lowered = (value or "").strip().lower()
    mapped = "".join(_CYRILLIC_MAP.get(char, char) for char in lowered)
    slug = _SLUG_RE.sub("-", mapped).strip("-")
    return slug or "source"


def classify_kind(mime: str, source_type: str, topic: str, url: str) -> str:
    """Вид источника: status-эндпоинты по типу/URL, всё остальное — новости.

    MIME не является решающим: в таблице у status-эндпоинтов он указан как
    ``application/rss+xml``, поэтому проверяются явные признаки — тип источника
    («Operational status / incidents»), сам URL (``status.``, ``statuspage.io``,
    ``/history.rss``) и JSON-формат.
    """
    if "json" in (mime or "").lower():
        return KIND_STATUS
    haystack = f"{source_type} {topic}".lower()
    if any(marker in haystack for marker in _STATUS_TYPE_MARKERS):
        return KIND_STATUS
    lowered_url = (url or "").lower()
    host_and_path = lowered_url.split("://", 1)[-1]
    if "statuspage.io" in host_and_path:
        return KIND_STATUS
    if "/history.rss" in host_and_path or "/history.global.rss" in host_and_path:
        return KIND_STATUS
    if host_and_path.startswith("status.") or "/status." in host_and_path:
        return KIND_STATUS
    return KIND_NEWS


def detect_language(row: dict[str, str], mapping: dict[str, str], url: str = "") -> str:
    """Язык источника: первая колонка RU/EN, затем домен .ru, затем контент."""
    first = (row.get(mapping.get("language", ""), "") or "").strip().lower()
    if first.startswith("ru"):
        return "ru"
    if first.startswith("en"):
        return "en"
    if ".ru" in (url or "").lower() or "/ru/" in (url or "").lower():
        return "ru"
    topic = row.get(mapping.get("topic", ""), "") or ""
    if re.search(r"[а-яё]", topic, re.IGNORECASE):
        return "ru"
    return "en"


def normalize_url(url: str) -> str:
    """Сравнимый вид URL: без завершающего слэша и с приведённым хостом."""
    value = (url or "").strip().rstrip("/")
    return value.lower().replace("https://", "").replace("http://", "")


def parse_rows(
    rows: list[dict[str, str]],
    *,
    existing_urls: set[str] | None = None,
    reserved_ids: set[str] | None = None,
) -> ImportReport:
    """Преобразовать строки таблицы в записи каталога.

    ``existing_urls`` — уже объявленные в ``config.SOURCES`` эндпоинты (в
    нормализованном виде): такие строки пропускаются, потому что курируемые
    записи конфига остаются главными (иначе имена вроде ``twilio`` столкнутся).
    ``reserved_ids`` — занятые идентификаторы для разрешения коллизий слага.
    """
    if not rows:
        return ImportReport(entries=(), skipped=())

    existing = {normalize_url(url) for url in (existing_urls or set())}
    reserved = set(reserved_ids or set())

    # Заголовки сопоставляются для каждого листа отдельно: в книге пользователя
    # основная таблица называется «RSS / Atom endpoint», а лист отклонённых —
    # «Кандидат / Endpoint / Статус». Один общий mapping молча терял бы endpoint
    # у второго листа и показывал бы ложную причину пропуска.
    sheets: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        sheets.setdefault(row.get("__sheet", ""), []).append(row)

    mappings: dict[str, dict[str, str]] = {}
    for sheet_name, sheet_rows in sheets.items():
        mapping = map_columns(list(sheet_rows[0].keys()))
        if all(field in mapping for field in ("url", "publisher")):
            mappings[sheet_name] = mapping
    if not mappings:
        raise ValueError(
            "cannot find column(s) ['url', 'publisher'] in the table; "
            f"headers seen: {list(rows[0].keys())}"
        )

    entries: list[CatalogEntry] = []
    skipped: list[tuple[str, str]] = []
    used_ids: dict[str, int] = {}
    seen_urls: set[str] = set()
    for sheet_name, sheet_rows in sheets.items():
        mapping = mappings.get(sheet_name)
        if mapping is None:
            for row in sheet_rows:
                label = next((value for value in row.values() if value), "(empty row)")
                skipped.append((str(label)[:120], f"sheet {sheet_name!r}: no endpoint column"))
            continue
        for row in sheet_rows:
            url = (row.get(mapping["url"], "") or "").strip()
            publisher = (row.get(mapping["publisher"], "") or "").strip()
            status = (row.get(mapping.get("status", ""), "") or "").strip()
            note = (row.get(mapping.get("note", ""), "") or "").strip()
            label = url or publisher or "(empty row)"
            if not url:
                skipped.append((label, "no endpoint in the row"))
                continue
            if not url.startswith(("http://", "https://")):
                skipped.append((label, f"not an http(s) endpoint: {url!r}"))
                continue
            if any(marker in status.lower() for marker in _EXCLUDED_MARKERS):
                skipped.append((url, f"excluded by the table: {status[:80]}"))
                continue
            if url in seen_urls:
                skipped.append((url, "duplicate endpoint (same publisher)"))
                continue
            if normalize_url(url) in existing:
                skipped.append((url, "already declared in config.SOURCES (curated entry wins)"))
                continue
            seen_urls.add(url)

            mime = row.get(mapping.get("mime", ""), "") or ""
            source_type = row.get(mapping.get("source_type", ""), "") or ""
            topic = (row.get(mapping.get("topic", ""), "") or "").strip()
            kind = classify_kind(mime, source_type, topic, url)
            grade = (row.get(mapping.get("grade", ""), "") or "A").strip().upper()
            if grade not in ("A+", "A", "B+", "B"):
                grade = grade if grade else "A"
            language = detect_language(row, mapping, url)

            base = transliterate(publisher or url.split("//")[-1].split("/")[0])
            if kind == KIND_STATUS and not base.endswith("status"):
                base = f"{base}-status"
            if base in reserved and base not in used_ids:
                base = f"{base}-feed"
            count = used_ids.get(base, 0)
            used_ids[base] = count + 1
            entry_id = base if count == 0 else f"{base}-{count + 1}"
            entries.append(
                CatalogEntry(
                    id=entry_id,
                    url=url,
                    kind=kind,
                    language=language,
                    grade=grade,
                    topic=topic,
                    gate="strict",
                    enabled=kind == KIND_NEWS,
                    verified=(row.get(mapping.get("verified", ""), "") or "").strip(),
                    note=(f"user verified: {topic}" if topic else note)[:180],
                )
            )
    return ImportReport(entries=tuple(entries), skipped=tuple(skipped))


def read_table(path: Path | str, *, sheets: list[str] | None = None) -> list[dict[str, str]]:
    """Прочитать CSV/TSV или XLSX в список словарей.

    Для XLSX читаются **все** листы, у которых есть колонки endpoint и издатель
    (листы «Сводка» и «Методология» отбрасываются сами). ``sheets`` ограничивает
    список листов по имени. Каждая строка помечается ключом ``__sheet``.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        return _read_xlsx(path, sheets=sheets)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(handle, dialect=dialect)
        return [
            {(key or ""): (value or "") for key, value in row.items()} | {"__sheet": path.name}
            for row in reader
        ]


def _read_xlsx(path: Path, *, sheets: list[str] | None = None) -> list[dict[str, str]]:
    try:
        from openpyxl import load_workbook  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "reading .xlsx needs the optional 'openpyxl' package; export the sheet as CSV "
            "instead (File → Download → CSV) or install it with 'pip install openpyxl'"
        ) from exc
    workbook = load_workbook(path, read_only=True, data_only=True)
    collected: list[dict[str, str]] = []
    for sheet in workbook.worksheets:
        if sheets and sheet.title not in sheets:
            continue
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            continue
        headers = [str(cell or "") for cell in rows[0]]
        mapping = map_columns(headers)
        if "url" not in mapping or "publisher" not in mapping:
            continue  # summary / methodology sheet
        for row in rows[1:]:
            if not row:
                continue
            record = {
                headers[index]: str(cell if cell is not None else "")
                for index, cell in enumerate(row)
                if index < len(headers)
            }
            collected.append(record | {"__sheet": sheet.title})
    return collected


MODULE_HEADER = '''"""Источники-«статусники» (M8+, D-016).

Каталог источников проекта: новостные RSS/Atom-ленты и отдельно —
operational status endpoints, которые не являются новостями.

Почему два вида:

* ``kind="news"`` — редакционные ленты. Только они попадают в
  ``config.SOURCES`` и участвуют в ``collect``/``run``/``doctor``.
* ``kind="status"`` — лента инцидентов status-страницы
  (``status.<vendor>/history.rss``, ``*.statuspage.io``). Содержимое — «все
  системы работают» / «инцидент закрыт», то есть не новости: такие записи
  объявлены в каталоге, но не собираются и не публикуются.

Данные каталога можно полностью заменить импортом из таблицы:

```bash
python -m telecom_news sources import --csv sources.csv --dry-run
python -m telecom_news sources import --csv sources.csv
python -m telecom_news sources --kind all        # что получилось
python -m telecom_news sources verify            # живая проверка эндпоинтов
```

Импорт читает все листы книги, классифицирует строки по колонке «Тип источника»
и URL (``Operational status / incidents`` и ``status.*/history.rss`` → status;
MIME не является решающим, потому что у status-эндпоинтов он указан как
``application/rss+xml``), а строки со статусом «исключён / не вошёл / 404 /
timeout» в каталог не попадают.
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
'''

MODULE_FOOTER = '''

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
'''


def _py_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _py_string_chunks(value: str, width: int) -> list[str]:
    """Разбить длинное значение на литералы, склеенные неявной конкатенацией.

    Разбиение идёт по границам слов, пробелы сохраняются, поэтому значение не
    меняется — только перестаёт быть длинной строкой исходника (ruff E501).
    """
    tokens = re.findall(r"\S+\s*", value) or [value]
    parts: list[str] = []
    current = ""
    for token in tokens:
        if current and len(current) + len(token) > width:
            parts.append(current)
            current = token
        else:
            current += token
    parts.append(current)
    return [_py_string(part) for part in parts if part]


def _render_field(field: str, value: object) -> list[str]:
    prefix = f"        {field}="
    if isinstance(value, bool):
        return [f"{prefix}{'True' if value else 'False'},"]
    literal = _py_string(str(value))
    if len(prefix) + len(literal) <= MAX_LINE - 2:
        return [f"{prefix}{literal},"]
    chunks = _py_string_chunks(str(value), MAX_LINE - len(prefix) - 2)
    lines = [f"{prefix}{chunks[0]}"]
    lines.extend(f"        {chunk}" for chunk in chunks[1:])
    lines[-1] += ","
    return lines


def render_module(
    entries: tuple[CatalogEntry, ...] | list[CatalogEntry],
    *,
    generated_from: str = "",
    generated_at: str = "",
) -> str:
    """Собрать исходный код ``sources_catalog.py`` с импортированными записями."""
    ordered = sorted(entries, key=lambda entry: (entry.kind, entry.id))
    lines: list[str] = []
    if generated_from:
        comment = (
            f"generated from {generated_from}"
            + (f" on {generated_at}" if generated_at else "")
            + " by `python -m telecom_news sources import`"
        )
        lines.extend(f"# {line}" for line in textwrap.wrap(comment, width=MAX_LINE - 2))
    lines.append("CATALOG: tuple[CatalogEntry, ...] = (")
    for entry in ordered:
        lines.append("    CatalogEntry(")
        for field in (
            "id",
            "url",
            "kind",
            "language",
            "grade",
            "topic",
            "gate",
            "enabled",
            "verified",
            "note",
        ):
            lines.extend(_render_field(field, getattr(entry, field)))
        lines.append("    ),")
    lines.append(")")
    body = "\n".join(lines)
    return f"{MODULE_HEADER}\n\n{body}\n{MODULE_FOOTER}"
