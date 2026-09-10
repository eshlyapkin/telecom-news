# CURRENT — telecom-news

Canonical project handoff. Claims are based on the repository files, Git state and
verified user-run results.

## Current status

- Branch: `arena/01a08bb1-telecom-news`.
- The working tree contains the M4–M6 implementation plus current security and documentation work; commit/push for this current documentation pass has not been performed.
- M0–M3: implemented.
- M4: Telegram Bot API delivery, HTML formatting, retry, dry-run and atomic `processed → published`; live delivery verified in the test channel.
- M5: one-shot `run`, `scripts/run_pipeline.sh`, pipeline logging, overlap lock and 15-minute cron operation verified in WSL.
- M6: four RSS sources, multi-source collection, source health and a deterministic SMS/messaging relevance guard.
- User verification: 23 articles published, 29 skipped, 0 new, 0 processed, 0 error.
- User verification: `117 passed`, Ruff check/format clean in WSL.
- User verification: DNS recovered after two transient WSL failures; subsequent source runs succeeded.

## Sources

Enabled sources:

- `sinch-blog`
- `twilio-blog`
- `infobip-blog`
- `gsma-newsroom`

All currently configured sources are English. A relevant Russian-language RSS/API
source is not selected or verified yet.

## Security status

- The Telegram token was verified with Bot API `getMe`.
- The token has been disclosed in chat during setup and must be treated as compromised until revoked and replaced again.
- Runtime secrets belong outside the repository in `~/.config/telecom-news/env` with mode `600`.
- `scripts/check_secrets.sh` and the pre-commit hook check staged additions for obvious Bot API tokens.
- The `httpx` request URL logger is suppressed so tokens do not appear in normal INFO logs.
- Token rotation is a user action and is **not verified as complete** in this handoff.

## Operational status

- `cron` in WSL is active and has produced successful 15-minute pipeline cycles.
- A temporary DNS failure caused `exit_code=1` twice; retry/recovery worked and no article data was lost.
- `doctor` is implemented and has mock coverage; live verification in the user's WSL is pending.
- SQLite backup/restore is implemented with integrity verification and `600` permissions; automatic backups support retention via `--keep-days`.
- Error recovery is implemented via `recover`; `run` retries error articles automatically.
- Telegram request pacing is implemented via `TELEGRAM_MIN_INTERVAL` (default `0.1`).
- `doctor` health probes preserve the item count from the last real collection.
- Repeated-error notifications are not implemented yet.

## Next work

1. Revoke the disclosed token and replace it locally; do not paste the replacement into chat.
2. Commit/push the pending security, hook, instruction and status documentation changes.
3. Implement M7 `doctor` with checks for SQLite, source DNS/HTTP, LM Studio, Telegram and environment.
4. Add SQLite backup/restore and interrupted-run recovery.
5. Add rate limits and operational error reporting.
6. Discover and verify at least one relevant Russian-language RSS/API source.

## References

- `docs/SECURITY.md` — token incident and rotation runbook.
- `docs/SKILLS/telegram-token-rotation.md` — safe token rotation procedure.
- `docs/SKILLS/m7-doctor-and-recovery.md` — next milestone procedure.
- `docs/DEVELOPMENT.md` — environment, scheduler, sources and hooks.
