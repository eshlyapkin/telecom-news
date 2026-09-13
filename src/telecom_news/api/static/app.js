/* M9d control panel: overview pause, run-now, queue, sources add/delete, AI rules. */

let currentProjectId = "";
let statusCache = null;
let runPollTimer = null;

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!response.ok) {
    const detail = (data && data.detail) || text || response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function el(id) {
  return document.getElementById(id);
}

function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmt(value) {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function setHealth(ok, label) {
  const node = el("health");
  node.textContent = label;
  node.style.color = ok ? "var(--ok)" : "var(--bad)";
}

function showTab(name) {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.hidden = panel.id !== `panel-${name}`;
  });
  if (name === "queue") refreshQueue();
  if (name === "published") refreshPublished();
  if (name === "sources") refreshSources();
  if (name === "discovery") {
    refreshCandidates();
    refreshQueries();
    refreshHistory();
  }
  if (name === "ai-rules") refreshRules();
}

function renderKpis(status, dash) {
  const items = [
    ["Queue", status.counts ? (status.counts.new || 0) + (status.counts.processed || 0) : dash.queue_total],
    ["New", status.counts?.new],
    ["Processed", status.counts?.processed],
    ["Published", status.counts?.published ?? dash.published_total],
    ["Skipped", status.counts?.skipped],
    ["Sources on", `${status.sources_enabled}/${status.sources_total}`],
    ["Subscribers", status.subscribers_active_with_lang],
  ];
  el("kpis").innerHTML = items
    .map(
      ([label, value]) =>
        `<div class="kpi"><div class="label">${escapeHtml(label)}</div><div class="value">${escapeHtml(fmt(value))}</div></div>`
    )
    .join("");
}

function renderRun(run) {
  if (!run) return;
  const banner = el("run-banner");
  const running = run.status === "running";
  el("btn-run-now").disabled = running;
  el("btn-run-submit").disabled = running;
  if (running) {
    banner.hidden = false;
    banner.className = "banner";
    banner.textContent = `Pipeline RUNNING (${run.stage}) since ${run.started_at || "…"}`;
  } else if (run.status === "error") {
    banner.hidden = false;
    banner.className = "banner bad";
    banner.textContent = `Last run ERROR (exit ${run.exit_code}) ${run.error || ""}`.trim();
  } else if (run.status === "ok") {
    banner.hidden = false;
    banner.className = "banner ok";
    banner.textContent = `Last run OK · ${run.stage} · finished ${run.finished_at || ""}`;
  } else {
    banner.hidden = true;
  }

  el("run-status").innerHTML = [
    ["Status", run.status],
    ["Stage", run.stage],
    ["Started", run.started_at],
    ["Finished", run.finished_at],
    ["Exit", run.exit_code],
  ]
    .map(
      ([k, v]) =>
        `<div><span>${escapeHtml(k)}</span><span>${escapeHtml(fmt(v))}</span></div>`
    )
    .join("");

  const log = el("run-log");
  if (run.log_tail) {
    log.hidden = false;
    log.textContent = run.log_tail;
  } else if (!running) {
    log.hidden = true;
    log.textContent = "";
  }

  if (running && !runPollTimer) {
    runPollTimer = setInterval(async () => {
      try {
        const s = await fetchJson("/api/run/status");
        renderRun(s);
        if (s.status !== "running") {
          clearInterval(runPollTimer);
          runPollTimer = null;
          refreshAll();
          refreshQueue();
        }
      } catch {
        /* ignore transient */
      }
    }, 1500);
  }
}

