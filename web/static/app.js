"use strict";

const SETTINGS_KEY = "assetMonitor.settings.v1";
const DEFAULT_SETTINGS = {
  db: "data/assets.db",
  threads: 50,
  timeout: 10,
  probe: false,
  verbose: false,
};
const PHASE_ORDER = ["discovery", "resolve", "probe", "save", "notify"];
const PHASE_WEIGHTS = {
  discovery: 15,
  resolve: 40,
  probe: 30,
  save: 10,
  notify: 5,
};
const PHASE_LABELS = {
  discovery: "Discovering subdomains",
  resolve: "Resolving DNS",
  probe: "Probing web services",
  save: "Updating state",
  notify: "Sending notifications",
};
const EVENT_TYPES = [
  "status",
  "phase",
  "source",
  "progress",
  "asset",
  "probe",
  "log",
  "notification",
  "done",
  "error",
  "cancelled",
];

const state = {
  settings: loadSettings(),
  jobId: null,
  status: "idle",
  terminal: true,
  es: null,
  assets: new Map(),
  rows: new Map(),
  newHostnames: new Set(),
  notificationMessages: new Set(),
  startTime: null,
  timer: null,
  fallbackTimer: null,
  lastEventId: 0,
  sortKey: "hostname",
  sortDirection: 1,
  phaseFractions: {},
  probeEnabled: false,
  notificationsEnabled: false,
  activePhase: null,
  sourceCount: 0,
  logCount: 0,
  filter: "",
  historySearchTimer: null,
};

const $ = (id) => document.getElementById(id);

function loadSettings() {
  try {
    const stored = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
    const merged = { ...DEFAULT_SETTINGS, ...stored };
    merged.db =
      typeof merged.db === "string" && merged.db.trim()
        ? merged.db
        : DEFAULT_SETTINGS.db;
    merged.threads = Number(merged.threads);
    merged.timeout = Number(merged.timeout);
    if (!Number.isFinite(merged.threads) || merged.threads < 1) {
      merged.threads = DEFAULT_SETTINGS.threads;
    }
    if (!Number.isFinite(merged.timeout) || merged.timeout <= 0) {
      merged.timeout = DEFAULT_SETTINGS.timeout;
    }
    merged.probe = Boolean(merged.probe);
    merged.verbose = Boolean(merged.verbose);
    return merged;
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

function persistSettings() {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(state.settings));
  } catch {
    // Local storage may be unavailable; settings still apply for this session.
  }
}

function syncSettingsToForms() {
  $("threads").value = state.settings.threads;
  $("timeout").value = state.settings.timeout;
  $("db").value = state.settings.db;
  $("probe").checked = state.settings.probe;
  $("verbose").checked = state.settings.verbose;

  $("settings-db").value = state.settings.db;
  $("settings-threads").value = state.settings.threads;
  $("settings-timeout").value = state.settings.timeout;
  $("settings-probe").checked = state.settings.probe;
  $("settings-verbose").checked = state.settings.verbose;
  $("history-db").textContent = state.settings.db;
}

async function api(path, options = {}) {
  const headers = {
    Accept: "application/json",
    ...(options.headers || {}),
  };
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    throw new Error(
      formatError(payload) || `Request failed with status ${response.status}`,
    );
  }
  if (response.status === 204) {
    return null;
  }
  const contentType = response.headers.get("content-type") || "";
  return contentType.includes("application/json")
    ? response.json()
    : response.text();
}

function formatError(payload) {
  if (!payload) {
    return "";
  }
  if (typeof payload.detail === "string") {
    return payload.detail;
  }
  if (Array.isArray(payload.detail)) {
    return payload.detail
      .map((item) => item.msg || JSON.stringify(item))
      .join("; ");
  }
  return JSON.stringify(payload);
}

function toast(message, type = "info") {
  const container = $("toasts");
  const element = document.createElement("div");
  element.className = `toast toast-${type}`;
  element.textContent = message;
  container.append(element);
  window.setTimeout(() => {
    element.style.opacity = "0";
    element.style.transform = "translateY(6px)";
    window.setTimeout(() => element.remove(), 180);
  }, 6000);
}

