# Skill: Telegram token rotation

## Purpose

Safely replace a Telegram Bot API token without exposing it in the repository,
logs, shell history, or chat.

## Preconditions

- The bot owner can access `@BotFather`.
- The channel `chat_id` is known.
- The project is run from the project-local `.venv`.

## Procedure

1. Revoke the old token in `@BotFather` with `/mybots` → bot → **API Token**.
2. Store the replacement only in `~/.config/telecom-news/env`.
3. Set file permissions to `600`.
4. Load the file with `source`; never pass the token as a command argument.
5. Check presence with `${#TELEGRAM_BOT_TOKEN}`, but never print the value.
6. Run `publish --dry-run`.
7. Send exactly one test article with `publish --limit 1`.
8. Confirm the Telegram message and then allow the scheduler to continue.
9. If any log contains the token, revoke it again.

## Safe verification

```bash
set -a
source ~/.config/telecom-news/env
set +a
test -n "$TELEGRAM_BOT_TOKEN"
test "$TELEGRAM_BOT_TOKEN" != "REPLACE_LOCALLY"
.venv/bin/python -m telecom_news publish --dry-run
```

## Forbidden

- Do not write the real token in this file.
- Do not add `.env` or secret files to Git.
- Do not use `echo "$TELEGRAM_BOT_TOKEN"`.
- Do not include the token in bug reports or screenshots.
