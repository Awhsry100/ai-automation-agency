window.__DISPATCHER_LOADED__ = true;

/**
 * Turbo Dispatch — Dispatcher UX (Phase 2)
 * Matches templates/dispatcher.html exactly:
 * IDs used:
 *  Left: ticketList, ticketSkeleton, ticketEmpty, ticketError, ticketErrorText,
 *        queueSubtitle, searchInput, mTotal, mOpen, mUrgent, mClosed,
 *        refreshBtn, detailsRefreshBtn, lastUpdatedValue
 * Right: detailsEmpty, detailsSkeleton, detailsPanel, detailsError, detailsErrorText,
 *        dTitle, dSubtitle, dPriorityPill, dStatusPill,
 *        dPhone, dService, dAddress, dZip, dAvailability, dMeta,
 *        statusSelect, saveStatusBtn, clearSelectionBtn
 *
 * ✅ NEW FEATURE: Seen / Unseen tickets
 * - Calls POST /api/tickets/:id/seen when a ticket is opened
 * - Shows a NEW pill for unseen tickets (no seen_at)
 */

const $ = (id) => document.getElementById(id);

// LEFT
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

// RIGHT
const detailsEmpty = $("detailsEmpty");
const detailsSkeleton = $("detailsSkeleton");
const detailsPanel = $("detailsPanel");
const detailsError = $("detailsError");
const detailsErrorText = $("detailsErrorText");

const dTitle = $("dTitle");
const dSubtitle = $("dSubtitle");
const dPriorityPill = $("dPriorityPill");
const dStatusPill = $("dStatusPill");

const dPhone = $("dPhone");
const dService = $("dService");
const dAddress = $("dAddress");
const dZip = $("dZip");
const dAvailability = $("dAvailability");
const dMeta = $("dMeta");

const statusSelect = $("statusSelect");
const saveStatusBtn = $("saveStatusBtn");
const clearSelectionBtn = $("clearSelectionBtn");

// State
let tickets = [];
let filtered = [];
let selectedId = null;
let lastRefreshAt = null;

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

  const isClosed = (s) => ["completed", "canceled"].includes((s || "").toLowerCase());
  const status = (t) => (t.status || "open").toLowerCase();

  const openCount = list.filter(t => status(t) === "open").length;

  // urgent should not include closed tickets
  const urgentCount = list.filter(t =>
    (t.urgency || "normal").toLowerCase() === "urgent" && !isClosed(status(t))
  ).length;

  const closedCount = list.filter(t => isClosed(status(t))).length;

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

function isUnseen(t) {
  // Unseen = never viewed by dispatcher
  return !(t && t.seen_at);
}

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

    const status = (t.status || "open").toLowerCase();
    const service = (t.service || "unknown").toLowerCase();
    const urgency = (t.urgency || "normal").toLowerCase();

    const badgeService = pill(service);
    const badgeStatus = pill(status, "pill-muted");
    const badgeUrgency = urgency === "urgent"
      ? pill("urgent", "urgent")
      : pill("normal", "pill-muted");

    // ✅ NEW: NEW pill for unseen tickets
    const badgeNew = isUnseen(t)
      ? pill("NEW", "pill-warn")
      : "";

    card.innerHTML = `
      <div class="ticket-top">
        <div class="ticket-title">${esc(t.id || "Ticket")}</div>
        <div class="badges">${badgeNew}${badgeService}${badgeStatus}${badgeUrgency}</div>
      </div>
      <div class="ticket-sub">${esc(ticketSubtitle(t))}</div>
    `;

    card.addEventListener("click", () => selectTicket(t.id));
    ticketList.appendChild(card);
  }
}