function renderStatus(status) {
  const bot =
    status.bot_lock_stale
      ? "STALE LOCK"
      : status.bot_running
        ? `running (${status.bot_detail || "ok"})`
        : `down (${status.bot_detail || "—"})`;
  const rows = [
    ["Project", status.project_id],
    ["Publish", status.publish_effectively_paused ? "PAUSED" : "on"],
    ["Project pause", status.project_publish_paused ? "yes" : "no"],
    ["Global pause", status.global_publish_paused ? "yes" : "no"],
    ["Bot", bot],
    ["Telegram cfg", status.telegram_configured ? "yes" : "missing"],
    ["Langs", (status.target_langs || []).join(", ") || "—"],
    ["Pipeline log", status.pipeline_log_mtime || "—"],
  ];
  el("status-body").innerHTML = rows
    .map(
      ([k, v]) =>
        `<div><span>${escapeHtml(k)}</span><span>${escapeHtml(fmt(v))}</span></div>`
    )
    .join("");

  const last = status.last_published;
  el("last-pub").innerHTML = last
    ? `<div><span>When</span><span>${escapeHtml(fmt(last.at))}</span></div>
       <div><span>Id</span><span>${escapeHtml(fmt(last.id))}</span></div>
       <div><span>Title</span><span>${escapeHtml(fmt(last.title))}</span></div>`
    : `<div class="muted">Never published in this DB.</div>`;

  const banner = el("pause-banner");
  if (status.publish_effectively_paused) {
    banner.hidden = false;
    banner.className = "banner bad";
    banner.textContent = status.global_publish_paused
      ? "GLOBAL PUBLISH PAUSED — channel posts are blocked."
      : "Project publish paused — channel posts are blocked for this project.";
  } else {
    banner.hidden = true;
  }

  el("btn-pause-project").hidden = !!status.project_publish_paused;
  el("btn-resume-project").hidden = !status.project_publish_paused;
  el("btn-pause-global").hidden = !!status.global_publish_paused;
  el("btn-resume-global").hidden = !status.global_publish_paused;

  renderRun(status.run || { status: "idle" });
}

/* Channel languages: a live override of TELECOM_NEWS_TARGET_LANGS. Every process
   rebuilds its config, so Apply reaches the next pipeline cycle on its own. */
const LANG_FLAGS = { ru: "🇷🇺", en: "🇬🇧" };

async function refreshLanguages() {
  try {
    const data = await fetchJson("/api/channel-languages");
    const active = new Set(data.langs || []);
    el("lang-choices").innerHTML = (data.supported || [])
      .map(
        (lang) => `<label class="check">
          <input type="checkbox" class="lang-box" value="${escapeHtml(lang)}"
            ${active.has(lang) ? "checked" : ""} />
          ${LANG_FLAGS[lang] || ""} ${escapeHtml(lang)}
        </label>`
      )
      .join("");
    el("lang-targets").innerHTML = (data.channel_targets || [])
      .map(
        (t) =>
          `<div><span>${escapeHtml(t.lang)}</span><span>${escapeHtml(t.chat_id)}</span></div>`
      )
      .join("");
    const unreachable = data.unreachable || [];
    el("lang-status").textContent =
      (data.source === "panel" ? "Set in this panel." : "Following the env file.") +
      (unreachable.length
        ? ` No chat id for: ${unreachable.join(", ")} — those posts cannot be sent.`
        : "");
  } catch (err) {
    el("lang-status").textContent = err.message;
  }
}

async function saveLanguages() {
  const langs = [...document.querySelectorAll(".lang-box:checked")].map((b) => b.value);
  if (!langs.length) {
    el("lang-status").textContent = "Pick at least one language.";
    return;
  }
  await fetchJson("/api/channel-languages", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ langs }),
  });
  await refreshLanguages();
  await refreshAll();
}

async function resetLanguages() {
  await fetchJson("/api/channel-languages", { method: "DELETE" });
  await refreshLanguages();
  await refreshAll();
}

function renderCards(dash) {
  el("project-cards").innerHTML = (dash.projects || [])
    .map((p) => {
      const badge = p.publish_paused
        ? '<span class="badge paused">paused</span>'
        : `<span class="badge ${p.status === "running" ? "running" : "warn"}">${escapeHtml(p.status)}</span>`;
      return `<article class="card clickable" data-id="${escapeHtml(p.id)}">
        <h2>${escapeHtml(p.name)} ${badge}</h2>
        <div class="kv">
          <div><span>Queue</span><span>${escapeHtml(fmt(p.queue))}</span></div>
          <div><span>Published</span><span>${escapeHtml(fmt(p.published))}</span></div>
          <div><span>id</span><span>${escapeHtml(p.id)}</span></div>
        </div>
      </article>`;
    })
    .join("");
  el("project-cards").querySelectorAll(".card").forEach((card) => {
    card.addEventListener("click", () => {
      el("project-select").value = card.dataset.id;
      currentProjectId = card.dataset.id;
      refreshAll();
    });
  });
}

