/* M9c control panel: overview pause, run-now, queue, sources, AI rules. */

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
  if (name === "sources") refreshSources();
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
      return `<tr data-id="${escapeHtml(s.id)}">
        <td><button type="button" class="toggle ${on ? "on" : ""}" title="toggle" data-enabled="${on}"></button></td>
        <td>${escapeHtml(s.id)}</td>
        <td>${escapeHtml(s.language)}</td>
        <td>${escapeHtml(s.relevance_gate)}</td>
        <td class="title"><a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.url)}</a></td>
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
el("btn-refresh-sources").onclick = () => refreshSources();
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

refreshAll();
setInterval(() => {
  if (!el("panel-overview").hidden) refreshAll();
}, 20000);
