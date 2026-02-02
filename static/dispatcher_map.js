// dispatcher_map.js — CLEAN + FIXED
// - Works with /c/<client_id>/api/tickets_map returning: { ok: true, tickets: [...] }
// - Adds proper “selected marker” styling (stores + restores original label)
// - Keeps your TURBO_DISPATCH hooks + fallback polling
// - Keeps Directions / Select / Schedule buttons in info window

let map;
let markers = new Map();        // ticketId -> google.maps.Marker
let baseLabels = new Map();     // ticketId -> string
let selectedMarkerId = null;

async function fetchTickets(clientId) {
  const res = await fetch(`/c/${encodeURIComponent(clientId)}/api/tickets_map`, {
    credentials: "same-origin",
    headers: { "Accept": "application/json" }
  });
  const data = await res.json();
  return data.tickets || [];
}

function openDirections(lat, lng) {
  const url = `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}`;
  window.open(url, "_blank");
}

function labelFor(t) {
  const u = (t.urgency || "").slice(0, 1).toUpperCase();
  const s = (t.service || "").slice(0, 1).toUpperCase();
  return `${u}${s}`;
}

function setMarkerSelected(id, on) {
  const m = markers.get(id);
  if (!m) return;

  // pop selected marker visually
  m.setZIndex(on ? 999 : undefined);

  const base = baseLabels.get(id) || m.getLabel() || "";

  if (on) {
    m.setLabel({
      text: `★ ${base}`,
      color: "#ffffff",
      fontWeight: "700",
    });
  } else {
    // restore default label
    m.setLabel(base);
  }
}

function selectTicketFromMap(ticketId) {
  // ✅ Works with your dispatcher scope (admin.js bridge)
  if (window.TURBO_DISPATCH && typeof window.TURBO_DISPATCH.selectTicket === "function") {
    window.TURBO_DISPATCH.selectTicket(ticketId);
    return true;
  }

  // Back-compat if you later expose a global selector
  if (typeof window.selectTicket === "function") {
    window.selectTicket(ticketId);
    return true;
  }

  console.warn("No dispatcher selection function found.");
  return false;
}

function openScheduleModalFromMap() {
  if (window.TURBO_DISPATCH && typeof window.TURBO_DISPATCH.openScheduleModal === "function") {
    window.TURBO_DISPATCH.openScheduleModal();
    return true;
  }
  console.warn("No schedule modal hook found.");
  return false;
}

function renderMarkers(tickets) {
  const seen = new Set();

  for (const t of tickets) {
    // skip bad geocodes
    if (typeof t.lat !== "number" || typeof t.lng !== "number") continue;

    const id = t.id;
    if (!id) continue;

    seen.add(id);

    if (!markers.has(id)) {
      const lbl = labelFor(t);
      baseLabels.set(id, lbl);

      const m = new google.maps.Marker({
        position: { lat: t.lat, lng: t.lng },
        map,
        label: lbl,
        title: t.address || id,
      });

      const infowin = new google.maps.InfoWindow({
        content: `
          <div style="font-family:system-ui;max-width:280px;">
            <div style="font-weight:700;margin-bottom:6px;">
              ${t.service || "Ticket"} • ${t.urgency || ""}
            </div>
            <div style="opacity:.85;margin-bottom:8px;">${t.address || ""}</div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
              <button id="dir-${id}" style="padding:6px 10px;border-radius:10px;border:1px solid #334155;background:#0b1220;color:#e5e7eb;cursor:pointer;">Directions</button>
              <button id="sel-${id}" style="padding:6px 10px;border-radius:10px;border:1px solid #334155;background:#0b1220;color:#e5e7eb;cursor:pointer;">Select</button>
              <button id="sch-${id}" style="padding:6px 10px;border-radius:10px;border:1px solid #334155;background:#0b1220;color:#e5e7eb;cursor:pointer;">Schedule</button>
            </div>
          </div>
        `,
      });

      m.addListener("click", () => {
        infowin.open({ anchor: m, map });

        setTimeout(() => {
          const dirBtn = document.getElementById(`dir-${id}`);
          if (dirBtn) dirBtn.onclick = () => openDirections(t.lat, t.lng);

          const selBtn = document.getElementById(`sel-${id}`);
          if (selBtn) selBtn.onclick = () => selectTicketFromMap(id);

          const schBtn = document.getElementById(`sch-${id}`);
          if (schBtn) schBtn.onclick = () => {
            // Ensure dispatcher selects ticket first, then open modal
            selectTicketFromMap(id);
            openScheduleModalFromMap();
          };
        }, 0);
      });

      markers.set(id, m);

      // Apply selection styling immediately if this ticket is currently selected
      if (selectedMarkerId === id) {
        setMarkerSelected(id, true);
      }
    } else {
      // Update existing marker position/title/label if needed
      const m = markers.get(id);
      const pos = m.getPosition();
      if (!pos || pos.lat() !== t.lat || pos.lng() !== t.lng) {
        m.setPosition({ lat: t.lat, lng: t.lng });
      }
      m.setTitle(t.address || id);

      const lbl = labelFor(t);
      // Update base label (unless selected; selection label is derived)
      baseLabels.set(id, lbl);
      if (selectedMarkerId === id) {
        setMarkerSelected(id, true);
      } else {
        m.setLabel(lbl);
      }
    }
  }

  // Remove markers for tickets that disappeared
  for (const [id, m] of markers.entries()) {
    if (!seen.has(id)) {
      m.setMap(null);
      markers.delete(id);
      baseLabels.delete(id);
      if (selectedMarkerId === id) selectedMarkerId = null;
    }
  }
}

async function initDispatchMap(clientId) {
  map = new google.maps.Map(document.getElementById("dispatchMap"), {
    center: { lat: 47.6062, lng: -122.3321 }, // default
    zoom: 9,
    disableDefaultUI: true,
    zoomControl: true,
  });

  // ✅ Hook into dispatcher events if present
  if (window.TURBO_DISPATCH) {
    window.TURBO_DISPATCH.onTicketsUpdated = (tickets) => {
      renderMarkers(tickets || []);
    };

    window.TURBO_DISPATCH.onSelectedTicketChanged = (ticket) => {
      const id = ticket?.id || null;

      if (selectedMarkerId && markers.has(selectedMarkerId)) {
        setMarkerSelected(selectedMarkerId, false);
      }

      selectedMarkerId = id;

      if (selectedMarkerId && markers.has(selectedMarkerId)) {
        setMarkerSelected(selectedMarkerId, true);

        // center to selection (nice UX)
        const pos = markers.get(selectedMarkerId).getPosition();
        if (pos) map.panTo(pos);
      }
    };
  }

  const tickets = await fetchTickets(clientId);
  if (tickets.length) {
    map.setCenter({ lat: tickets[0].lat, lng: tickets[0].lng });
    map.setZoom(11);
  }

  renderMarkers(tickets);

  // Fallback refresh even if dispatcher is pushing updates
  setInterval(async () => {
    const next = await fetchTickets(clientId);
    renderMarkers(next);
  }, 10000);
}

window.initDispatchMap = initDispatchMap;
