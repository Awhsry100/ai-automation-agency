window.__DISPATCHER_LOADED__ = true;

/**
 * Turbo Dispatch — Dispatcher UX (Phase 2)
 * Matches templates/dispatcher.html exactly.
 * Fixes:
 *  - Uses client-scoped API routes: /c/<client_id>/api/...
 *  - Adds TURBO_DISPATCH hooks for map integration
 *  - Implements filters + include archived toggle
 *  - Implements assign tech + save notes
 *  - Implements schedule modal + quick status actions
 */

const $ = (id) => document.getElementById(id);

// ===== CLIENT / BASE URL =====
const CLIENT_ID = (window.TURBO_CLIENT_ID || "").trim();
const BASE = `/c/${encodeURIComponent(CLIENT_ID)}`;

// ===== LEFT =====
const ticketList = $("ticketList");
const ticketSkeleton = $("ticketSkeleton");
const ticketEmpty = $("ticketEmpty");
const ticketError = $("ticketError");
const ticketErrorText = $("ticketErrorText");
const queueSubtitle = $("queueSubtitle");
const searchInput = $("searchInput");

const mTotal = $("mTotal");
const mOpen = $("mOpen");
const mUrgent = $("mUrgent");
const mClosed = $("mClosed");

const refreshBtn = $("refreshBtn");
const detailsRefreshBtn = $("detailsRefreshBtn");
const lastUpdatedValue = $("lastUpdatedValue");
const autoRefreshValue = $("autoRefreshValue");

// Filters row
const fAll = $("fAll");
const fOpen = $("fOpen");
const fUrgent = $("fUrgent");
const fBreachSoon = $("fBreachSoon");
const fUnassigned = $("fUnassigned");
const fArchived = $("fArchived");
const includeDeletedToggle = $("includeDeletedToggle");

// ===== RIGHT =====
const detailsEmpty = $("detailsEmpty");
const detailsSkeleton = $("detailsSkeleton");
const detailsPanel = $("detailsPanel");
const detailsError = $("detailsError");
const detailsErrorText = $("detailsErrorText");

const dTitle = $("dTitle");
const dSubtitle = $("dSubtitle");
const dPriorityPill = $("dPriorityPill");
const dStatusPill = $("dStatusPill");
const dSlaPill = $("dSlaPill");

const dPhone = $("dPhone");
const dService = $("dService");
const dAddress = $("dAddress");
const dZip = $("dZip");
const dAvailability = $("dAvailability");

// Assign + notes
const techSelect = $("techSelect");
const assignTechBtn = $("assignTechBtn");
const assignStatus = $("assignStatus");

const internalNotes = $("internalNotes");
const saveMetaBtn = $("saveMetaBtn");
const metaSaveStatus = $("metaSaveStatus");

// Quick actions row
const callBtn = $("callBtn");
const textBtn = $("textBtn");
const actionScheduleBtn = $("actionScheduleBtn");
const actionEnRouteBtn = $("actionEnRouteBtn");
const actionOnsiteBtn = $("actionOnsiteBtn");
const actionCompleteBtn = $("actionCompleteBtn");
const actionCancelBtn = $("actionCancelBtn");

// Schedule modal
const scheduleModal = $("scheduleModal");
const scheduleTicketHint = $("scheduleTicketHint");
const scheduleCloseBtn = $("scheduleCloseBtn");
const scheduleStart = $("scheduleStart");
const scheduleDuration = $("scheduleDuration");
const scheduleSaveBtn = $("scheduleSaveBtn");
const scheduleCancelBtn = $("scheduleCancelBtn");

// State
let tickets = [];
let filtered = [];
let selectedId = null;
let selectedTicket = null;
let lastRefreshAt = null;

let activeFilter = "all"; // all|open|urgent|breachSoon|unassigned|archived
let autoRefreshEnabled = true;
let autoRefreshHandle = null;

// ===== helpers =====
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function show(el) { if (el) el.classList.remove("hidden"); }
function hide(el) { if (el) el.classList.add("hidden"); }
function setText(el, value) { if (el) el.textContent = (value ?? "—"); }

function isClosedStatus(s) {
  return ["completed", "canceled"].includes((s || "").toLowerCase());
}

function statusOf(t) {
  return (t?.status || "open").toLowerCase();
}

function urgencyOf(t) {
  return (t?.urgency || "normal").toLowerCase();
}

function slaAgeSeconds(t) {
  const v = t?.sla_calc?.age_seconds;
  return (typeof v === "number") ? v : null;
}

