const chatLog = document.getElementById("chatLog");
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");

const newChatBtn = document.getElementById("newChatBtn");

const statusPill = document.getElementById("statusPill");
const ticketStatusPill = document.getElementById("ticketStatusPill");

const ticketKv = document.getElementById("ticketKv");
const timelineEl = document.getElementById("timeline");

let ticketId = null;
let sending = false;

function esc(s){
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));
}

function addMsg(who, text){
  const div = document.createElement("div");
  div.className = `msg ${who === "You" ? "user" : "bot"}`;
  div.innerHTML = `<div class="who">${esc(who)}</div><div>${esc(text)}</div>`;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function setReady(isReady){
  statusPill.textContent = isReady ? "Ready" : "Working…";
}

function kvRow(label, value){
  return `
    <div class="k">${esc(label)}</div>
    <div class="v">${esc(value || "—")}</div>
  `;
}

function renderTicket(ticket){
  if (!ticket){
    ticketStatusPill.textContent = "open";
    ticketKv.innerHTML = kvRow("Session", "—")
      + kvRow("Service", "—")
      + kvRow("Urgency", "—")
      + kvRow("Status", "—")
      + kvRow("Address", "—")
      + kvRow("Availability", "—")
      + kvRow("Phone", "—");
    timelineEl.innerHTML = `<div class="event"><div class="label">No timeline yet</div></div>`;
    return;
  }

  ticketStatusPill.textContent = (ticket.status || "open");

  ticketKv.innerHTML =
    kvRow("Session", ticket.id || "—") +
    kvRow("Service", ticket.service || "—") +
    kvRow("Urgency", ticket.urgency || "—") +
    kvRow("Status", ticket.status || "—") +
    kvRow("Address", ticket.address || "—") +
    kvRow("Availability", ticket.availability || "—") +
    kvRow("Phone", ticket.phone || (ticket.phone_declined ? "Declined" : "—"));

  const tl = Array.isArray(ticket.timeline) ? ticket.timeline : [];
  if (!tl.length){
    timelineEl.innerHTML = `<div class="event"><div class="label">No timeline yet</div></div>`;
    return;
  }

  timelineEl.innerHTML = "";
  for (const item of tl){
    const ev = document.createElement("div");
    ev.className = "event";
    const by = item.by || "system";
    const at = item.at || "";
    const label = item.label || "";
    ev.innerHTML = `
      <div class="meta"><span>${esc(by)}</span><span>${esc(at)}</span></div>
      <div class="label">${esc(label)}</div>
    `;
    timelineEl.appendChild(ev);
  }
}

async function sendMessage(text){
  if (sending) return;
  sending = true;
  setReady(false);

  try{
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type":"application/json", "Accept":"application/json" },
      body: JSON.stringify({ message: text, ticket_id: ticketId || "" })
    });

    const data = await res.json();

    if (!res.ok){
      addMsg("Turbo Dispatch", data.error || "Something went wrong.");
      return;
    }

    // Save ticket id for continuity
    if (data.ticket && data.ticket.id) ticketId = data.ticket.id;

    // Reply parts
    const parts = Array.isArray(data.reply_parts) ? data.reply_parts : [];
    if (parts.length){
      addMsg("Turbo Dispatch", parts.join("\n\n"));
    } else {
      addMsg("Turbo Dispatch", "Got it.");
    }

    renderTicket(data.ticket);

  } catch (e){
    addMsg("Turbo Dispatch", "Network error. Try again.");
  } finally {
    sending = false;
    setReady(true);
  }
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = (chatInput.value || "").trim();
  if (!text) return;

  addMsg("You", text);
  chatInput.value = "";
  sendMessage(text);
});

newChatBtn.addEventListener("click", () => {
  // wipe UI + start fresh
  ticketId = null;
  chatLog.innerHTML = "";
  addMsg("Turbo Dispatch", `Hey — I’m Turbo Dispatch.\nWhat’s going on today? (Example: "outlet sparking" or "toilet overflowing")`);
  renderTicket(null);
  chatInput.focus();
});

// boot
addMsg("Turbo Dispatch", `Hey — I’m Turbo Dispatch.\nWhat’s going on today? (Example: "outlet sparking" or "toilet overflowing")`);
renderTicket(null);
