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

## Writing a topic by hand

Only needed when you want something the AI rules do not express — otherwise leave
the box alone and edit the rules instead.

**One line = one search request.** Blank lines are dropped, duplicates are
removed, at most 40 lines. An empty box goes back to the topics generated from
the rules.

| Write | Meaning |
|---|---|
| `"A2P SMS"` | the phrase as a whole |
| `"SMS firewall" OR "SMS fraud" OR smishing` | any of the three, one request |
| `SMS firewall` | both words, not necessarily together — broader |
| `смс мошенничество операторы` | Cyrillic anywhere on the line → Russian-language press |

Keep a line specific. Measured against the live search: `"A2P SMS"` returned 86
articles from 61 publishers, all on topic; a bare `sms` returned 100 articles
whose first hit was a high-school sports round-up. The scan only takes the
publisher from each result, so a noisy line wastes a request and fills the probe
queue with irrelevant sites.

Quotes and OR are the operators worth using. There is no syntax of our own: the
line is passed to the news search as typed.

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

## The threshold is yours to set

How much of a feed must be on topic before it is proposed lives next to the
re-check period, in the panel and in `data/discovery_settings.json`. The default
is 25%.

```bash
.venv/bin/python -m telecom_news discover --set-min-hit-rate 0.2  # stored
.venv/bin/python -m telecom_news discover --min-hit-rate 0.1      # this scan only
```

Lower it when the beat you want is covered by outlets that write about it in
one headline out of five. `tcpaworld.com` — US court and FCC rulings on text
marketing, exactly the regulation the channel wants — scores 21% and was refused
by the default until the threshold moved to 20% (2026-09-16).

Lower it too far and the pool fills with general news: at 10% the near misses
were `theguardian.com`, `appuals.com` and a comments feed. Read `--history`
before choosing, then accept by hand.

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
  after the cooldown. A full browser header set does not help: `capacitymedia.com`,
  `telecoms.com`, `mobileecosystemforum.com`, `commsbusiness.co.uk` and
  `mobilemarketingmagazine.com` refuse every automated request (checked
  2026-09-16). Several of the best messaging outlets simply cannot be read, and
  no setting changes that.
- A 403 from the **home page** no longer hides the feed: the browser-User-Agent
  retry used to be skipped entirely on a probe, because the switch spent the one
  attempt a probe is allowed. `mobileworldlive.com` was invisible for that reason
  alone (fixed 2026-09-16).
