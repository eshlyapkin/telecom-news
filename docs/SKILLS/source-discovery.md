# Source discovery — proposing new sources without adding them

Discovery never changes the source registry. It writes proposals; accepting one
is a human action, in the Discovery tab or `discover --accept <id>`.

```bash
.venv/bin/python -m telecom_news discover              # one pass
.venv/bin/python -m telecom_news discover --list       # open proposals
.venv/bin/python -m telecom_news discover --history    # where it has been
.venv/bin/python -m telecom_news discover --accept <id>
.venv/bin/python -m telecom_news discover --dismiss <id>
```

Scheduled: `scripts/run_discovery.sh` (own lock, own log in
`data/logs/discovery.log`, `TELECOM_NEWS_DISCOVER_SITES` for breadth). **Daily,
not hourly** — a 25-site pass takes about two minutes and the link neighbourhood
moves slowly.

## How a site is found

Two seeds, search first:

1. **Topical news search.** The topics come from the messaging terms of your AI
   rules (D-027), OR-ed in groups; a scan runs the next few and remembers where it
   stopped, so consecutive scans cover the whole rule set. Only the **publisher**
   is taken from a result — its links are consent-walled redirects with no article
   text (D-025).
2. **Outbound links** of articles already collected, ranked by how many distinct
   articles cite a host.

## How a site is judged

The share of recent **headlines** that pass your keyword gate — the same rules
that later decide what gets published. Headlines, not full text: an ecommerce
email-marketing blog scores 90% on full text and 10% on headlines, and its
headlines are about Shopify themes (D-024).

A site with no usable feed is checked for a dated news sitemap, which `collect`
can read as a source of its own (D-026). **Look for** narrows the scan to one
kind.

## Repeat scans

Every probe is recorded per host: when, the outcome (`proposed`, `off_topic` with
the score that lost, `nothing_found`, `unreachable`) and what was found.

A host probed within the re-check period is skipped, so a repeat scan reaches
sites nobody has looked at; when the period expires it comes back, so an outlet
that starts publishing a feed is not lost forever. The **next check** column shows
which applies.

```bash
.venv/bin/python -m telecom_news discover --set-recheck-days 14   # 0 = never skip
.venv/bin/python -m telecom_news discover --recheck               # ignore it once
```

## Reading a rejection

`--history` is where "why is this site not proposed?" is answered:

```
14:55   10%   in 30d   omnisend.com    found a feed, but its headlines are off topic
14:42    -    in 30d   terrapinn.com   site did not answer
```

## Limits, stated plainly

- The news search is not a documented API. If it stops answering, a scan degrades
  to link-following instead of failing; `--no-search` forces that.
- Outlets whose sitemap carries no dates cannot be read at all.
- Sites behind a WAF (403 on everything) are recorded as unreachable and retried
  after the cooldown.