function setConnection(mode) {
  const connection = $("connection");
  const labels = {
    online: "Online",
    offline: "Offline",
    live: "Live",
    reconnecting: "Reconnecting",
  };
  connection.className = `connection connection-${mode}`;
  $("connection-text").textContent = labels[mode] || mode;
}

async function checkHealth() {
  try {
    const data = await api("/api/health");
    $("version").textContent = `v${data.version}`;
    if (!state.es) {
      setConnection("online");
    }
  } catch {
    setConnection("offline");
  }
}

function isTerminal(status) {
  return ["completed", "failed", "cancelled"].includes(status);
}

function setStatus(status) {
  state.status = status;
  const labels = {
    idle: "Idle",
    queued: "Queued",
    running: "Running",
    completed: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
  };
  const badge = $("job-status");
  badge.className = `badge badge-${status}`;
  badge.textContent = labels[status] || status;
}

function formatDuration(seconds) {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function startElapsed() {
  stopElapsed();
  const update = () => {
    const start = state.startTime || Date.now();
    const seconds = Math.max(0, Math.floor((Date.now() - start) / 1000));
    $("stat-elapsed").textContent = formatDuration(seconds);
  };
  update();
  state.timer = window.setInterval(update, 1000);
}

function stopElapsed() {
  if (state.timer) {
    window.clearInterval(state.timer);
    state.timer = null;
  }
}

function startFallbackPolling() {
  if (state.fallbackTimer) {
    return;
  }
  state.fallbackTimer = window.setInterval(async () => {
    if (!state.jobId || state.terminal) {
      stopFallbackPolling();
      return;
    }
    try {
      const snapshot = await api(`/api/scans/${state.jobId}`);
      if (state.terminal) {
        stopFallbackPolling();
        return;
      }
      if (isTerminal(snapshot.status)) {
        state.terminal = true;
        closeStream();
        stopFallbackPolling();
        applySnapshot(snapshot);
        toast(
          snapshot.status === "completed"
            ? "Scan completed."
            : snapshot.status === "cancelled"
              ? "Scan cancelled."
              : snapshot.error || "Scan failed.",
          snapshot.status === "completed"
            ? "success"
            : snapshot.status === "cancelled"
              ? "warning"
              : "error",
        );
        if ($("view-history").classList.contains("active")) {
          loadHistory();
        }
      } else {
        setStatus(snapshot.status);
      }
    } catch {
      // Keep retrying until the server returns or the stream reconnects.
    }
  }, 2000);
}

function stopFallbackPolling() {
  if (state.fallbackTimer) {
    window.clearInterval(state.fallbackTimer);
    state.fallbackTimer = null;
  }
}

function formatTime(value) {
  if (!value) {
    return "—";
  }
  let date;
  if (typeof value === "number") {
    date = new Date(value * 1000);
  } else {
    const normalized = value.includes("T")
      ? value
      : `${value.replace(" ", "T")}Z`;
    date = new Date(normalized);
  }
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString();
}

function showView(view) {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  document.querySelectorAll(".view").forEach((section) => {
    section.classList.toggle("active", section.id === `view-${view}`);
  });
  if (view === "history") {
    loadHistory();
  }
}

function resetPhases() {
  state.phaseFractions = {};
  PHASE_ORDER.forEach((phase) => {
    state.phaseFractions[phase] = 0;
    const item = document.querySelector(
      `.phases li[data-phase="${phase}"]`,
    );
    if (item) {
      item.dataset.state = "pending";
      const detail = item.querySelector("small");
      if (detail) {
        detail.textContent = "pending";
      }
    }
  });
  state.activePhase = null;
  updateOverallProgress();
}

function setPhase(phase, phaseState, detail) {
  const item = document.querySelector(
    `.phases li[data-phase="${phase}"]`,
  );
  if (!item) {
    return;
  }
  item.dataset.state = phaseState;
  const label = item.querySelector("small");
  if (label) {
    label.textContent = detail;
  }
}

function markPhaseSkipped(phase) {
  if (state.phaseFractions[phase] === 1) {
    return;
  }
  state.phaseFractions[phase] = 1;
  setPhase(phase, "skipped", "skipped");
  updateOverallProgress();
}

function phaseDetail(phase, data) {
  if (phase === "discovery") {
    return `${data.count ?? 0} names`;
  }
  if (phase === "resolve") {
    return `${data.count ?? 0} active`;
  }
  if (phase === "probe") {
    return `${data.count ?? 0} responses`;
  }
  if (phase === "save") {
    return `${data.new ?? 0} new`;
  }
  if (phase === "notify") {
    return `${data.sent ?? 0} sent`;
  }
  return "done";
}

function updateOverallProgress() {
  let value = 0;
  PHASE_ORDER.forEach((phase) => {
    const fraction = state.phaseFractions[phase] || 0;
    value += (PHASE_WEIGHTS[phase] || 0) * fraction;
  });
  setProgress(Math.min(100, value));
}

function setProgress(value) {
  const clamped = Math.max(0, Math.min(100, value));
  const bar = $("progress-bar");
  bar.style.width = `${clamped}%`;
  $("progress").setAttribute("aria-valuenow", String(Math.round(clamped)));
}

function setProgressLabel(phase) {
  $("progress-label").textContent = PHASE_LABELS[phase] || "Working";
}

function handlePhase(data) {
  const phase = data.phase;
  if (!PHASE_ORDER.includes(phase)) {
    return;
  }
  if (data.status === "running") {
    state.activePhase = phase;
    setPhase(phase, "running", "running");
    setProgressLabel(phase);
    if (data.total !== undefined) {
      state.phaseFractions[phase] = Number(data.total) === 0 ? 1 : 0;
      updateOverallProgress();
    }
    return;
  }
  if (data.status === "completed") {
    state.phaseFractions[phase] = 1;
    setPhase(phase, "completed", phaseDetail(phase, data));
    updateOverallProgress();
    if (phase === "resolve" && !state.probeEnabled) {
      markPhaseSkipped("probe");
    }
    if (phase === "save" && !state.notificationsEnabled) {
      markPhaseSkipped("notify");
    }
  }
}

function handleProgress(data) {
  const phase = data.phase;
  if (!PHASE_ORDER.includes(phase)) {
    return;
  }
  const completed = Number(data.completed) || 0;
  const total = Number(data.total) || 0;
  state.activePhase = phase;
  state.phaseFractions[phase] = total > 0 ? Math.min(1, completed / total) : 1;
  updateOverallProgress();
}

function handleSource(data) {
  state.sourceCount += 1;
  state.phaseFractions.discovery = Math.min(
    1,
    (state.sourceCount / 4) * 1,
  );
  updateOverallProgress();
  const chip = document.createElement("span");
  chip.className = `source-chip ${data.ok ? "ok" : "fail"}`;
  chip.textContent = data.ok
    ? `${data.name}: ${data.count ?? 0} names`
    : `${data.name}: failed`;
  if (data.error) {
    chip.title = data.error;
  }
  $("sources").append(chip);
}

function addLog(level, message) {
  const line = document.createElement("div");
  line.className = `log-line log-${level}`;
  const time = document.createElement("span");
  time.className = "log-time";
  time.textContent = new Date().toLocaleTimeString();
  const text = document.createElement("span");
  text.textContent = message;
  line.append(time, text);
  const log = $("log");
  log.append(line);
  state.logCount += 1;
  while (state.logCount > 500 && log.firstElementChild) {
    log.firstElementChild.remove();
    state.logCount -= 1;
  }
  log.scrollTop = log.scrollHeight;
}

function cell(text, className) {
  const element = document.createElement("td");
  element.textContent = text;
  if (className) {
    element.className = className;
  }
  return element;
}

function buildAssetRow(asset) {
  const row = document.createElement("tr");
  row.dataset.hostname = asset.hostname;

  row.append(cell(asset.hostname, "hostname"));
  row.append(
    cell((asset.addresses || []).join(", ") || "—", "addresses"),
  );

  const statusCell = document.createElement("td");
  const badge = document.createElement("span");
  badge.className = `badge ${asset.is_new ? "badge-new" : "badge-known"}`;
  badge.textContent = asset.is_new ? "New" : "Known";
  statusCell.append(badge);
  row.append(statusCell);

  const probeCell = document.createElement("td");
  const probes = asset.probes || [];
  if (!probes.length) {
    probeCell.textContent = "—";
  } else {
    const list = document.createElement("div");
    list.className = "probe-list";
    probes.forEach((probe) => {
      const line = document.createElement("div");
      line.className = "probe-line";
      const url = document.createElement("span");
      url.className = "probe-url";
      url.textContent = `${probe.url} [${probe.status}]`;
      const meta = document.createElement("span");
      meta.className = "probe-meta";
      meta.textContent = ` title=${probe.title || "-"} server=${
        probe.server || "-"
      }`;
      line.append(url, meta);
      list.append(line);
    });
    probeCell.append(list);
  }
  row.append(probeCell);
  return row;
}

function sortedAssets() {
  const assets = Array.from(state.assets.values());
  const direction = state.sortDirection;
  assets.sort((left, right) => {
    let a;
    let b;
    if (state.sortKey === "status") {
      a = left.is_new ? 0 : 1;
      b = right.is_new ? 0 : 1;
    } else {
      a = left.hostname;
      b = right.hostname;
    }
    if (a < b) {
      return -1 * direction;
    }
    if (a > b) {
      return 1 * direction;
    }
    return 0;
  });
  return assets;
}

function applyFilterToRow(row, hostname) {
  row.hidden = Boolean(
    state.filter && !hostname.toLowerCase().includes(state.filter),
  );
}

function applyFilter() {
  state.rows.forEach((row, hostname) => {
    applyFilterToRow(row, hostname);
  });
}

function upsertAssetRow(asset) {
  const body = $("results-body");
  const row = buildAssetRow(asset);
  const previous = state.rows.get(asset.hostname);
  if (previous) {
    previous.replaceWith(row);
  } else {
    body.append(row);
  }
  state.rows.set(asset.hostname, row);
  applyFilterToRow(row, asset.hostname);
  updateResultsVisibility();
}

function renderResults() {
  const body = $("results-body");
  body.replaceChildren();
  state.rows.clear();
  sortedAssets().forEach((asset) => {
    const row = buildAssetRow(asset);
    body.append(row);
    state.rows.set(asset.hostname, row);
  });
  applyFilter();
  updateResultsVisibility();
}

function updateResultsVisibility() {
  const hasResults = state.assets.size > 0;
  $("results-empty").hidden = hasResults;
  $("results-table").closest(".table-wrap").hidden = !hasResults;
}

function updateStats() {
  let newCount = 0;
  let probed = 0;
  state.assets.forEach((asset) => {
    if (asset.is_new) {
      newCount += 1;
    }
    if ((asset.probes || []).length) {
      probed += 1;
    }
  });
  $("stat-active").textContent = String(state.assets.size);
  $("stat-new").textContent = String(newCount);
  $("stat-probed").textContent = String(probed);
}

function setExportEnabled(enabled) {
  $("export-json").disabled = !enabled;
  $("export-csv").disabled = !enabled;
}

function handleAsset(data) {
  const hostname = String(data.hostname || "");
  if (!hostname) {
    return;
  }
  const existing = state.assets.get(hostname) || {
    hostname,
    addresses: [],
    is_new: false,
    probes: [],
  };
  existing.addresses = Array.isArray(data.addresses)
    ? data.addresses
    : existing.addresses;
  existing.is_new = Boolean(data.is_new);
  state.assets.set(hostname, existing);
  upsertAssetRow(existing);
  updateStats();
}

function handleProbe(data) {
  const hostname = String(data.hostname || "");
  if (!hostname) {
    return;
  }
  const existing = state.assets.get(hostname) || {
    hostname,
    addresses: [],
    is_new: false,
    probes: [],
  };
  existing.probes = Array.isArray(data.probes) ? data.probes : [];
  state.assets.set(hostname, existing);
  upsertAssetRow(existing);
  updateStats();
}

function handleTerminal(type, data) {
  if (state.terminal) {
    return;
  }
  state.terminal = true;
  closeStream();
  if (type === "done") {
    setStatus("completed");
    toast("Scan completed.", "success");
  } else if (type === "cancelled" || (data && data.message === "Scan cancelled.")) {
    setStatus("cancelled");
    toast("Scan cancelled.", "warning");
  } else {
    setStatus("failed");
    toast((data && data.message) || "Scan failed.", "error");
  }
  finishJob();
}

function attachStream() {
  closeStream();
  const source = new EventSource(
    `/api/scans/${state.jobId}/events?last_event_id=0`,
  );
  state.es = source;
  EVENT_TYPES.forEach((type) => {
    source.addEventListener(type, (event) => {
      let data = {};
      try {
        data = JSON.parse(event.data);
      } catch {
        data = {};
      }
      handleEvent(type, data);
    });
  });
  source.onopen = () => {
    stopFallbackPolling();
    setConnection("live");
  };
  source.onerror = () => {
    if (!state.terminal) {
      setConnection("reconnecting");
      startFallbackPolling();
    }
  };
}

function closeStream() {
  if (state.es) {
    state.es.close();
    state.es = null;
  }
}

function handleEvent(type, data) {
  switch (type) {
    case "status":
      setStatus(data.status || "running");
      break;
    case "phase":
      handlePhase(data);
      break;
    case "source":
      handleSource(data);
      break;
    case "progress":
      handleProgress(data);
      break;
    case "asset":
      handleAsset(data);
      break;
    case "probe":
      handleProbe(data);
      break;
    case "log":
      addLog(data.level || "info", data.message || "");
      break;
    case "notification": {
      const provider = data.provider || "Notification";
      if (data.ok) {
        toast(`${provider} notification sent.`, "success");
      } else {
        const message = data.error || `${provider} notification failed.`;
        state.notificationMessages.add(message);
        toast(message, "warning");
      }
      break;
    }
    case "done":
      handleTerminal("done", data);
      break;
    case "error":
      handleTerminal("error", data);
      break;
    case "cancelled":
      handleTerminal("cancelled", data);
      break;
    default:
      break;
  }
}

function applySnapshot(snapshot) {
  state.status = snapshot.status || state.status;
  state.terminal = isTerminal(state.status);
  state.lastEventId = snapshot.last_event_id || state.lastEventId;
  if (snapshot.probe !== undefined) {
    state.probeEnabled = Boolean(snapshot.probe);
  }
  if (snapshot.notifications !== undefined) {
    state.notificationsEnabled = Boolean(snapshot.notifications);
  }
  state.newHostnames = new Set(
    (snapshot.new_assets || []).map((asset) => asset.hostname),
  );
  state.assets.clear();
  state.rows.clear();
  (snapshot.assets || []).forEach((asset) => {
    state.assets.set(asset.hostname, {
      hostname: asset.hostname,
      addresses: asset.addresses || [],
      is_new: state.newHostnames.has(asset.hostname),
      probes: (snapshot.probes || {})[asset.hostname] || [],
    });
  });
  Object.entries(snapshot.probes || {}).forEach(([hostname, probes]) => {
    if (!state.assets.has(hostname)) {
      state.assets.set(hostname, {
        hostname,
        addresses: [],
        is_new: false,
        probes,
      });
    }
  });
  renderResults();
  updateStats();
  setStatus(state.status);
  if (state.terminal) {
    stopElapsed();
    stopFallbackPolling();
    $("run-button").disabled = false;
    $("cancel-button").disabled = true;
    if (state.status === "completed") {
      setProgress(100);
      $("progress-label").textContent = "Complete";
      PHASE_ORDER.forEach((phase) => {
        const item = document.querySelector(
          `.phases li[data-phase="${phase}"]`,
        );
        if (!item || item.dataset.state !== "pending") {
          return;
        }
        if (
          (phase === "probe" && !state.probeEnabled) ||
          (phase === "notify" && !state.notificationsEnabled)
        ) {
          markPhaseSkipped(phase);
        } else {
          state.phaseFractions[phase] = 1;
          setPhase(phase, "completed", "done");
        }
      });
      updateOverallProgress();
    } else {
      $("progress-label").textContent =
        state.status === "cancelled" ? "Cancelled" : "Failed";
    }
    setConnection("online");
  }
  if (snapshot.error) {
    $("form-error").textContent = snapshot.error;
  }
  if (snapshot.status === "completed") {
    $("form-error").textContent = "";
  }
  (snapshot.notification_errors || []).forEach((message) => {
    if (!state.notificationMessages.has(message)) {
      state.notificationMessages.add(message);
      toast(message, "warning");
    }
  });
  setExportEnabled(state.status === "completed");
}

async function finishJob() {
  stopElapsed();
  stopFallbackPolling();
  $("run-button").disabled = false;
  $("cancel-button").disabled = true;
  setConnection("online");
  if (!state.jobId) {
    return;
  }
  try {
    const snapshot = await api(`/api/scans/${state.jobId}`);
    applySnapshot(snapshot);
  } catch (error) {
    toast(error.message, "error");
  }
  if ($("view-history").classList.contains("active")) {
    loadHistory();
  }
}

async function restoreActiveJob() {
  try {
    const data = await api("/api/scans/active");
    if (!data.job) {
      return;
    }
    const job = data.job;
    resetScanUI();
    state.jobId = job.id;
    state.terminal = false;
    state.probeEnabled = Boolean(job.probe);
    state.notificationsEnabled = Boolean(job.notifications);
    state.startTime = (job.created_at || Date.now() / 1000) * 1000;
    if (job.target) {
      $("target").value = job.target;
    }
    if (job.db) {
      state.settings.db = job.db;
      persistSettings();
      $("db").value = job.db;
      $("settings-db").value = job.db;
      $("history-db").textContent = job.db;
    }
    setStatus(job.status || "running");
    $("run-button").disabled = true;
    $("cancel-button").disabled = false;
    $("progress-label").textContent = "Reconnecting";
    startElapsed();
    attachStream();
  } catch {
    // A missing active job is not an error on startup.
  }
}

async function startScan(event) {
  event.preventDefault();
  const formError = $("form-error");
  formError.textContent = "";
  const payload = {
    target: $("target").value.trim(),
    threads: Number($("threads").value),
    timeout: Number($("timeout").value),
    db: $("db").value.trim(),
    probe: $("probe").checked,
    verbose: $("verbose").checked,
    webhook: $("webhook").value.trim() || null,
    telegram: $("telegram").value.trim() || null,
  };
  if (!payload.target) {
    formError.textContent = "Enter a target domain.";
    return;
  }
  if (!Number.isFinite(payload.threads) || payload.threads < 1) {
    formError.textContent = "Concurrency must be at least 1.";
    return;
  }
  if (!Number.isFinite(payload.timeout) || payload.timeout <= 0) {
    formError.textContent = "Timeout must be greater than 0.";
    return;
  }
  if (!payload.db) {
    formError.textContent = "Enter a database path.";
    return;
  }

  state.settings = {
    ...state.settings,
    db: payload.db,
    threads: payload.threads,
    timeout: payload.timeout,
    probe: payload.probe,
    verbose: payload.verbose,
  };
  persistSettings();
  syncSettingsToForms();
  $("run-button").disabled = true;

  try {
    const data = await api("/api/scans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    resetScanUI();
    state.jobId = data.job_id;
    state.terminal = false;
    state.probeEnabled = payload.probe;
    state.notificationsEnabled = Boolean(payload.webhook || payload.telegram);
    state.startTime = Date.now();
    setStatus(data.status || "running");
    $("run-button").disabled = true;
    $("cancel-button").disabled = false;
    $("progress-label").textContent = "Starting";
    startElapsed();
    attachStream();
    toast("Scan started.", "success");
  } catch (error) {
    $("run-button").disabled = false;
    formError.textContent = error.message;
    toast(error.message, "error");
  }
}

async function cancelScan() {
  if (!state.jobId || state.terminal) {
    return;
  }
  $("cancel-button").disabled = true;
  $("progress-label").textContent = "Cancelling";
  try {
    const snapshot = await api(`/api/scans/${state.jobId}/cancel`, {
      method: "POST",
    });
    if (isTerminal(snapshot.status)) {
      const wasTerminal = state.terminal;
      state.terminal = true;
      closeStream();
      stopFallbackPolling();
      applySnapshot(snapshot);
      if (!wasTerminal) {
        if (snapshot.status === "completed") {
          toast("Scan completed.", "success");
        } else if (snapshot.status === "cancelled") {
          toast("Scan cancelled.", "warning");
        } else {
          toast(snapshot.error || "Scan failed.", "error");
        }
      }
    } else {
      toast("Cancellation requested.", "warning");
    }
  } catch (error) {
    $("cancel-button").disabled = false;
    toast(error.message, "error");
  }
}

function resetScanUI() {
  closeStream();
  stopFallbackPolling();
  state.jobId = null;
  state.assets.clear();
  state.rows.clear();
  state.newHostnames.clear();
  state.notificationMessages.clear();
  state.phaseFractions = {};
  state.activePhase = null;
  state.sourceCount = 0;
  state.lastEventId = 0;
  state.filter = "";
  state.logCount = 0;
  $("results-body").replaceChildren();
  $("sources").replaceChildren();
  $("log").replaceChildren();
  $("result-filter").value = "";
  $("webhook").value = "";
  $("telegram").value = "";
  $("form-error").textContent = "";
  $("stat-active").textContent = "0";
  $("stat-new").textContent = "0";
  $("stat-probed").textContent = "0";
  $("stat-elapsed").textContent = "00:00";
  $("progress-label").textContent = "Waiting";
  $("cancel-button").disabled = true;
  setExportEnabled(false);
  resetPhases();
  setProgress(0);
  updateResultsVisibility();
  stopElapsed();
}

function bindNavigation() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });
}