function applySearch() {
  const q = (searchInput?.value || "").trim().toLowerCase();

  if (!q) {
    filtered = tickets.slice();
  } else {
    filtered = tickets.filter(t => {
      const blob = [
        t.id, t.phone, t.address, t.address_raw, t.availability,
        t.service, t.urgency, t.status, t.seen_by, t.seen_at
      ].join(" ").toLowerCase();
      return blob.includes(q);
    });
  }

  renderList();
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

async function markSeen(id) {
  // Non-blocking; do not fail the UI
  try {
    await fetch(`/api/tickets/${encodeURIComponent(id)}/seen`, { method: "POST" });
  } catch (_) {}
}

async function loadTickets() {
  setLeftState("loading");

  try {
    const res = await fetch("/api/tickets", { headers: { "Accept": "application/json" } });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to load tickets");

    tickets = Array.isArray(data) ? data : [];
    filtered = tickets.slice();

    lastRefreshAt = Date.now();
    applySearch();

    // If no selection, keep right panel in "select a ticket" state
    if (!selectedId) {
      setRightState("empty");
      return;
    }

    // Keep selection if still exists
    const still = tickets.find(t => t.id === selectedId);
    if (!still) {
      selectedId = null;
      renderList();
      setRightState("empty");
      return;
    }

    await loadDetails(selectedId);

  } catch (e) {
    setLeftState("error", e.message);
    setRightState("empty");
  }
}

async function loadDetails(id) {
  setRightState("loading");

  try {
    const res = await fetch(`/api/tickets/${encodeURIComponent(id)}`, { headers: { "Accept": "application/json" } });
    const t = await res.json();
    if (!res.ok) throw new Error(t.error || "Failed to load ticket details");

    // ✅ NEW: mark as seen when opened
    markSeen(id);

    setText(dTitle, `Ticket ${t.id || ""}`.trim());
    setText(dSubtitle, `Created: ${t.created_at || "—"}`);

    setUrgencyPill(dPriorityPill, t.urgency);
    setStatusPill(dStatusPill, t.status);

    setText(dPhone, t.phone || (t.phone_declined ? "Declined" : "—"));
    setText(dService, t.service || "—");
    setText(dAddress, t.address || "—");
    setText(dZip, t.address_zip || "—");
    setText(dAvailability, t.availability || "—");

    // statusSelect is already populated from backend in the template
    if (statusSelect) statusSelect.value = (t.status || "open");

    const age = (t.sla_calc && t.sla_calc.age_human) ? t.sla_calc.age_human : "—";
    const seen = t.seen_at ? `Seen: ${t.seen_by || "—"} @ ${t.seen_at}` : "Seen: NEW";
    setText(dMeta, `Age: ${age} • Draft: ${t.draft ? "yes" : "no"} • ${seen}`);

    setRightState("ready");
    renderList(); // keeps left highlight accurate

  } catch (e) {
    setRightState("error", e.message);
  }
}

async function selectTicket(id) {
  selectedId = id;
  renderList();
  await loadDetails(id);
}

async function saveStatus() {
  if (!selectedId || !statusSelect) return;

  const newStatus = statusSelect.value;

  try {
    if (saveStatusBtn) saveStatusBtn.disabled = true;

    const res = await fetch(`/api/tickets/${encodeURIComponent(selectedId)}/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({ status: newStatus })
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to save status");

    // Refresh list + details to reflect changes
    await loadTickets();

  } catch (e) {
    setRightState("error", e.message);
  } finally {
    if (saveStatusBtn) saveStatusBtn.disabled = false;
  }
}

function clearSelection() {
  selectedId = null;
  renderList();
  setRightState("empty");
}

function tickLastUpdated() {
  if (!lastUpdatedValue) return;

  if (!lastRefreshAt) {
    lastUpdatedValue.textContent = "—";
    return;
  }

  const sec = Math.floor((Date.now() - lastRefreshAt) / 1000);
  lastUpdatedValue.textContent = sec <= 1 ? "just now" : `${sec}s ago`;
}

// Wire events
refreshBtn?.addEventListener("click", loadTickets);
detailsRefreshBtn?.addEventListener("click", loadTickets);
saveStatusBtn?.addEventListener("click", saveStatus);
clearSelectionBtn?.addEventListener("click", clearSelection);
searchInput?.addEventListener("input", applySearch);

// Start
setInterval(tickLastUpdated, 1000);
loadTickets();