function fillSwitcher(projects) {
  const select = el("project-select");
  const keep = currentProjectId || select.value;
  select.innerHTML = projects
    .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}</option>`)
    .join("");
  if (keep && projects.some((p) => p.id === keep)) {
    select.value = keep;
  } else if (projects[0]) {
    select.value = projects[0].id;
  }
  currentProjectId = select.value;
  select.onchange = () => {
    currentProjectId = select.value;
    refreshAll();
  };
}

async function refreshAll() {
  try {
    const [dash, list] = await Promise.all([
      fetchJson("/api/dashboard"),
      fetchJson("/api/projects"),
    ]);
    fillSwitcher(list.projects || []);
    if (!currentProjectId && list.projects?.[0]) {
      currentProjectId = list.projects[0].id;
      el("project-select").value = currentProjectId;
    }
    const status = await fetchJson(
      `/api/ops/status?project_id=${encodeURIComponent(currentProjectId || "")}`
    );
    statusCache = status;
    el("overview-title").textContent = status.project_id || "Overview";
    renderKpis(status, dash);
    renderStatus(status);
    renderCards(dash);
    refreshLanguages();
    const runBusy = status.run && status.run.status === "running";
    setHealth(
      !status.publish_effectively_paused && !runBusy,
      runBusy ? "RUNNING" : status.publish_effectively_paused ? "PAUSED" : "API · OK"
    );
  } catch (err) {
    setHealth(false, "API error");
    el("kpis").innerHTML = `<p class="muted">${escapeHtml(err.message)}</p>`;
  }
}

function fillArticleTable(tableId, rows) {
  const tbody = el(tableId).querySelector("tbody");
  if (!rows || !rows.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="muted">Empty</td></tr>`;
    return;
  }
  tbody.innerHTML = rows
    .map(
      (r) => `<tr>
      <td>${escapeHtml(r.id)}</td>
      <td>${escapeHtml(r.source_id)}</td>
      <td class="title"><a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.title || "(no title)")}</a></td>
      <td>${escapeHtml(fmt(r.published_at))}</td>
    </tr>`
    )
    .join("");
}

async function refreshQueue() {
  if (!currentProjectId) return;
  try {
    const data = await fetchJson(
      `/api/projects/${encodeURIComponent(currentProjectId)}/queue?limit=50`
    );
    const c = data.counts || {};
    el("queue-kpis").innerHTML = ["new", "processed", "published", "skipped", "error"]
      .map(
        (k) =>
          `<div class="kpi"><div class="label">${k}</div><div class="value">${escapeHtml(fmt(c[k] || 0))}</div></div>`
      )
      .join("");
    fillArticleTable("table-new", data.new);
    fillArticleTable("table-processed", data.processed);
  } catch (err) {
    el("queue-kpis").innerHTML = `<p class="muted">${escapeHtml(err.message)}</p>`;
  }
}

let publishedCache = [];

/* Published posts: what actually went out, per language. Every field is
   inserted as escaped text — the post body comes from feeds and from the model,
   so it must never reach the page as markup. */
async function refreshPublished() {
  if (!currentProjectId) return;
  try {
    const data = await fetchJson(
      `/api/projects/${encodeURIComponent(currentProjectId)}/published?limit=50`
    );
    publishedCache = data.posts || [];
    el("published-meta").textContent = data.db_exists
      ? `${data.count} published article(s) · channel languages: ${(data.target_langs || []).join(", ")}`
      : "No database for this project yet.";
    renderPublished();
  } catch (err) {
    el("published-list").innerHTML = `<p class="muted">${escapeHtml(err.message)}</p>`;
  }
}

