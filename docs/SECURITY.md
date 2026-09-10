# Security and secret handling

## Current status (2026-09-10)

- Telegram Bot API delivery is implemented and has been verified with a real test channel.
- The bot token was exposed during setup in chat and in an HTTP request log. Treat the old token as compromised.
- The token must be revoked in BotFather and replaced before the next production run.
- The replacement token must not be committed, pasted into project documentation, or written to `data/`.
- The Telegram HTTP client is configured not to emit `httpx` request URLs at INFO level, because Bot API URLs contain the token.
- `TELEGRAM_CHAT_ID` is not a secret, but it is still configured through the environment.

## Token rotation runbook

1. Open `@BotFather` in Telegram.
2. Run `/mybots` and select `@sms_telecom_news_bot`.
3. Open **API Token** and revoke the current token.
4. Generate or copy the replacement token. Do not paste it into chat or GitHub.
5. On the WSL machine, update the private environment file:

   ```bash
   nano ~/.config/telecom-news/env
   chmod 600 ~/.config/telecom-news/env
   ```

   The file should contain only local environment assignments, for example:

   ```bash
   export TELEGRAM_BOT_TOKEN='REPLACE_LOCALLY'
   export TELEGRAM_CHAT_ID='-1004389197486'
   ```

6. Verify only that the variable is present; never print its value:

   ```bash
   test -n "$TELEGRAM_BOT_TOKEN" && echo "Telegram token is set"
   test "$TELEGRAM_BOT_TOKEN" != "REPLACE_LOCALLY"
   ```

7. Run a dry-run, then publish one article:

   ```bash
   .venv/bin/python -m telecom_news publish --dry-run
   .venv/bin/python -m telecom_news publish --limit 1
   ```

8. Inspect logs for errors, not for the token:

   ```bash
   tail -50 data/logs/pipeline.log
   ```

## Rules

- Never put tokens in Python files, Markdown, shell scripts, commits, issues, screenshots, or chat.
- Never run `set -x` around the pipeline or print the complete environment.
- Keep secrets outside the repository, preferably in `~/.config/telecom-news/env` with mode `600`.
- If a token appears in a log, terminal capture, or chat, revoke it and repeat this runbook.
- A Telegram failure leaves the article `processed`; do not manually reset it unless the API response is understood.
