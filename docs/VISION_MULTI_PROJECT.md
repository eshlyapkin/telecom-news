# VISION — Multi-Project News Operations Platform (§§40–70)

> **Status:** proposed vision + gap analysis (S1+S2).  
> **Not** an accepted implementation milestone end-to-end.  
> Source of the requirements: operator brief §§40–70 (2026-09-12).  
> Claims about *current* code rest on the repository; anything not verified is marked **NOT VERIFIED**.

## 0. Product principle (§70)

Do **not** design the system as a “Telegram Channel Manager”.

Design it as a **MULTI-PROJECT NEWS OPERATIONS PLATFORM** where Telegram is **one** publishing destination. Future destinations (website, newsletter, Slack, LinkedIn, webhook) must fit the same model without rewriting the core:

```text
Sources → News Discovery → AI Analysis → Project Matching
        → Queue → Content Generation → Publishing Destination(s)
```

One discovered item may feed **multiple projects** and **multiple destinations**.

---

## 1. Vision summary (operator brief)

| § | Theme | Intent |
|---|---|---|
| 40 | Multi-Project Architecture | Top-level object = **Project** with own sources, AI rules, queue, channels, scheduler, analytics, logs, alerts, settings |
| 41 | Global Dashboard | Cross-project KPIs + project cards |
| 42 | Project Switcher | Persistent switcher; full UI context per project |
| 43 | Create Project | Wizard (info → topics → AI → sources → channels → publishing mode) |
| 44 | Project Templates | SMS / CPaaS / Regulation / Fraud / Custom; save-from-project |
| 45 | Clone Project | Selective copy of sources/rules/channels/queue/history |
| 46 | Project Isolation | Every entity has `projectId`; **backend** enforces ownership |
| 47 | Shared Sources | Global source definition + per-project subscription |
| 48 | Global Source Library | Catalog with health, projects using it, error rate |
| 49 | Shared News Detection | One fetch → N project AI evaluations |
| 50 | Cross-Project News View | Global explorer with per-project ACCEPT/REJECT scores |
| 51 | Multi-Project Queue | Per-project queue + Global Queue |
| 52 | Multi-Channel Publishing | N Telegram channels per project |
| 53 | Channel-Specific Formatting | Lang, style, hashtags, CTA, footer per channel |
| 54 | Global Channel Management | All channels across projects |
| 55 | Global Service Management | Infrastructure vs project workers |
| 56 | Global Kill Switch | Pause all / pause one project (confirm on global) |
| 57 | Global Search | News, sources, projects, channels, … |
| 58 | Global Analytics | By project / source / channel / errors |
| 59 | Project Cost Monitoring | Only if backend provides real cost data — **never invent** |
| 60 | Project Permissions | user → role → projects → permissions |
| 61 | Project Activity | Audit log filterable by project |
| 62 | Project Status Overview | Alerts visible even inside another project |
| 63 | Navigation Architecture | GLOBAL / PROJECT / SYSTEM |
| 64 | URL Architecture | `/projects/{id}/…` is source of truth, not only FE state |
| 65 | Multi-Project API | REST under `/api/projects/…` + global filters |
| 66 | Scalability | UX for 1…50+ projects (switcher, search, favorites — not only tabs) |
| 67 | Project Favorites | Favorites + recent |
| 68 | Project Overview Page | Health, queue, published, rejected, channels, recent errors |
| 69 | Dual configuration | Global settings vs project settings clearly labelled |
| 70 | Platform principle | Multi-destination ready data model |

Example projects from the brief: **SMS Business News**, **CPaaS Market**, **RCS News**.

---

## 2. Gap analysis — current `telecom-news` vs §§40–70

Verified baseline (git `master` / D-020 era):

- Single-tenant CLI pipeline: `collect → process → publish → deliver`
- One SQLite DB (`data/news.db`), one process config (`config.SOURCES`, env Telegram/LM)
- No Web GUI, no FastAPI in MVP (D-004 deferred until confirmed use case)
- No `projectId` on articles/sources/channels
- Multi-language channel posts and bot subscribers exist (M8), still **one** implicit project

| § | Requirement | Current state | Gap | Suggested phase |
|---|---|---|---|---|
| 40 | Many independent projects | One implicit project (whole repo) | Project entity, registry, isolation | **M9a foundation** (started) |
| 41 | Global dashboard | `diagnose` / `status` / `doctor` CLI only | Aggregate API + UI | M9b GUI |
| 42 | Project switcher | N/A | FE shell + URL context | M9b |
| 43 | Create project wizard | N/A | API `POST /projects` + UI wizard | M9b–c |
| 44 | Templates | N/A | Template store | M10 |
| 45 | Clone project | N/A | Clone API | M10 |
| 46 | Isolation + backend checks | No `projectId` | Schema + authz on every object | M9a→M11 |
| 47–48 | Shared sources / library | Global `SOURCES` + catalog, not multi-project | `source_definitions` + `project_sources` | M10 |
| 49–50 | Shared news / cross view | One article row, one relevance path | Shared article + per-project evaluation | M11 |
| 51 | Global + project queues | Statuses `new`/`processed`/… single queue | Queue scoped by project | M10–11 |
| 52–54 | Multi-channel + formatting | `TELEGRAM_CHAT_ID` / `_RU`/`_EN` | Channel table per project + format profiles | M10 |
| 55 | Global vs project services | One bot + one pipeline script | Service model + health | M12 ops |
| 56 | Kill switches | Stop cron / unset token manually | Flags in registry + publish guards | M9a (flag) → M9b UI |
| 57–58 | Search / analytics | SQL/CLI ad hoc | Index + metrics API | M12 |
| 59 | Cost monitoring | No token accounting | Only if LM/API exposes usage | later / NOT invent |
| 60 | Permissions | None | Auth + RBAC | M12 (after API auth) |
| 61–62 | Audit / cross-status | Logs on disk | Structured audit table | M12 |
| 63–64 | Nav + URLs | N/A | FE router | M9b |
| 65 | Multi-project API | None (D-004 deferred) | FastAPI optional extra — **use case now confirmed** | **M9a** |
| 66–68 | Scale UX / overview | N/A | FE patterns | M9b+ |
| 69 | Dual settings | Env file only | Settings split | M10 |
| 70 | Multi-destination model | Telegram-hardcoded delivery | `Destination` abstraction | M11+ (Telegram remains first) |