function bindScanForm() {
  $("scan-form").addEventListener("submit", startScan);
  $("cancel-button").addEventListener("click", cancelScan);
  $("clear-log").addEventListener("click", () => {
    $("log").replaceChildren();
    state.logCount = 0;
  });
}

function bindResultTable() {
  $("result-filter").addEventListener("input", (event) => {
    state.filter = event.target.value.trim().toLowerCase();
    applyFilter();
  });
  document.querySelectorAll(".sort-button").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.sort;
      if (state.sortKey === key) {
        state.sortDirection *= -1;
      } else {
        state.sortKey = key;
        state.sortDirection = 1;
      }
      renderResults();
    });
  });
  $("export-json").addEventListener("click", () => {
    if (state.jobId) {
      window.location.assign(
        `/api/scans/${state.jobId}/export?format=json`,
      );
    }
  });
  $("export-csv").addEventListener("click", () => {
    if (state.jobId) {
      window.location.assign(
        `/api/scans/${state.jobId}/export?format=csv`,
      );
    }
  });
}

function bindSettings() {
  $("settings-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const db = $("settings-db").value.trim();
    const threads = Number($("settings-threads").value);
    const timeout = Number($("settings-timeout").value);
    if (!db) {
      toast("Database path cannot be empty.", "error");
      return;
    }
    if (!Number.isFinite(threads) || threads < 1) {
      toast("Concurrency must be at least 1.", "error");
      return;
    }
    if (!Number.isFinite(timeout) || timeout <= 0) {
      toast("Timeout must be greater than 0.", "error");
      return;
    }
    state.settings = {
      db,
      threads,
      timeout,
      probe: $("settings-probe").checked,
      verbose: $("settings-verbose").checked,
    };
    persistSettings();
    syncSettingsToForms();
    toast("Settings saved.", "success");
    if ($("view-history").classList.contains("active")) {
      loadHistory();
    }
  });

  $("reset-settings").addEventListener("click", () => {
    state.settings = { ...DEFAULT_SETTINGS };
    persistSettings();
    syncSettingsToForms();
    toast("Settings reset to defaults.", "info");
  });
}