function setLeftState(state, msg) {
  // state: "loading" | "ready" | "empty" | "error"
  if (state === "loading") {
    show(ticketSkeleton); hide(ticketList); hide(ticketEmpty); hide(ticketError);
    if (queueSubtitle) queueSubtitle.textContent = "Loading tickets…";
    return;
  }

  hide(ticketSkeleton);

  if (state === "empty") {
    hide(ticketList); show(ticketEmpty); hide(ticketError);
    if (queueSubtitle) queueSubtitle.textContent = "No tickets yet";
    return;
  }

  if (state === "error") {
    hide(ticketList); hide(ticketEmpty); show(ticketError);
    if (ticketErrorText) ticketErrorText.textContent = msg || "Please try refresh.";
    if (queueSubtitle) queueSubtitle.textContent = "Error loading tickets";
    return;
  }

  // ready
  hide(ticketEmpty); hide(ticketError); show(ticketList);
  if (queueSubtitle) queueSubtitle.textContent = `${filtered.length} tickets shown`;
}

function setRightState(state, msg) {
  // state: "empty" | "loading" | "ready" | "error"
  hide(detailsEmpty); hide(detailsSkeleton); hide(detailsPanel); hide(detailsError);

  if (state === "empty") show(detailsEmpty);
  else if (state === "loading") show(detailsSkeleton);
  else if (state === "ready") show(detailsPanel);
  else {
    show(detailsError);
    if (detailsErrorText) detailsErrorText.textContent = msg || "Try selecting again or refresh.";
  }
}

function computeMetrics(list) {
  const total = list.length;
  const openCount = list.filter(t => statusOf(t) === "open").length;

  // urgent should not include closed tickets
  const urgentCount = list.filter(t =>
    urgencyOf(t) === "urgent" && !isClosedStatus(statusOf(t))
  ).length;

  const closedCount = list.filter(t => isClosedStatus(statusOf(t))).length;

  setText(mTotal, total);
  setText(mOpen, openCount);
  setText(mUrgent, urgentCount);
  setText(mClosed, closedCount);
}

function ticketSubtitle(t) {
  const bits = [];
  if (t.phone) bits.push(t.phone);
  if (t.address) bits.push(t.address);
  if (t.availability) bits.push(`Avail: ${t.availability}`);
  return bits.join(" • ") || "—";
}

function pill(label, cls = "") {
  const safe = esc(label || "");
  const klass = `pill ${cls}`.trim();
  return `<span class="${klass}">${safe}</span>`;
}

function setUrgencyPill(el, urgency) {
  if (!el) return;
  el.classList.remove("urgent");
  const u = (urgency || "normal").toLowerCase();
  el.textContent = u;
  if (u === "urgent") el.classList.add("urgent"); // uses your CSS .pill.urgent
}

function setStatusPill(el, status) {
  if (!el) return;
  el.textContent = (status || "open").toLowerCase();
}

function setSlaPill(el, t) {
  if (!el) return;
  const age = (t?.sla_calc?.age_human) ? t.sla_calc.age_human : "—";
  el.textContent = `SLA: ${age}`;
}

// ===== API helpers =====
async function apiFetch(path, options = {}) {
  const url = path.startsWith("/") ? path : `/${path}`;
  const res = await fetch(url, {
    credentials: "same-origin",
    headers: {
      "Accept": "application/json",
      ...(options.headers || {})
    },
    ...options
  });
  let data = null;
  try { data = await res.json(); } catch { /* ignore */ }
  if (!res.ok) {
    const msg = data?.error || `Request failed (${res.status})`;
    throw new Error(msg);
  }
  return data;
}

function ticketsUrl() {
  const includeDeleted = includeDeletedToggle?.checked ? "1" : "0";
  return `${BASE}/api/tickets?include_deleted=${includeDeleted}`;
}

async function loadTickets() {
  setLeftState("loading");

  try {
    const data = await apiFetch(ticketsUrl());
    tickets = Array.isArray(data) ? data : [];
    applySearchAndFilter(false);

    lastRefreshAt = Date.now();

    // Keep selection if still exists
    if (selectedId) {
      const still = tickets.find(t => t.id === selectedId);
      if (!still) {
        selectedId = null;
        selectedTicket = null;
        setRightState("empty");
      } else {
        await loadDetails(selectedId);
      }
    } else {
      setRightState("empty");
    }

    // Notify map of updates
    turboDispatchTicketsUpdated(tickets);

  } catch (e) {
    setLeftState("error", e.message);
    setRightState("empty");
  }
}

