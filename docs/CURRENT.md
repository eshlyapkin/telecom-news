# CURRENT — telecom-news

Canonical project handoff. Claims rest on repository files, Git state and actual
command results; anything else is marked NOT VERIFIED.

## Status (2026-09-11)

- **M0–M7 are implemented.** `master` carries M0–M3 (`242ac0d`); M4–M7 live on
  `arena/01a08bb1-telecom-news` (tip `6dbe4ed`, 11 commits ahead, `master` is its
  ancestor).
- M4–M7 were fast-forwarded into `arena/01a09044-telecom-news` and pushed.
  **PR #1 (`6dbe4ed` → `master`) is open, GitHub reports `MERGEABLE`**; it is not
  merged, so `master` on its own still describes only M0–M3.
- **D-011 sits on top of `6dbe4ed` on this branch:** 12 Russian feeds in
  `config.SOURCES`, the Cyrillic-aware relevance guard and the off-topic fix in
  `processors/relevance.py`, plus tests and this document. It is a separate
  commit, so the M0–M3 → M4–M7 history stays attributable per milestone.
- Verified by the agent on 2026-09-11: `pytest -q` → **143 passed** (125 before
  D-011); `ruff check` and `ruff format --check` clean; `git diff --check` clean;
  working tree clean before the D-011 edits.
- Verified behaviour of the merged code: `doctor` prints per-dependency
  `[OK]`/`[FAIL]` and exits `1` when any check fails; `backup` writes an
  integrity-checked `0600` file and honours `--keep-days`; `collect` on a
  disabled source exits `2`.

## Why the channel looked empty (2026-09-10 evening → 2026-09-11)

Root cause, proven from code, not inferred:

1. M6 (`9a7c3a6`) added a deterministic pre-LLM gate: `check_relevance()`
   returns `relevant=False` when `has_messaging_signal()` finds no term from
   `MESSAGING_TERMS`.
2. That tuple was **entirely Latin-script** (asserted by a test now). Russian
   articles write «СМС», «чат-бот», «мессенджер» — no match, so every such item
   was stored as `skipped` **without the model ever being asked**.
3. The three RU feeds had just been enabled in M7 (`6dbe4ed`), and the four EN
   feeds publish 2–3 times a week, so a 15-minute cron produced no publications.
4. Secondary defect found in the same function: `is_obviously_off_topic()` ended
   with `and not has_messaging_signal(article)`, which is always `False` at that
   point of `check_relevance()` — the voice/video/email guard could never fire
   (dead code).

D-011 fixes both: Cyrillic signal terms (recall) plus a title-based off-topic
rule that can actually fire. `смс`/`чат-бот`/`мессендж` stories now reach the
LLM while generic «5G/LTE/камеры» CNews items are still skipped without a call.

Measured against reality, so expectations stay honest: of 25 real fresh RU
headlines fetched from `cnews-telecom`, `cnews-safe` and `anti-malware-news` on
2026-09-11, **0** mention the target ecosystem in the title. That is correct
filtering, not a defect — today those feeds carried 5G/LTE rollouts, cameras,
SAP CVEs, passkeys and AI stories. Two structural limits follow:

- `parse_feed()` has no description for most CNews `/news/line/` items (only
  `/news/top/` ones carry a lead), and the guard reads title + feed text only.
  An SMS angle that appears further down the article is therefore invisible to
  both the guard and the LLM. `content-review` (operator/SMS-fraud focus) is the
  productive RU feed; CNews is a low-yield supplement.
- If the volume stays too thin, the next step is a per-source decision rather
  than more keywords: skip the deterministic gate for narrow sources
  (e.g. `content-review`) and let the model judge. Not implemented — needs
  approval as D-012.

## Sources

19 declared, **15 enabled**. `sinch-blog`, `twilio-blog`, `infobip-blog`,
`gsma-newsroom` (en); `content-review`, `iksmedia`, `habr-cellular-news`,
`cnews-telecom`, `cnews-safe`, `cnews-biz`, `cnews-internet`, `securitylab-news`,
`securitylab-analytics`, `anti-malware-news`, `anti-malware-analytics` (ru).

Declared but disabled: `cnews-corp`, `securitylab-vulnerabilities`,
`anti-malware-press`, `nag-all`. Flip `enabled` in `src/telecom_news/config.py`
to activate — no other code change is needed.

Verification level per feed: `cnews-telecom`, `cnews-safe`, `anti-malware-news`
were fetched by the agent on 2026-09-11 and showed items dated 2026-09-11; the
other nine new feeds are **user-verified only** — the agent sandbox has no
outbound network (`curl` to any host fails), so they are NOT VERIFIED here.

## Security status

- **Token rotation: user confirmed a new token on 2026-09-11.** The revocation of
  the old one is user-attested; the agent cannot inspect BotFather state.
- No Telegram token exists in the repository: every blob of `master` and of
  `arena/01a08bb1-telecom-news` was scanned for the Bot API token pattern — clean.
- Runtime secrets stay outside the repo in `~/.config/telecom-news/env` (mode
  `600`). `scripts/check_secrets.sh` and the pre-commit hook check staged
  additions; `httpx` request-URL logging stays suppressed because Bot API URLs
  embed the token.
- Never paste a token into chat, issues, commits or logs. `TELEGRAM_CHAT_ID` is
  configured through the environment too, though it is not a secret.

## Still open

- Live run of `run` after D-011 — the published volume must be re-measured; the
  earlier figure (23 published, 29 skipped, 0 error) predates this change and
  came from RU feeds that were being skipped. NOT VERIFIED.
- `doctor` against live dependencies (LM Studio, Telegram, real feeds) — mock
  coverage only.
- Interrupted-run recovery required by M7 (kill mid-run, rerun without duplicates
  or loss) — covered by tests and indirectly by two transient DNS failures in
  WSL; no kill test recorded.
- Repeated-error notifications — not implemented.
- Cadence: cron runs every 15 minutes, ROADMAP M5 states a daily cycle. Decide
  whether 15 minutes is intended; at that rate `skipped` items also cost LLM calls.
- Documentation debt: `docs/PROJECT_STATE.md` and `docs/SESSION_HANDOFF.md` still
  describe the end of planning («M0 not started, no code at all»), `docs/ROADMAP.md`
  carries no milestone statuses, `docs/TECHNICAL_SPEC.md` specifies a superseded
  M0 (click CLI in `main.py`), `docs/TEST_SPEC.md` is a 3-line stub. Dead code from
  that superseded spec is still shipped: `src/telecom_news/main.py` (imported by
  nothing) and `logging_setup.py` (used only by its own test; the CLI uses
  `logging_config.py`).
- `AGENTS.md` states the project root as `/home/joe/Projects/telecom-news`.

## References

- `docs/DECISIONS.md` — D-007…D-011 (sources, Telegram delivery, Russian feeds).
- `docs/SECURITY.md` — token incident and rotation runbook.
- `docs/SKILLS/telegram-token-rotation.md`, `docs/SKILLS/m7-doctor-and-recovery.md`.
- `docs/DEVELOPMENT.md` — environment, scheduler, sources, hooks.
