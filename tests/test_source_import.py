"""Tests for the source catalog and the table importer (D-016). No network."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from telecom_news import cli
from telecom_news.source_import import (
    classify_kind,
    map_columns,
    parse_rows,
    read_table,
    render_module,
    transliterate,
)
from telecom_news.sources_catalog import (
    CATALOG,
    KIND_NEWS,
    KIND_STATUS,
    catalog_counts,
    catalog_issues,
)

HEADERS = [
    "Класс/Категория",
    "Источник / издатель",
    "RSS / Atom endpoint",
    "Формат / MIME",
    "Тип источника",
    "Релевантность тематике",
    "Тематика",
    "Дата проверки",
    "Результат проверки",
    "Официальная страница / основание",
    "Комментарий",
]


def _row(**values: str) -> dict[str, str]:
    row = {header: "" for header in HEADERS}
    row.update(values)
    return row


def test_catalog_is_consistent() -> None:
    assert CATALOG, "the catalog must not be empty"
    assert catalog_issues() == []
    counts = catalog_counts()
    assert counts["kind"][KIND_NEWS] >= 1
    assert all(entry.url.startswith("https://") for entry in CATALOG)


def test_map_columns_understands_the_research_table_headers() -> None:
    mapping = map_columns(HEADERS)
    assert mapping["url"] == "RSS / Atom endpoint"
    assert mapping["publisher"] == "Источник / издатель"
    assert mapping["mime"] == "Формат / MIME"
    assert mapping["grade"] == "Релевантность тематике"
    assert mapping["status"] == "Результат проверки"
    assert mapping["note"] == "Комментарий"


def test_classify_kind_uses_mime_and_topic() -> None:
    assert (
        classify_kind("application/json", "Operational status / Incidents", "", "") == KIND_STATUS
    )
    assert (
        classify_kind("application/rss+xml", "SMS vendor", "", "https://x.com/blog/feed")
        == KIND_NEWS
    )
    assert classify_kind("text/xml", "Технические новости", "", "https://x.ru/rss") == KIND_NEWS
    # A JSON status endpoint is status even without a telling topic.
    assert classify_kind("application/json", "", "", "https://status.example.com/history.rss") == (
        KIND_STATUS
    )


def test_transliterate_makes_readable_ids() -> None:
    assert transliterate("Хабра — Сотовая связь / новости") == "habra-sotovaya-svyaz-novosti"
    assert transliterate("Twilio — Status") == "twilio-status"
    assert transliterate("!!!") == "source"


def test_parse_rows_builds_entries_and_skips_unusable_rows() -> None:
    curated = {"www.twilio.com/en-us/blog.feed.xml"}
    rows = [
        _row(
            **{
                "Класс/Категория": "EN",
                "Источник / издатель": "Twilio",
                "RSS / Atom endpoint": "https://www.twilio.com/en-us/blog.feed.xml",
                "Формат / MIME": "application/rss+xml",
                "Релевантность тематике": "A+",
            }
        ),
        _row(
            **{
                "Класс/Категория": "EN",
                "Источник / издатель": "Twilio — Status",
                "RSS / Atom endpoint": "https://status.twilio.com/history.rss",
                "Формат / MIME": "application/json",
                "Тип источника": "Operational status / Incidents",
                "Релевантность тематике": "A",
                "Дата проверки": "2026-09-11",
            }
        ),
        _row(
            **{
                "Класс/Категория": "RU",
                "Источник / издатель": "Хабра — Информационная безопасность / новости",
                "RSS / Atom endpoint": "https://habr.com/ru/rss/hubs/infosecurity/news/?fl=ru",
                "Формат / MIME": "text/xml",
                "Тематика": "SMS fraud; OTP; phishing",
                "Релевантность тематике": "A",
            }
        ),
        _row(
            **{
                "Источник / издатель": "Subex",
                "RSS / Atom endpoint": "https://www.subex.com/feed/",
                "Результат проверки": "НЕ ПОДТВЕРЖДЁН / НЕ РАБОТАЕТ; Исключён: HTTP 410",
            }
        ),
        _row(
            **{
                "Источник / издатель": "No endpoint",
                "RSS / Atom endpoint": "",
            }
        ),
    ]

    report = parse_rows(rows, existing_urls=curated, reserved_ids={"twilio"})

    by_id = {entry.id: entry for entry in report.entries}
    assert set(by_id) == {"twilio-status", "habra-informatsionnaya-bezopasnost-novosti"}, by_id
    assert by_id["twilio-status"].kind == KIND_STATUS
    assert by_id["twilio-status"].enabled is False, "status endpoints are declared, not collected"
    assert by_id["twilio-status"].language == "en"
    assert by_id["habra-informatsionnaya-bezopasnost-novosti"].language == "ru"
    assert by_id["habra-informatsionnaya-bezopasnost-novosti"].grade == "A"

    reasons = dict(report.skipped)
    assert any("curated entry wins" in reason for reason in reasons.values())
    assert any("excluded by the table" in reason for reason in reasons.values())
    assert any("no endpoint" in reason for reason in reasons.values())
    assert report.skipped_count == 3


def test_parse_rows_disambiguates_repeated_publishers() -> None:
    rows = [
        _row(**{"Источник / издатель": "Vendor", "RSS / Atom endpoint": "https://a.example/feed"}),
        _row(**{"Источник / издатель": "Vendor", "RSS / Atom endpoint": "https://b.example/feed"}),
    ]

    report = parse_rows(rows)

    assert [entry.id for entry in report.entries] == ["vendor", "vendor-2"]


def test_parse_rows_rejects_a_table_without_endpoints() -> None:
    import pytest

    with pytest.raises(ValueError):
        parse_rows([{"Комментарий": "no columns at all"}])


def test_read_table_handles_semicolon_csv_and_bom(tmp_path: Path) -> None:
    path = tmp_path / "sheet.csv"
    path.write_text(
        "\ufeff" + ";".join(HEADERS) + "\n"
        "EN;SlickText;https://www.slicktext.com/blog/feed/;application/rss+xml;SMS vendor;A+;"
        "SMS marketing;2026-09-11;РАБОТАЕТ;;\n",
        encoding="utf-8",
    )

    rows = read_table(path)

    assert rows[0]["Источник / издатель"] == "SlickText"
    assert rows[0]["RSS / Atom endpoint"] == "https://www.slicktext.com/blog/feed/"


def test_render_module_produces_an_importable_catalog(tmp_path: Path) -> None:
    rows = [
        _row(
            **{
                "Источник / издатель": "SlickText",
                "RSS / Atom endpoint": "https://www.slicktext.com/blog/feed/",
                "Формат / MIME": "application/rss+xml",
                "Релевантность тематике": "A+",
                "Тематика": "SMS marketing",
                "Дата проверки": "2026-09-11",
            }
        ),
        _row(
            **{
                "Источник / издатель": "SlickText — Status",
                "RSS / Atom endpoint": "https://status.slicktext.com/history.rss",
                "Формат / MIME": "application/json",
                "Тематика": "Operational status",
            }
        ),
    ]
    report = parse_rows(rows)
    target = tmp_path / "generated_catalog.py"
    target.write_text(render_module(report.entries, generated_from="sheet.csv"), encoding="utf-8")

    spec = importlib.util.spec_from_file_location("generated_catalog_under_test", target)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses needs the module registered
    spec.loader.exec_module(module)

    assert module.catalog_issues() == []
    assert {entry.id for entry in module.CATALOG} == {"slicktext", "slicktext-status"}
    assert module.CATALOG[0].language == "en"


def test_sources_command_lists_the_catalog(capsys) -> None:
    assert cli._cmd_sources() == 0
    out = capsys.readouterr().out

    assert "Catalog:" in out
    assert CATALOG[0].id in out
    assert "kind" in out


def test_sources_command_reports_issues_and_status_kind(monkeypatch, capsys) -> None:
    from telecom_news import sources_catalog

    broken = sources_catalog.CatalogEntry(id="x", url="ftp://nope", kind="weird")
    monkeypatch.setattr(sources_catalog, "CATALOG", (broken,))

    code = cli._cmd_sources(kind="all")

    out = capsys.readouterr().out
    assert code == 1
    assert "unknown kind" in out and "url must be http(s)" in out


def test_sources_verify_reports_reachable_and_broken_feeds(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from telecom_news.config import SourceConfig

    monkeypatch.setattr(
        "telecom_news.config.SOURCES",
        {
            "good": SourceConfig(id="good", url="https://good.example/feed", language="en"),
            "bad": SourceConfig(id="bad", url="https://bad.example/feed", language="en"),
            "off": SourceConfig(
                id="off", url="https://off.example/feed", language="en", enabled=False
            ),
        },
    )

    class FakeRSS:
        def __init__(self, source_id: str, feed_url: str, language: str) -> None:
            self.source_id = source_id

        def collect(self, *, limit: int):
            from datetime import datetime, timezone

            from telecom_news.collectors import CollectorError
            from telecom_news.collectors.base import RawItem

            if self.source_id == "bad":
                raise CollectorError("GET https://bad.example/feed failed with HTTP 500")
            return [
                RawItem(
                    url="https://good.example/a",
                    source_id="good",
                    title="A",
                    published_at=datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc),
                )
            ]

    import telecom_news.collectors as collectors

    monkeypatch.setattr(collectors, "RssCollector", FakeRSS)
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))

    code = cli._cmd_sources(verify=True, db_path=tmp_path / "news.db")
    out = capsys.readouterr().out

    assert code == 1
    assert "[OK]   good" in out
    assert "[FAIL] bad" in out
    assert "off" not in out, "disabled sources are not verified"


def test_sources_import_writes_a_catalog(tmp_path: Path, capsys) -> None:
    path = tmp_path / "sheet.csv"
    path.write_text(
        "\ufeff" + ",".join(HEADERS) + "\n"
        "EN,SlickText,https://www.slicktext.com/blog/feed/,application/rss+xml,SMS vendor,A+,"
        "SMS marketing,2026-09-11,РАБОТАЕТ,,\n",
        encoding="utf-8",
    )
    target = tmp_path / "sources_catalog.py"

    assert cli._cmd_sources_import(path, output=target) == 0
    out = capsys.readouterr().out

    assert "1 news feed(s)" in out
    assert target.exists()
    assert 'id="slicktext"' in target.read_text(encoding="utf-8")

    assert cli._cmd_sources_import(path, output=target, dry_run=True) == 0
    assert "Dry run" in capsys.readouterr().out


def test_sources_import_reports_a_bad_table(tmp_path: Path, capsys) -> None:
    assert cli._cmd_sources_import(tmp_path / "missing.csv") == 2
    assert "cannot read the table" in capsys.readouterr().err

    bad = tmp_path / "bad.csv"
    bad.write_text("Комментарий\nничего полезного\n", encoding="utf-8")
    assert cli._cmd_sources_import(bad) == 2
    assert "cannot find column" in capsys.readouterr().err


def test_read_table_reads_every_data_sheet_of_a_workbook(tmp_path: Path) -> None:
    """A workbook with summary/methodology sheets yields only source rows."""
    openpyxl = __import__("openpyxl")
    workbook = openpyxl.Workbook()
    summary = workbook.active
    summary.title = "Сводка"
    summary.append(["Метрика", "Значение"])
    summary.append(["Источников", "100"])
    data = workbook.create_sheet("100 уникальных")
    data.append(HEADERS)
    data.append(
        [
            "EN",
            "SlickText",
            "https://www.slicktext.com/blog/feed/",
            "application/rss+xml",
            "SMS vendor / marketing",
            "A+",
            "SMS marketing",
            "2026-09-11",
            "РАБОТАЕТ",
            "",
            "",
        ]
    )
    priority = workbook.create_sheet("A+ приоритет")
    priority.append(HEADERS)
    priority.append(
        [
            "EN",
            "SlickText",
            "https://www.slicktext.com/blog/feed/",
            "application/rss+xml",
            "SMS vendor / marketing",
            "A+",
            "SMS marketing",
            "2026-09-11",
            "РАБОТАЕТ",
            "",
            "",
        ]
    )
    path = tmp_path / "book.xlsx"
    workbook.save(path)

    rows = read_table(path)

    assert len(rows) == 2, "summary sheet must be ignored, data sheets kept"
    assert {row["__sheet"] for row in rows} == {"100 уникальных", "A+ приоритет"}

    report = parse_rows(rows)
    assert [entry.id for entry in report.entries] == ["slicktext"]
    assert report.skipped_count == 1
    assert "duplicate endpoint" in report.skipped[0][1]


def test_import_does_not_exclude_entries_that_came_from_the_catalog(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Regression: after a first import the catalog is part of config.SOURCES.

    Those rows must not be treated as hand-written curated entries, otherwise a
    second import would silently drop everything it had imported before.
    """
    from telecom_news import sources_catalog
    from telecom_news.config import SourceConfig

    catalog_entry = sources_catalog.CatalogEntry(
        id="slicktext", url="https://www.slicktext.com/blog/feed/", kind=KIND_NEWS
    )
    monkeypatch.setattr(sources_catalog, "CATALOG", (catalog_entry,))
    monkeypatch.setattr(
        "telecom_news.config.SOURCES",
        {
            "slicktext": SourceConfig(id="slicktext", url=catalog_entry.url, language="en"),
            "twilio-blog": SourceConfig(
                id="twilio-blog", url="https://www.twilio.com/en-us/blog.feed.xml", language="en"
            ),
        },
    )
    path = tmp_path / "sheet.csv"
    path.write_text(
        "\ufeff" + ",".join(HEADERS) + "\n"
        "EN,SlickText,https://www.slicktext.com/blog/feed/,application/rss+xml,SMS vendor,A+,"
        "SMS marketing,2026-09-11,РАБОТАЕТ,,\n"
        "EN,Twilio,https://www.twilio.com/en-us/blog.feed.xml,application/rss+xml,SMS vendor,A+,"
        "CPaaS,2026-09-11,РАБОТАЕТ,,\n",
        encoding="utf-8",
    )

    assert cli._cmd_sources_import(path, output=tmp_path / "out.py") == 0
    out = capsys.readouterr().out

    assert "1 news feed(s)" in out, out
    generated = (tmp_path / "out.py").read_text(encoding="utf-8")
    assert 'id="slicktext"' in generated
    assert "curated entry wins" in out