function renderPublished() {
  const q = (el("published-filter").value || "").trim().toLowerCase();
  const posts = publishedCache.filter((p) => {
    if (!q) return true;
    const haystack = [p.title, p.source_id, ...(p.renditions || []).map((r) => r.headline)]
      .join(" ")
      .toLowerCase();
    return haystack.includes(q);
  });
  if (!posts.length) {
    el("published-list").innerHTML = `<p class="muted">Nothing published yet.</p>`;
    return;
  }
  el("published-list").innerHTML = posts
    .map((p) => {
      const renditions = (p.renditions || [])
        .map((r) => {
          const flag = r.lang === "ru" ? "🇷🇺" : r.lang === "en" ? "🇬🇧" : r.lang;
          const badge = r.channel_sent
            ? `<span class="badge running">sent${r.message_id ? ` · id ${escapeHtml(r.message_id)}` : ""}</span>`
            : `<span class="badge warn">not in channel</span>`;
          const subs = r.subscriber_sends
            ? `<span class="badge on">${escapeHtml(r.subscriber_sends)} subscriber(s)</span>`
            : "";
          const failed = (r.failed || []).length
            ? `<span class="badge error">${escapeHtml(r.failed.length)} failed</span>`
            : "";
          return `<div class="rendition">
            <div class="rendition-head">${flag} ${badge} ${subs} ${failed}
              <span class="muted small">${escapeHtml(fmt(r.sent_at))}</span></div>
            <div class="post-preview">
              <strong>${escapeHtml(r.headline)}</strong>
              <p>${escapeHtml(r.summary)}</p>
            </div>
            <details><summary class="muted small">Telegram markup as sent</summary>
              <pre class="log">${escapeHtml(r.telegram_html)}</pre></details>
          </div>`;
        })
        .join("");
      return `<article class="card static published-card">
        <h2>#${escapeHtml(p.id)} · ${escapeHtml(p.source_id)}
          <span class="badge on">${escapeHtml(fmt(p.category))}</span></h2>
        <div class="muted small">
          posted ${escapeHtml(fmt(p.posted_at))} ·
          <a href="${escapeHtml(p.url)}" target="_blank" rel="noopener">original</a>
        </div>
        ${renditions || '<p class="muted small">No rendition stored.</p>'}
      </article>`;
    })
    .join("");
}

let sourcesCache = [];

async function refreshSources() {
  try {
    const data = await fetchJson("/api/sources");
    sourcesCache = data.sources || [];
    renderSources();
  } catch (err) {
    el("table-sources").querySelector("tbody").innerHTML =
      `<tr><td colspan="5" class="muted">${escapeHtml(err.message)}</td></tr>`;
  }
}

