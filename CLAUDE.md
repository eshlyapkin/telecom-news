# CLAUDE.md

The working rules for this project live in **[AGENTS.md](AGENTS.md)** — read it
first. It is written for any AI assistant, not just Claude, so there is one set
of rules rather than two that can drift apart.

Short version, so this file is useful on its own:

1. Orient with `docs/CURRENT.md` (the STATE section at the top), then
   `git log -3 --oneline` and `git status -sb`.
2. `data/` on this machine is the operator's live configuration and article
   database. Never edit it to test something — point `TELECOM_NEWS_DATA_DIR` at a
   scratch directory.
3. `pytest -q` and `git push` work as-is; the hooks in `.githooks/` run the
   checks. Do not bypass them with `--no-verify`.
4. Publishing to the channel, editing or deleting channel messages, and pushing
   to GitHub are visible to other people: confirm first.
5. Claims rest on command output and file contents. Anything else is marked
   NOT VERIFIED.