function statusBadge(status) {
  const badge = document.createElement("span");
  badge.className = `badge badge-${status || "idle"}`;
  badge.textContent = status || "unknown";
  return badge;
}

function renderHistoryRuns(runs) {
  const body = $("history-runs-body");
  body.replaceChildren();
  runs.forEach((run) => {
    const row = document.createElement("tr");
    row.append(cell(formatTime(run.started_at)));
    row.append(cell(run.target));
    const statusCell = document.createElement("td");
    statusCell.append(statusBadge(run.status));
    row.append(statusCell);
    row.append(cell(String(run.asset_count ?? 0)));
    row.append(cell(String(run.new_count ?? 0)));
    row.append(cell(String(run.probe_count ?? 0)));
    const errorCell = cell(run.error || "—");
    if (run.error) {
      errorCell.title = run.error;
    }
    row.append(errorCell);
    body.append(row);
  });
  $("history-runs-empty").hidden = runs.length > 0;
  body.closest(".table-wrap").hidden = runs.length === 0;
}

function renderHistoryAssets(assets) {
  const body = $("history-assets-body");
  body.replaceChildren();
  assets.forEach((asset) => {
    const row = document.createElement("tr");
    row.append(cell(asset.hostname, "hostname"));
    row.append(cell(asset.target));
    row.append(cell((asset.addresses || []).join(", ") || "—", "addresses"));
    row.append(cell(formatTime(asset.first_seen)));
    row.append(cell(formatTime(asset.last_seen)));
    body.append(row);
  });
  $("history-assets-empty").hidden = assets.length > 0;
  body.closest(".table-wrap").hidden = assets.length === 0;
}