function renderSources() {
  const q = (el("source-filter").value || "").trim().toLowerCase();
  const rows = sourcesCache.filter(
    (s) =>
      !q ||
      s.id.toLowerCase().includes(q) ||
      (s.url || "").toLowerCase().includes(q)
  );
  const tbody = el("table-sources").querySelector("tbody");
  tbody.innerHTML = rows
    .map((s) => {
      const on = s.enabled;
      const custom = s.custom ? ' <span class="badge running">custom</span>' : "";
      return `<tr data-id="${escapeHtml(s.id)}" data-custom="${!!s.custom}">
        <td><button type="button" class="toggle ${on ? "on" : ""}" title="toggle" data-enabled="${on}"></button></td>
        <td>${escapeHtml(s.id)}${custom}${s.type === "sitemap" ? ' <span class="badge warn">sitemap</span>' : ""}</td>
        <td>${escapeHtml(s.language)}</td>
        <td>${escapeHtml(s.relevance_gate)}</td>
        <td class="title"><a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.url)}</a></td>
        <td><button type="button" class="btn danger small-btn source-delete" title="delete source">✕</button></td>
      </tr>`;
    })
    .join("");
  tbody.querySelectorAll(".toggle").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const tr = btn.closest("tr");
      const id = tr.dataset.id;
      const next = btn.dataset.enabled !== "true";
      btn.disabled = true;
      try {
        await fetchJson(`/api/sources/${encodeURIComponent(id)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: next }),
        });
        await refreshSources();
        await refreshAll();
      } catch (err) {
        alert(err.message);
        btn.disabled = false;
      }
    });
  });
  tbody.querySelectorAll(".source-delete").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const tr = btn.closest("tr");
      const id = tr.dataset.id;
      const builtIn = tr.dataset.custom !== "true";
      const note = builtIn
        ? "It is a built-in feed: the delete is stored as an overlay and adding the id back restores it."
        : "It was added here, so the entry is removed for good.";
      if (!confirm(`Delete source "${id}"?\n\n${note}`)) return;
      btn.disabled = true;
      try {
        await fetchJson(`/api/sources/${encodeURIComponent(id)}`, { method: "DELETE" });
        await refreshSources();
        await refreshAll();
      } catch (err) {
        alert(err.message);
        btn.disabled = false;
      }
    });
  });
}

async function addSource() {
  const url = (el("src-url").value || "").trim();
  const status = el("src-add-status");
  if (!url) {
    status.textContent = "Feed URL is required.";
    return;
  }
  const body = {
    url,
    type: el("src-type").value || "rss",
    language: el("src-lang").value || "en",
    relevance_gate: el("src-gate").value || "strict",
  };
  const id = (el("src-id").value || "").trim();
  if (id) body.id = id;
  const created = await fetchJson("/api/sources", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  el("src-url").value = "";
  el("src-id").value = "";
  status.textContent = `Added "${created.id}". Run collect to pull its first items.`;
  await refreshSources();
  await refreshAll();
}

/* Discovery: proposals only. Accepting one calls the same API the Sources tab
   uses, so a candidate becomes an ordinary custom source. */
/* Search queries are split on newlines only: a query may legitimately contain a
   comma, unlike the AI-rules term lists. */
function textToLines(text) {
  return String(text || "")
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

async function refreshQueries() {
  try {
    const data = await fetchJson("/api/discovery-queries");
    el("discover-queries").value = (data.queries || []).join("\n");
    const origin =
      data.source === "ai-rules"
        ? `${data.count} topic(s) generated from your AI rules`
        : `${data.count} custom topic(s) — the AI rules would give ${(data.from_rules || []).length}`;
    const next = (data.next_batch || []).map((q) => `· ${q}`).join("\n");
    el("queries-status").textContent =
      `${origin}. Each scan searches ${data.per_scan}, continuing where the last one stopped.` +
      (next ? `\nNext scan:\n${next}` : "");
  } catch (err) {
    el("queries-status").textContent = err.message;
  }
}

async function saveQueries(queries) {
  const data = await fetchJson("/api/discovery-queries", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ queries }),
  });
  el("discover-queries").value = (data.queries || []).join("\n");
  el("queries-status").textContent = queries.length
    ? "Topics saved. They apply to the next scan."
    : "Back to the topics generated from your AI rules.";
}

/* Where the scans have been. A site is skipped until its cooldown expires, so
   this table is also the answer to "will the next scan look somewhere new?". */
async function refreshHistory() {
  try {
    const [data, settings] = await Promise.all([
      fetchJson("/api/discovery-history?limit=200"),
      fetchJson("/api/discovery-settings"),
    ]);
    el("recheck-days").value = settings.recheck_after_days;
    const rule = settings.recheck_after_days
      ? `a site is skipped for ${settings.recheck_after_days} days after a probe`
      : "every site is probed on every scan";
    el("history-meta").textContent = data.count
      ? `${data.count} site(s) probed · ${data.due_for_recheck} due for a re-check (${rule})`
      : `No site has been probed yet (${rule}).`;
    const tbody = el("table-history").querySelector("tbody");
    if (!data.count) {
      tbody.innerHTML = `<tr><td colspan="6" class="muted">Empty</td></tr>`;
      return;
    }
    tbody.innerHTML = (data.checked || [])
      .map((row) => {
        const rate =
          row.hit_rate === null || row.hit_rate === undefined
            ? "—"
            : `${Math.round(row.hit_rate * 100)}%` +
              (row.items ? ` (${escapeHtml(row.hits)}/${escapeHtml(row.items)})` : "");
        const found = row.url
          ? `<a href="${escapeHtml(row.url)}" target="_blank" rel="noopener">${escapeHtml(row.kind || "")}</a>`
          : "—";
        const days = row.days_until_recheck;
        const next = row.due_for_recheck
          ? '<span class="badge warn">due now</span>'
          : `<span class="badge on">in ${escapeHtml(days)} day${days === 1 ? "" : "s"}</span>`;
        return `<tr>
          <td>${escapeHtml(String(row.last_checked_at || "").slice(0, 16))}</td>
          <td>${escapeHtml(row.host)}</td>
          <td>${escapeHtml(row.outcome_label)}</td>
          <td>${rate}</td>
          <td>${found}</td>
          <td>${next}</td>
        </tr>`;
      })
      .join("");
  } catch (err) {
    el("history-meta").textContent = err.message;
  }
}

async function refreshCandidates() {
  try {
    const data = await fetchJson("/api/source-candidates");
    el("candidates-meta").textContent =
      `${data.count} open proposal(s) · ${data.dismissed} dismissed · last scan ${fmt(data.scanned_at)}`;
    const rows = data.candidates || [];
    if (!rows.length) {
      el("candidates-list").innerHTML =
        `<p class="muted">No proposals yet. Run a scan — it needs collected articles to follow links from.</p>`;
      renderCandidateActions();
      return;
    }
    el("candidates-list").innerHTML = rows
      .map((c) => {
        const rate = Math.round((c.hit_rate || 0) * 100);
        const samples = (c.sample_titles || [])
          .map((s) => `<li>${escapeHtml(s)}</li>`)
          .join("");
        return `<article class="card static" data-id="${escapeHtml(c.id)}">
          <h2>${escapeHtml(c.id)}
            <span class="badge ${rate >= 50 ? "running" : "warn"}">${escapeHtml(rate)}% on topic</span>
            <span class="badge on">${escapeHtml(c.language)}</span>
            <span class="badge ${c.type === "sitemap" ? "warn" : "on"}">${escapeHtml(c.type || "rss")}</span></h2>
          <div class="kv">
            <div><span>Feed</span><span><a href="${escapeHtml(c.feed_url)}" target="_blank" rel="noopener">${escapeHtml(c.feed_url)}</a></span></div>
            <div><span>Matched</span><span>${escapeHtml(c.hits)} of ${escapeHtml(c.items)} recent items</span></div>
            <div><span>Found</span><span>${escapeHtml(c.origin)}</span></div>
          </div>
          ${samples ? `<ul class="samples">${samples}</ul>` : ""}
          <div class="actions">
            <button type="button" class="btn ok candidate-add">Add as source</button>
            <button type="button" class="btn danger candidate-dismiss">Dismiss</button>
          </div>
        </article>`;
      })
      .join("");
    renderCandidateActions();
  } catch (err) {
    el("candidates-list").innerHTML = `<p class="muted">${escapeHtml(err.message)}</p>`;
  }
}

function renderCandidateActions() {
  document.querySelectorAll("#candidates-list .card").forEach((card) => {
    const id = card.dataset.id;
    const act = async (path, btn) => {
      btn.disabled = true;
      try {
        await fetchJson(`/api/source-candidates/${encodeURIComponent(id)}/${path}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        await refreshCandidates();
        await refreshSources();
        await refreshAll();
      } catch (err) {
        alert(err.message);
        btn.disabled = false;
      }
    };
    card.querySelector(".candidate-add").addEventListener("click", (e) => act("accept", e.target));
    card
      .querySelector(".candidate-dismiss")
      .addEventListener("click", (e) => act("dismiss", e.target));
  });
}