def test_rendered_module_keeps_lines_within_the_project_limit() -> None:
    """The generated catalog is a normal source file: no ruff E501 in it."""
    source = render_module(CATALOG, generated_from="table.xlsx", generated_at="2026-09-11")
    offenders = [
        (index, len(line))
        for index, line in enumerate(source.splitlines(), start=1)
        if len(line) > 100
    ]
    assert offenders == []
    assert "note=" in source and "\\n" not in source.split("note=", 1)[1][:200]


def test_parse_rows_maps_headers_per_sheet() -> None:
    """Sheets of the book have different headers: rejected rows use «Endpoint».

    With one shared mapping the rejected sheet lost its endpoint column and every
    row looked like an empty row, which hid the real reason for the skip.
    """
    rows = [
        {
            "__sheet": "100 уникальных",
            "Язык": "EN",
            "Источник / издатель": "Vendor",
            "RSS / Atom endpoint": "https://vendor.example/feed",
            "Тематика": "SMS",
        },
        {
            "__sheet": "Не включено",
            "Кандидат": "Rejected",
            "Endpoint": "https://rejected.example/feed",
            "Статус": "Не подтверждён / не работает",
            "Причина": "Исключён: HTTP 410 при проверке.",
        },
    ]
    report = parse_rows(rows)
    assert [entry.id for entry in report.entries] == ["vendor"]
    assert report.skipped == (
        (
            "https://rejected.example/feed",
            "excluded by the table: Не подтверждён / не работает",
        ),
    )
