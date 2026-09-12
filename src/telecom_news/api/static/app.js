/* Minimal Global Dashboard + Project Switcher (VISION §§41–42). */

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status} ${text}`);
  }
  return response.json();
}

function el(id) {
  return document.getElementById(id);
}

function fmt(value) {
  if (value === null || value === undefined) return "—";
  return String(value);
}

function renderKpis(dash) {
  const items = [
    ["Projects", dash.projects_total],
    ["Running", dash.projects_running],
    ["Problems", dash.projects_problems],
    ["Queue", dash.queue_total],
    ["Published", dash.published_total],
    ["Sources (declared)", dash.sources_declared_total],
  ];
  el("kpis").innerHTML = items
    .map(
      ([label, value]) =>
        `<div class="kpi"><div class="label">${label}</div><div class="value">${fmt(value)}</div></div>`
    )
    .join("");
  const banner = el("pause-banner");
  if (dash.global_publish_paused) {
    banner.hidden = false;
    banner.textContent = "GLOBAL PUBLISH PAUSED — no project should publish until resumed.";
  } else {
    banner.hidden = true;
  }
}

function statusBadge(project) {
  if (project.publish_paused) return '<span class="badge paused">paused</span>';
  const st = project.status || "running";
  const cls = st === "running" ? "running" : st === "error" ? "error" : "warn";
  return `<span class="badge ${cls}">${st}</span>`;
}

function renderCards(dash) {
  el("cards").innerHTML = (dash.projects || [])
    .map(
      (p) => `
    <article class="card" data-id="${p.id}">
      <h2>${escapeHtml(p.name)} ${statusBadge(p)}</h2>
      <div class="row"><span>Queue</span><span>${fmt(p.queue)}</span></div>
      <div class="row"><span>Published</span><span>${fmt(p.published)}</span></div>
      <div class="row"><span>Sources</span><span>${fmt(p.source_ids_count)}</span></div>
      <div class="row"><span>Channels</span><span>${fmt(p.channels_count)}</span></div>
      <div class="row"><span>id</span><span>${escapeHtml(p.id)}</span></div>
    </article>`
    )
    .join("");
  el("cards").querySelectorAll(".card").forEach((card) => {
    card.addEventListener("click", () => selectProject(card.dataset.id));
  });
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fillSwitcher(projects) {
  const select = el("project-select");
  const current = select.value;
  select.innerHTML =
    `<option value="">All Projects</option>` +
    projects
      .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}</option>`)
      .join("");
  if (current) select.value = current;
  select.onchange = () => {
    if (!select.value) {
      el("detail").hidden = true;
      el("global").hidden = false;
      return;
    }
    selectProject(select.value);
  };
}

async function selectProject(id) {
  el("project-select").value = id;
  const data = await fetchJson(`/api/projects/${encodeURIComponent(id)}`);
  el("global").hidden = true;
  el("detail").hidden = false;
  el("detail-title").textContent = data.name || id;
  el("detail-body").textContent = JSON.stringify(data, null, 2);
}

async function refresh() {
  try {
    const [health, dash, list] = await Promise.all([
      fetchJson("/api/health"),
      fetchJson("/api/dashboard"),
      fetchJson("/api/projects"),
    ]);
    el("health").textContent = health.global_publish_paused
      ? "API · PUBLISH PAUSED"
      : "API · OK";
    el("health").style.color = health.global_publish_paused ? "var(--warn)" : "var(--ok)";
    renderKpis(dash);
    renderCards(dash);
    fillSwitcher(list.projects || []);
  } catch (err) {
    el("health").textContent = "API error";
    el("health").style.color = "var(--bad)";
    el("kpis").innerHTML = `<p class="muted">${escapeHtml(err.message)}</p>`;
  }
}

refresh();
setInterval(refresh, 15000);