async function startDiscoveryScan() {
  const banner = el("discover-banner");
  const data = await fetchJson("/api/source-candidates/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      max_sites: Number(el("discover-sites").value) || 12,
      use_search: el("discover-use-search").checked,
      look_for: el("discover-look-for").value || "both",
      recheck: el("discover-recheck").checked,
    }),
  });
  banner.hidden = false;
  banner.className = "banner";
  banner.textContent = "Scanning… this probes external sites and takes a while.";
  renderRun(data);
  const poll = setInterval(async () => {
    try {
      const status = await fetchJson("/api/run/status");
      if (status.status !== "running") {
        clearInterval(poll);
        banner.className = status.status === "ok" ? "banner ok" : "banner bad";
        banner.textContent =
          status.status === "ok" ? "Scan finished." : `Scan failed: ${status.error || ""}`;
        refreshCandidates();
        refreshHistory();
      }
    } catch {
      /* ignore transient */
    }
  }, 2000);
}

function termsToText(list) {
  return (list || []).join("\n");
}

function textToTerms(text) {
  return String(text || "")
    .split(/[\n,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

async function refreshRules() {
  try {
    const data = await fetchJson("/api/ai-rules");
    const r = data.rules || {};
    el("rules-prompt").value = r.system_prompt || "";
    el("rules-messaging").value = termsToText(r.messaging_terms);
    el("rules-offtopic").value = termsToText(r.off_topic_terms);
    el("rules-force-llm").checked = !!r.force_llm_gate;
    el("rules-notes").value = r.notes || "";
    el("rules-meta").textContent = data.overridden
      ? `Override file: ${data.path}`
      : `Using built-in defaults (no file yet). Will write ${data.path} on Save.`;
    const effective = data.effective_system_prompt || "";
    el("rules-effective").textContent = effective
      ? `The classifier receives ${effective.length} characters (policy + contract).`
      : "";
    el("rules-status").textContent = "";
  } catch (err) {
    el("rules-status").textContent = err.message;
  }
}

async function saveRules() {
  const body = {
    system_prompt: el("rules-prompt").value,
    messaging_terms: textToTerms(el("rules-messaging").value),
    off_topic_terms: textToTerms(el("rules-offtopic").value),
    force_llm_gate: el("rules-force-llm").checked,
    notes: el("rules-notes").value,
  };
  await fetchJson("/api/ai-rules", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  el("rules-status").textContent = "Saved.";
  await refreshRules();
}

async function resetRules() {
  if (!confirm("Reset AI rules to built-in defaults and delete ai_rules.json?")) return;
  await fetchJson("/api/ai-rules/reset", { method: "POST" });
  el("rules-status").textContent = "Reset to defaults.";
  await refreshRules();
}

async function pauseProject(paused) {
  if (!currentProjectId) return;
  await fetchJson(`/api/projects/${encodeURIComponent(currentProjectId)}/publish-pause`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paused }),
  });
  await refreshAll();
}

async function pauseGlobal(paused) {
  if (paused && !confirm("Pause publishing for ALL projects?")) return;
  await fetchJson("/api/system/publish-pause", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paused, confirm: paused }),
  });
  await refreshAll();
}

