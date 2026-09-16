AGENTS.md — working rules for any AI assistant on this project.
Read this file first; `CLAUDE.md` and `docs/AI_WORKFLOW.md` point back here.

Project root: the telecom-news repository. Confirm it with
`git rev-parse --show-toplevel` plus the presence of `src/telecom_news/`, not by
a literal path (on the operator's machine it is /home/joe/Projects/telecom-news).

ORIENTATION — in this order, and no further until the task needs more:

1. `docs/CURRENT.md` — the STATE section at the top. Everything below it is a
   session log; read one entry only when you need the reasoning for that area.
2. `git log -3 --oneline` and `git status -sb`.
3. This file.

Then: `docs/DECISIONS.md` is the reasoning of record (D-001…), ARCHITECTURE is
the structure, ROADMAP the scope, `docs/SKILLS/` the operational runbooks.

SOURCE OF TRUTH

The source of truth is:
1. actual project files;
2. Git state;
3. actual tool/command results.

Previous assistant statements and plans are not evidence.

Never claim that:
- a file was created or changed;
- a command was executed;
- a test passed;
- a source was selected or verified;
- an endpoint exists;
- a milestone was completed;

unless this was confirmed by an actual tool result or by reading the
corresponding project file.

If information is not verified, explicitly say "not verified".

PROJECT STARTUP

Follow ORIENTATION above, then report current state, next step and blockers.

Do not read the whole documentation set up front. ROADMAP, ARCHITECTURE and
DECISIONS are loaded lazily, only when the task at hand needs them.

Do not trust documentation over the machine. Where a document and a command
disagree, the command wins and the document gets fixed.

SECURITY

Never store or print Telegram tokens or other credentials. Keep runtime secrets in
a user-owned environment file outside the repository (for example,
`~/.config/telecom-news/env`, mode 600). If a token appears in chat, logs, a
screenshot, or a commit, treat it as compromised: revoke it in BotFather and
rotate it. Run `docs/SKILLS/telegram-token-rotation.md` after rotation.

THE OPERATOR'S DATA IS LIVE

`data/` on the operator's machine is production state, not fixtures: publication
languages, relevance rules, source overrides, discovery history, the article
database. The control panel writes it while you work.

- Never edit a file in `data/` to try something out. Point
  `TELECOM_NEWS_DATA_DIR` at a scratch directory instead.
- Never send a partial `PUT /api/ai-rules`: it sets the field, and a call with
  one term replaces the operator's curated list. The panel always sends the whole
  list. (This happened on 2026-09-13; a backup taken seconds earlier saved it.)
- Back up before touching anything there, and verify the restore by comparing
  content, not by assuming.
- A file in `data/` changing under you is normal — the operator is using the
  panel. Re-read before concluding something broke.

INVARIANTS THAT COST AN INCIDENT

Each of these is load-bearing; changing one re-opens a bug that already shipped.
The full reasoning is in DECISIONS.

- The classifier's JSON contract (`relevance.RESPONSE_FORMAT_INSTRUCTION`) is
  appended by code to every prompt. The AI-rules editor holds editorial policy
  only. Do not tell an operator to put the output format in their prompt (D-023).
- Operator term lists are merged with the built-in ones, never replace them.
  A saved list without "чат-бот"/"мессендж" silently killed Russian coverage
  (D-011, D-022).
- Discovery scores a candidate feed by its **headlines**. Full text admits any
  vendor blog that mentions SMS in passing — measured: 90% vs 10% (D-024).
- News-search results supply **publishers**, never articles: their links are
  consent-walled redirects with no text, and ingesting them breaks URL-based
  deduplication (D-025).
- An undated sitemap entry is skipped. A plain sitemap is the whole archive in no
  order; telecompaper.com lists 19982 undated URLs (D-026).
- `~/.config/telecom-news/env` is a systemd EnvironmentFile: `VAR=value`, never
  `export VAR=value`, which systemd drops silently.
- The classifier knows only what the policy **names**. It rejected FCC opt-out
  rulemaking as "TCPA regulations unrelated to SMS" because the policy said
  "opt-in/opt-out requirements" and never said TCPA; naming the statute took the
  same source from 1 relevant article in 8 to 5 in 8 (2026-09-16). When a whole
  class of news is missing, read the rejection reasons before blaming the code.
- `telecom-news-serve` holds the code it was started with. After changing Python
  under `src/`, `systemctl --user restart telecom-news-serve` or the panel keeps
  running the old version — including the bug you just fixed. The scheduled
  pipeline is unaffected: it starts a fresh process per run.
- A settings writer changes only the fields it was given. `save_settings` takes
  `recheck_after_days` and `min_hit_rate` independently, for the same reason the
  AI-rules editor must send the whole list: a caller that knows about one field
  must not reset the other by omission.

TESTS ARE HERMETIC

`tests/conftest.py` gives every test its own data directory and clears the
project's environment variables. Consequences:

- Plain `pytest -q` and plain `git push` work in any shell. The old
  `env -u TELECOM_NEWS_DISABLED_SOURCES` workaround is obsolete — do not
  reintroduce it or advise it.
- A test that needs a config value sets it itself.
- Never make a test read the operator's `data/`.

WORKING RULES

Work on one milestone or one explicitly defined subtask at a time.

Before modifying code:
- inspect the files relevant to the task;
- check current Git state;
- identify the acceptance criteria for the task.

Do not implement functionality belonging to later milestones unless
explicitly approved.

Do not invent dependencies, files, APIs, endpoints or project state.

EXTERNAL RESEARCH

For external facts such as RSS/Atom endpoints, APIs, publication URLs,
vendor capabilities, and current product/service information:

- do not rely on model memory;
- use search results only for discovery;
- verify final claims against the official source;
- do not invent RSS/API endpoints;
- if a fact cannot be confirmed, mark it as NOT VERIFIED;
- do not repeat essentially the same failed search query;
- after several poor searches, change strategy.

For source discovery, evaluate publication relevance first.
Do not search for RSS/API until the content source itself has been shown
to regularly publish material relevant to the project's domain.

VERIFICATION

After changing code:

1. run the tests relevant to the change;
2. run the full test suite when completing a milestone;
3. run:
   git diff --check
   git status --short
   git diff --stat
4. compare the actual result with the milestone acceptance criteria.

A task is not complete merely because code was written.

A task is complete only when its acceptance criteria have been verified.

If verification fails:
- diagnose the failure;
- fix only within the current task scope;
- rerun verification;
- do not report the task as completed until verification succeeds.

DOCUMENTATION

docs/CURRENT.md is the canonical short handoff.

Update it when project state changes.

Do not duplicate the same detailed state across multiple documents.

Update ROADMAP only when project scope or milestone definition changes.
Update ARCHITECTURE only when architecture changes.
Update DECISIONS only when an architectural/project decision is made.

OUTWARD-FACING ACTIONS

Publishing to the channel, deleting or editing channel messages, and pushing to
GitHub are visible to other people. Confirm before doing them, even when the
surrounding task is approved; approval of one does not carry to the next.

`publish --dry-run` previews a post without sending. Prefer it.

HOOKS

`.githooks/` is enabled with `git config core.hooksPath .githooks`
(`scripts/setup.sh` does this). pre-commit runs the secret scan,
`git diff --check` and ruff; pre-push runs the full suite. Do not bypass them
with `--no-verify`: if a hook fails, the fix is the code, not the flag.

GIT

Do not create commits unless explicitly requested by the user.

Before a requested commit:
- verify tests;
- run git diff --check;
- inspect git status and diff;
- confirm only expected files will be committed.

Never start the next milestone automatically after completing the current one.
Stop and report the result.