### What already helps

- Layered collectors / processors / storage / delivery (D-002)
- Source catalog + kill-switch env (D-016, D-017)
- Renditions per language (M8) — seed for channel-specific formatting
- `diagnose` evidence model — seed for project/global health cards
- Optional FastAPI was always the documented extension point (README, D-004)

### What must not break

- Live single-project CLI + `scripts/run_pipeline.sh` on the operator machine
- Existing `data/news.db` schema for articles (additive migrations only)
- No secrets in git; tokens stay in env

---

## 3. Implementation strategy (accepted direction for M9)

**Vertical slice first, not the whole GUI.**

### M9a — Foundation (this change set)

1. **Project registry** (JSON under `data/projects/registry.json` by default) with:
   - `id`, `name`, `description`, `status` (`running`/`paused`/`error`)
   - `publish_paused` (project kill switch)
   - `db_path` (default: shared legacy DB for the built-in project)
   - `source_ids` (subset of global `SOURCES`; empty = all enabled)
   - `channel_chat_ids` override (optional)
   - timestamps
2. **Built-in default project** `sms-business-news` mirroring today’s single-tenant setup (no data migration required).
3. **HTTP API** (optional extra `api`): FastAPI app with:
   - `GET /api/health`
   - `GET /api/projects`, `POST /api/projects`
   - `GET /api/projects/{id}`, `PATCH /api/projects/{id}`
   - `GET /api/dashboard` (global aggregates from registry + optional DB counts)
   - static **minimal GUI** at `/` (dashboard + project list; no auth yet — **localhost / trusted network only**)
4. **CLI**: `projects` / `projects list` / `projects show` / `serve` (API+GUI).
5. Docs: this file, D-021, ROADMAP M9, CURRENT note.
6. Tests for registry + API (httpx ASGI) without network.

### Explicitly out of M9a

- Full wizard, templates, clone, RBAC, shared article fan-out, cost UI
- Migrating every article row to `project_id` (planned M10–M11)
- Replacing Telegram as sole destination
- Production auth / HTTPS termination

### Later milestones (sketch)

| Milestone | Focus |
|---|---|
| M9b | GUI shell: switcher, overview, queue read-only, kill switch buttons |
| M10 | Sources subscription model, channels table, clone/templates |
| M11 | Shared news + per-project AI evaluation; destination abstraction |
| M12 | Auth/RBAC, audit, analytics, infrastructure panel |

---

## 4. Data model direction (target, not all in M9a)

```text
Project
  id, name, status, publish_paused, settings…

SourceDefinition (global)
  id, url, type, health…

ProjectSource (subscription)
  project_id, source_id, enabled, gate_override…

Article (shared, optional later)
  id, url, content_hash, …

ProjectArticle / Evaluation
  project_id, article_id, status, score, category…

Channel / Destination
  id, project_id, type=telegram|…, config, format_profile…

Publication
  project_id, destination_id, article_id, …
```

M9a only persists **Project** in the registry file. Legacy articles stay in the existing DB attached to the default project.

---

## 5. Security notes

- M9a API has **no authentication**. Bind to `127.0.0.1` by default; document risk if exposed.
- Default listen port is **8765** (`python -m telecom_news serve`), not 8000 (often occupied). Override with `--port` or `TELECOM_NEWS_SERVE_PORT`.
- Never put Telegram tokens in project JSON; reference env / secret store.
- Cost metrics (§59): show only backend-provided numbers — never fabricate.

---

## 6. Mapping brief examples → default seed

| Brief example | Registry seed (M9a) |
|---|---|
| SMS Business News | `sms-business-news` (default, uses current SOURCES + DB) |
| CPaaS Market | optional empty template id `cpaas-market` (paused, no sources) — created only if operator asks |
| RCS News | same — not auto-created in M9a |

M9a seeds **one** default project so production behaviour is unchanged.

---

## 7. Acceptance for M9a (verifiable)

- [x] Vision + gap doc exists (`docs/VISION_MULTI_PROJECT.md`)
- [ ] `python -m telecom_news projects list` shows default project
- [ ] `GET /api/projects` returns JSON with default project (when api extra installed)
- [ ] `GET /api/dashboard` returns aggregate counts
- [ ] GUI root page renders without separate Node build
- [ ] Existing `pytest` suite still green; new tests for registry/API
- [ ] CLI `run` / `collect` / `publish` paths unchanged for operators who never call `serve`

---

## 8. References

- Operator brief §§40–70 (chat, 2026-09-12)
- `docs/DECISIONS.md` — D-004 (API deferred → superseded in part by D-021), D-021
- `docs/ROADMAP.md` — M9
- `docs/ARCHITECTURE.md` — MVP single pipeline (still accurate for runtime path)