async function loadDetails(id) {
  setRightState("loading");

  try {
    const t = await apiFetch(`${BASE}/api/tickets/${encodeURIComponent(id)}`);
    selectedTicket = t;

    setText(dTitle, `Ticket ${t.id || ""}`.trim());
    setText(dSubtitle, `Created: ${t.created_at || "—"}`);

    setSlaPill(dSlaPill, t);
    setUrgencyPill(dPriorityPill, t.urgency);
    setStatusPill(dStatusPill, t.status);

    setText(dPhone, t.phone || (t.phone_declined ? "Declined" : "—"));
    setText(dService, t.service || "—");
    setText(dAddress, t.address || "—");
    setText(dZip, t.address_zip || "—");
    setText(dAvailability, t.availability || "—");

    // Buttons: call/text
    wireCallTextButtons(t);

    // Assign + notes
    if (techSelect) techSelect.value = (t.assigned_tech_id || "");
    if (internalNotes) internalNotes.value = (t.internal_notes || "");

    setText(assignStatus, "—");
    setText(metaSaveStatus, "—");

    setRightState("ready");
    renderList();

    // Notify map selection
    turboDispatchSelectedTicketChanged(t);

  } catch (e) {
    setRightState("error", e.message);
  }
}

async function setStatus(id, status) {
  await apiFetch(`${BASE}/api/tickets/${encodeURIComponent(id)}/status`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status })
  });
}