async function loadHistoryData() {
  const target = $("history-target").value;
  const search = $("history-search").value.trim();
  const runParams = new URLSearchParams({
    db: state.settings.db,
    limit: "100",
  });
  const assetParams = new URLSearchParams({
    db: state.settings.db,
    limit: "1000",
  });
  if (target) {
    runParams.set("target", target);
    assetParams.set("target", target);
  }
  if (search) {
    assetParams.set("search", search);
  }
  try {
    const [runsData, assetsData] = await Promise.all([
      api(`/api/history/runs?${runParams.toString()}`),
      api(`/api/history/assets?${assetParams.toString()}`),
    ]);
    renderHistoryRuns(runsData.runs || []);
    renderHistoryAssets(assetsData.assets || []);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function loadHistory() {
  $("history-db").textContent = state.settings.db;
  try {
    const data = await api(
      `/api/history/targets?db=${encodeURIComponent(state.settings.db)}`,
    );
    const select = $("history-target");
    const previous = select.value;
    select.replaceChildren();
    const allOption = document.createElement("option");
    allOption.value = "";
    allOption.textContent = "All targets";
    select.append(allOption);
    (data.targets || []).forEach((target) => {
      const option = document.createElement("option");
      option.value = target;
      option.textContent = target;
      select.append(option);
    });
    if (Array.from(select.options).some((option) => option.value === previous)) {
      select.value = previous;
    }
    await loadHistoryData();
  } catch (error) {
    toast(error.message, "error");
  }
}

function bindHistory() {
  $("refresh-history").addEventListener("click", loadHistory);
  $("history-target").addEventListener("change", loadHistoryData);
  $("history-search").addEventListener("input", () => {
    window.clearTimeout(state.historySearchTimer);
    state.historySearchTimer = window.setTimeout(loadHistoryData, 350);
  });
}

async function init() {
  syncSettingsToForms();
  bindNavigation();
  bindScanForm();
  bindResultTable();
  bindSettings();
  bindHistory();
  resetScanUI();
  setStatus("idle");
  updateStats();
  await checkHealth();
  await restoreActiveJob();
  window.setInterval(checkHealth, 15000);
}

document.addEventListener("DOMContentLoaded", init);