async function startRunNow() {
  if (!currentProjectId) return;
  const limitRaw = el("run-limit").value;
  const body = {
    stage: el("run-stage").value || "full",
    dry_run: el("run-dry").checked,
  };
  if (limitRaw) body.limit = Number(limitRaw);
  const data = await fetchJson(`/api/projects/${encodeURIComponent(currentProjectId)}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  renderRun(data);
  showTab("overview");
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => showTab(btn.dataset.tab));
});
el("btn-refresh").onclick = () => refreshAll();
el("btn-refresh-queue").onclick = () => refreshQueue();
el("btn-save-langs").onclick = () => saveLanguages().catch((e) => alert(e.message));
el("btn-reset-langs").onclick = () => resetLanguages().catch((e) => alert(e.message));
el("btn-refresh-published").onclick = () => refreshPublished();
el("published-filter").oninput = () => renderPublished();
el("btn-refresh-sources").onclick = () => refreshSources();
async function saveRecheckDays() {
  const value = Number(el("recheck-days").value);
  await fetchJson("/api/discovery-settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ recheck_after_days: value }),
  });
  await refreshHistory();
}

el("btn-save-recheck").onclick = () => saveRecheckDays().catch((e) => alert(e.message));
el("btn-refresh-candidates").onclick = () => {
  refreshCandidates();
  refreshHistory();
};
el("btn-discover-scan").onclick = () => startDiscoveryScan().catch((e) => alert(e.message));
el("btn-save-queries").onclick = () =>
  saveQueries(textToLines(el("discover-queries").value)).catch((e) => alert(e.message));
el("btn-reset-queries").onclick = () => saveQueries([]).catch((e) => alert(e.message));
el("btn-refresh-rules").onclick = () => refreshRules();
el("btn-save-rules").onclick = () => saveRules().catch((e) => alert(e.message));
el("btn-reset-rules").onclick = () => resetRules().catch((e) => alert(e.message));
el("btn-pause-project").onclick = () => pauseProject(true).catch((e) => alert(e.message));
el("btn-resume-project").onclick = () => pauseProject(false).catch((e) => alert(e.message));
el("btn-pause-global").onclick = () => pauseGlobal(true).catch((e) => alert(e.message));
el("btn-resume-global").onclick = () => pauseGlobal(false).catch((e) => alert(e.message));
el("btn-run-now").onclick = () => {
  el("run-stage").focus();
  window.scrollTo({ top: el("run-stage").offsetTop - 80, behavior: "smooth" });
};
el("btn-run-submit").onclick = () => startRunNow().catch((e) => alert(e.message));
el("source-filter").oninput = () => renderSources();
el("btn-add-source").onclick = () =>
  addSource().catch((e) => {
    el("src-add-status").textContent = e.message;
  });

refreshAll();
setInterval(() => {
  if (!el("panel-overview").hidden) refreshAll();
}, 20000);
