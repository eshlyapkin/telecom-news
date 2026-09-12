# Prod MVP checklist — channel + bot + subscriber

Operator runbook to treat the **news engine** as production-ready.
Does **not** cover the multi-project GUI (M9b+). Secrets stay in
`~/.config/telecom-news/env` (mode 600); never paste tokens into chat or git.

## 0. One-time: merge and install

```bash
cd ~/Projects/telecom-news
source .venv/bin/activate
git fetch origin
git checkout master
git pull origin master          # after PR #6 is merged
# or, until merge:
# git checkout arena/01a095de-telecom-news && git pull

pip install -e '.[dev]'        # + '.[api]' only if you use serve
python -m telecom_news --version
```

## 1. Environment (scheduler must see the same file)

```bash
# ~/.config/telecom-news/env  (example keys — fill on the machine, do not commit)
# export TELEGRAM_BOT_TOKEN=...
# export TELEGRAM_CHAT_ID=...          # channel
# export TELEGRAM_CHAT_ID_EN=...       # optional second language channel
# export TELECOM_NEWS_TARGET_LANGS=ru,en
# export LMSTUDIO_BASE_URL=http://172.28.64.1:1234/v1
# export TELECOM_NEWS_DISABLED_SOURCES=commlawblog,iksmedia,beyond-telecom-law-blog,mef-news,deutsche-telekom-blog
# export TELECOM_NEWS_LIMIT=30         # collect/process budget only (D-020)

source ~/.config/telecom-news/env
.venv/bin/python -m telecom_news doctor
```

Doctor must show `[OK]` for database, telegram, lmstudio. Failing sources that
are already in `TELECOM_NEWS_DISABLED_SOURCES` are expected to stay off.

## 2. Scheduler (channel publish path)

Confirm Task Scheduler / cron runs every ~15 minutes:

```bash
source ~/.config/telecom-news/env
cd ~/Projects/telecom-news
./scripts/run_pipeline.sh
tail -n 40 data/logs/pipeline.log
.venv/bin/python -m telecom_news diagnose
```

Healthy signs:

- fresh `===== … =====` blocks in `pipeline.log`
- `exit_code=0`
- diagnose: LM/Telegram OK; quiet-channel only if feeds had no SMS-relevant items

Windows Task Scheduler example (WSL):

```text
wsl -d <distro> -- bash -lc 'source ~/.config/telecom-news/env && cd /home/joe/Projects/telecom-news && ./scripts/run_pipeline.sh'
```

## 3. Subscriber bot (private DM path — separate from channel)

Channel posts do **not** need the bot. `/start`, languages and `deliver` do.

```bash
pgrep -af 'telecom_news bot' || echo 'bot not running'
.venv/bin/python -m telecom_news diagnose   # Bot: / Subscribers: lines

# Start (stale lock is auto-removed since prod-MVP fix):
mkdir -p data/logs
nohup .venv/bin/python -m telecom_news bot >> data/logs/bot.log 2>&1 &
sleep 1
pgrep -af 'telecom_news bot'
tail -n 15 data/logs/bot.log
```

After reboot WSL: schedule the same `nohup … bot` line (or `bot --once` from the
pipeline task if you prefer short polls).

### 3a. Real /start (not in the channel)

1. Open a **private** chat with `@sms_telecom_news_bot` (or your bot).
2. Send `/start`, choose language(s), finish.
3. Verify:

```bash
.venv/bin/python - <<'PY'
import sqlite3
con = sqlite3.connect("data/news.db")
print("subscribers:")
for row in con.execute(
    "SELECT chat_id, username, ui_lang, status, last_seen_at FROM subscribers"
):
    print(row)
print("languages:")
for row in con.execute("SELECT chat_id, lang FROM subscriber_languages"):
    print(row)
con.close()
PY
tail -n 20 data/logs/bot.log
```

Expect:

- `last_seen_at` is **today**
- `username` is **your** Telegram nick (or NULL), **not** `sms_telecom_news_bot`
- bot.log shows a private `/start`, not only `channel_post … ignored`

### 3b. Remove a demo / wrong subscriber

Only if diagnose flags `suspicious-subscriber` and you confirmed the chat is not you:

```bash
.venv/bin/python - <<'PY'
import sqlite3
CHAT = "1288967298"  # replace with the bad chat_id from diagnose
con = sqlite3.connect("data/news.db")
con.execute("DELETE FROM subscriber_languages WHERE chat_id = ?", (CHAT,))
con.execute("DELETE FROM subscribers WHERE chat_id = ?", (CHAT,))
con.commit()
print("removed", CHAT)
con.close()
PY
# then /start again in the private chat
```

### 3c. Dry-run private delivery

```bash
.venv/bin/python -m telecom_news deliver --dry-run --limit 3
```

## 4. Accept “quiet channel” when it is the filter

If diagnose says articles were stored and **all skipped as irrelevant**, the
pipeline is healthy: feeds had no SMS/messaging signal in title/feed text.
Options later (separate decision): wait, D-012 bypass for narrow feeds, or
trim noisy sources — **not** a publish outage.

## 5. Done criteria (prod MVP)

| Check | Command / signal |
|---|---|
| Code on master (or tracking branch) | `git log -1`, PR #6 merged |
| doctor green | `doctor` |
| scheduler alive | fresh `pipeline.log` |
| diagnose non-blocking for infra | no BLOCK on telegram/llm/stale-runs |
| bot process up | `pgrep` + diagnose `Bot: running` |
| real subscriber | not bot username; languages ≥ 1 |
| channel | last publication recent **or** quiet-channel = filter only |
| optional GUI | `serve` on `:8765` — nice-to-have, not required for MVP |

## 6. NOT this checklist

- Full multi-project GUI (§§40–70 / M9b+)
- Auth on the API
- Post text quality (title translation, image URLs) — separate fix
- Inventing AI cost numbers