async function assignTech(id, techId) {
  await apiFetch(`${BASE}/api/tickets/${encodeURIComponent(id)}/assign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tech_id: techId || "" })
  });
}

async function saveMeta(id, notesText) {
  await apiFetch(`${BASE}/api/tickets/${encodeURIComponent(id)}/meta`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ internal_notes: notesText || "" })
  });
}

// Scheduling: prefer /schedule (your later endpoint), fallback to /calendar
async function scheduleTicket(id, startLocalISO, durationMinutes) {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "America/Los_Angeles";
  const mins = Number(durationMinutes || 60);

  // Try /schedule first
  try {
    return await apiFetch(`${BASE}/api/tickets/${encodeURIComponent(id)}/schedule`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start: startLocalISO,
        duration_minutes: mins,
        timezone: tz
      })
    });
  } catch (e) {
    // Fallback /calendar endpoint (expects start/end or duration)
    return await apiFetch(`${BASE}/api/tickets/${encodeURIComponent(id)}/calendar`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start: startLocalISO,
        duration_minutes: mins
      })
    });
  }
}

// ===== Filtering / searching =====
function isBreachSoon(t) {
  // simple heuristic: age_seconds >= 2h (you can tune)
  const s = slaAgeSeconds(t);
  return typeof s === "number" && s >= (2 * 60 * 60);
}

function matchesActiveFilter(t) {
  const st = statusOf(t);
  const urg = urgencyOf(t);

  if (activeFilter === "open") return st === "open";
  if (activeFilter === "urgent") return urg === "urgent" && !isClosedStatus(st);
  if (activeFilter === "breachSoon") return !isClosedStatus(st) && isBreachSoon(t);
  if (activeFilter === "unassigned") return !isClosedStatus(st) && !(t.assigned_tech_id || "").trim();
  if (activeFilter === "archived") return !!t.deleted;
  return true; // all
}

function applySearchAndFilter(keepRightState = true) {
  const q = (searchInput?.value || "").trim().toLowerCase();

  let list = tickets.slice();

  // If "Archived" filter is active, show only deleted; otherwise hide deleted unless includeDeleted is checked
  const includeDeleted = includeDeletedToggle?.checked;
  if (activeFilter === "archived") {
    list = list.filter(t => !!t.deleted);
  } else if (!includeDeleted) {
    list = list.filter(t => !t.deleted);
  }

  // Apply filter
  list = list.filter(matchesActiveFilter);

  // Apply search
  if (q) {
    list = list.filter(t => {
      const blob = [
        t.id, t.phone, t.address, t.address_raw, t.availability,
        t.service, t.urgency, t.status, t.assigned_tech_name
      ].join(" ").toLowerCase();
      return blob.includes(q);
    });
  }

  filtered = list;
  renderList();

  if (!keepRightState && !selectedId) setRightState("empty");
}

function setActiveFilter(name) {
  activeFilter = name;

  // button visual state (optional)
  const btns = [fAll, fOpen, fUrgent, fBreachSoon, fUnassigned, fArchived];
  for (const b of btns) b?.classList.remove("btn-primary");

  if (name === "all") fAll?.classList.add("btn-primary");
  else if (name === "open") fOpen?.classList.add("btn-primary");
  else if (name === "urgent") fUrgent?.classList.add("btn-primary");
  else if (name === "breachSoon") fBreachSoon?.classList.add("btn-primary");
  else if (name === "unassigned") fUnassigned?.classList.add("btn-primary");
  else if (name === "archived") fArchived?.classList.add("btn-primary");

  applySearchAndFilter(true);
}

// ===== Rendering =====
function renderList() {
  if (!ticketList) return;

  ticketList.innerHTML = "";
  computeMetrics(filtered);

  if (!filtered.length) {
    setLeftState("empty");
    return;
  }

  setLeftState("ready");

  for (const t of filtered) {
    const card = document.createElement("div");
    card.className = "ticket-card" + (t.id === selectedId ? " active" : "");
    card.dataset.id = t.id;

    const status = statusOf(t);
    const service = (t.service || "unknown").toLowerCase();
    const urgency = urgencyOf(t);

    const badgeService = pill(service);
    const badgeStatus = pill(status, "pill-muted");
    const badgeUrgency = urgency === "urgent"
      ? pill("urgent", "urgent")
      : pill("normal", "pill-muted");

    const archivedBadge = t.deleted ? pill("archived", "pill-muted") : "";

    card.innerHTML = `
      <div class="ticket-top">
        <div class="ticket-title">${esc(t.id || "Ticket")}</div>
        <div class="badges">${badgeService}${badgeStatus}${badgeUrgency}${archivedBadge}</div>
      </div>
      <div class="ticket-sub">${esc(ticketSubtitle(t))}</div>
    `;

    card.addEventListener("click", () => selectTicket(t.id));
    ticketList.appendChild(card);
  }
}

async function selectTicket(id) {
  selectedId = id;
  renderList();
  await loadDetails(id);
}

// ===== Call/Text wiring =====
function wireCallTextButtons(t) {
  const phone = (t.phone || "").trim();
  const canUse = !!phone && phone !== "Declined";

  if (callBtn) {
    callBtn.classList.toggle("disabled", !canUse);
    callBtn.href = canUse ? `tel:${phone}` : "#";
  }
  if (textBtn) {
    textBtn.classList.toggle("disabled", !canUse);
    textBtn.href = canUse ? `sms:${phone}` : "#";
  }
}

// ===== Assign tech + notes =====
async function onAssignTech() {
  if (!selectedId) return;
  const techId = (techSelect?.value || "").trim();

  try {
    if (assignTechBtn) assignTechBtn.disabled = true;
    setText(assignStatus, "Assigning…");

    await assignTech(selectedId, techId);
    setText(assignStatus, "✅ Assigned");

    await loadTickets(); // refresh list/details
  } catch (e) {
    setText(assignStatus, `⚠️ ${e.message}`);
  } finally {
    if (assignTechBtn) assignTechBtn.disabled = false;
  }
}

async function onSaveNotes() {
  if (!selectedId) return;
  const notes = (internalNotes?.value || "").trim();

  try {
    if (saveMetaBtn) saveMetaBtn.disabled = true;
    setText(metaSaveStatus, "Saving…");

    await saveMeta(selectedId, notes);
    setText(metaSaveStatus, "✅ Saved");

    await loadTickets();
  } catch (e) {
    setText(metaSaveStatus, `⚠️ ${e.message}`);
  } finally {
    if (saveMetaBtn) saveMetaBtn.disabled = false;
  }
}

// ===== Quick status actions =====
async function quickSetStatus(newStatus) {
  if (!selectedId) return;

  try {
    await setStatus(selectedId, newStatus);
    await loadTickets();
  } catch (e) {
    setRightState("error", e.message);
  }
}

// ===== Schedule modal =====
function openScheduleModal() {
  if (!selectedId) return false;

  // hint
  if (scheduleTicketHint) scheduleTicketHint.textContent = `Ticket ${selectedId}`;

  // default datetime-local value: now + 1 hour
  if (scheduleStart) {
    const now = new Date();
    now.setMinutes(0, 0, 0);
    now.setHours(now.getHours() + 1);

    // datetime-local wants "YYYY-MM-DDTHH:MM"
    const pad = (n) => String(n).padStart(2, "0");
    const v = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`;
    scheduleStart.value = v;
  }

  show(scheduleModal);
  scheduleModal?.classList.remove("hidden");
  return true;
}

function closeScheduleModal() {
  hide(scheduleModal);
  scheduleModal?.classList.add("hidden");
}

async function onScheduleSave() {
  if (!selectedId) return;

  const start = (scheduleStart?.value || "").trim(); // "YYYY-MM-DDTHH:MM"
  const dur = (scheduleDuration?.value || "60").trim();

  if (!start) return;

  try {
    if (scheduleSaveBtn) scheduleSaveBtn.disabled = true;

    await scheduleTicket(selectedId, start, dur);

    closeScheduleModal();
    await loadTickets();
  } catch (e) {
    // keep modal open; show error via right panel
    setRightState("error", e.message);
  } finally {
    if (scheduleSaveBtn) scheduleSaveBtn.disabled = false;
  }
}

// ===== Last updated timer =====
function tickLastUpdated() {
  if (!lastUpdatedValue) return;

  if (!lastRefreshAt) {
    lastUpdatedValue.textContent = "—";
    return;
  }

  const sec = Math.floor((Date.now() - lastRefreshAt) / 1000);
  lastUpdatedValue.textContent = sec <= 1 ? "just now" : `${sec}s ago`;
}

// ===== Auto refresh =====
function setAutoRefresh(on) {
  autoRefreshEnabled = !!on;
  if (autoRefreshValue) autoRefreshValue.textContent = autoRefreshEnabled ? "on" : "off";

  if (autoRefreshHandle) {
    clearInterval(autoRefreshHandle);
    autoRefreshHandle = null;
  }

  if (autoRefreshEnabled) {
    autoRefreshHandle = setInterval(() => {
      loadTickets();
    }, 10000);
  }
}

// ===== Map integration (TURBO_DISPATCH hooks) =====
function turboDispatchTicketsUpdated(list) {
  if (window.TURBO_DISPATCH && typeof window.TURBO_DISPATCH.onTicketsUpdated === "function") {
    window.TURBO_DISPATCH.onTicketsUpdated(list || []);
  }
}

function turboDispatchSelectedTicketChanged(ticket) {
  if (window.TURBO_DISPATCH && typeof window.TURBO_DISPATCH.onSelectedTicketChanged === "function") {
    window.TURBO_DISPATCH.onSelectedTicketChanged(ticket || null);
  }
}

// Expose dispatcher API for map + other scripts
window.TURBO_DISPATCH = window.TURBO_DISPATCH || {};
window.TURBO_DISPATCH.selectTicket = (ticketId) => {
  if (!ticketId) return;
  selectTicket(ticketId);
};
window.TURBO_DISPATCH.openScheduleModal = () => openScheduleModal();

// ===== Wire events =====
refreshBtn?.addEventListener("click", loadTickets);
detailsRefreshBtn?.addEventListener("click", loadTickets);

searchInput?.addEventListener("input", () => applySearchAndFilter(true));
includeDeletedToggle?.addEventListener("change", () => applySearchAndFilter(true));

fAll?.addEventListener("click", () => setActiveFilter("all"));
fOpen?.addEventListener("click", () => setActiveFilter("open"));
fUrgent?.addEventListener("click", () => setActiveFilter("urgent"));
fBreachSoon?.addEventListener("click", () => setActiveFilter("breachSoon"));
fUnassigned?.addEventListener("click", () => setActiveFilter("unassigned"));
fArchived?.addEventListener("click", () => setActiveFilter("archived"));

assignTechBtn?.addEventListener("click", onAssignTech);
saveMetaBtn?.addEventListener("click", onSaveNotes);

actionScheduleBtn?.addEventListener("click", () => openScheduleModal());
actionEnRouteBtn?.addEventListener("click", () => quickSetStatus("en_route"));
actionOnsiteBtn?.addEventListener("click", () => quickSetStatus("onsite"));
actionCompleteBtn?.addEventListener("click", () => quickSetStatus("completed"));
actionCancelBtn?.addEventListener("click", () => quickSetStatus("canceled"));

// Modal buttons
scheduleCloseBtn?.addEventListener("click", closeScheduleModal);
scheduleCancelBtn?.addEventListener("click", closeScheduleModal);
scheduleSaveBtn?.addEventListener("click", onScheduleSave);

// Close modal if clicking backdrop
scheduleModal?.addEventListener("click", (e) => {
  if (e.target && e.target.classList.contains("modal-backdrop")) closeScheduleModal();
});

// ===== Start =====
setInterval(tickLastUpdated, 1000);
setActiveFilter("all");
setAutoRefresh(true);
loadTickets();
