// static/chat.js
(() => {
  // ✅ HARD GUARD: prevents double-init if chat.js is included twice
  if (window.__TURBO_CHAT_INIT__) return;
  window.__TURBO_CHAT_INIT__ = true;

  const cid = window.TURBO_CLIENT_ID;

  const chat = document.getElementById("chat");
  const chatInner = document.getElementById("chatInner");
  const msgInput = document.getElementById("msgInput");
  const sendBtn = document.getElementById("sendBtn");
  const newChatBtn = document.getElementById("newChatBtn");
  const newMsgPill = document.getElementById("newMsgPill");
  const chatState = document.getElementById("chatState");

  // ✅ double-submit/click dedupe
  let __submitJustHandled = false;
  // ✅ NEW: Enter key can trigger both keydown handler AND form submit
  let __enterJustHandled = false;

  // ✅ catch form-submit reloads
  const chatForm = document.getElementById("chatForm") || (msgInput ? msgInput.closest("form") : null);
  if (chatForm) {
    chatForm.addEventListener("submit", (e) => {
      e.preventDefault();
      e.stopPropagation();

      // ✅ if click handler already triggered send, skip submit send
      if (__submitJustHandled) {
        __submitJustHandled = false;
        return;
      }

      // ✅ NEW: if Enter key already triggered send, skip submit send
      if (__enterJustHandled) {
        __enterJustHandled = false;
        return;
      }

      sendMessage();
    });
  }

  // Ticket UI
  const ticketStatusPill = document.getElementById("ticketStatusPill");
  const ticketId = document.getElementById("ticketId");
  const ticketService = document.getElementById("ticketService");
  const ticketUrgency = document.getElementById("ticketUrgency");
  const ticketStatus = document.getElementById("ticketStatus");
  const ticketAddress = document.getElementById("ticketAddress");
  const ticketAvailability = document.getElementById("ticketAvailability");
  const ticketPhone = document.getElementById("ticketPhone");
  const timeline = document.getElementById("timeline");

  // ✅ Dedupe / stable hydrate
  const seenMsgIds = new Set();

  function setState(s) { if (chatState) chatState.textContent = s; }
  function sleep(ms) { return new Promise((resolve) => window.setTimeout(resolve, ms)); }
  function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

  // ===== Typewriter tuning =====
  function msPerCharFor(text) {
    const len = String(text || "").length;
    if (len <= 60) return 18;
    if (len <= 180) return 14;
    if (len <= 400) return 11;
    return 9;
  }
  function endPauseFor(text) {
    const len = String(text || "").length;
    return clamp(140 + len * 1.0, 160, 520);
  }

  function escapeHtml(str) {
    return (str ?? "").replace(/[&<>"']/g, (ch) => {
      switch (ch) {
        case "&": return "&amp;";
        case "<": return "&lt;";
        case ">": return "&gt;";
        case "\"": return "&quot;";
        case "'": return "&#039;";
        default: return ch;
      }
    });
  }

  function textToHtml(text) {
    return escapeHtml(text || "").replace(/\n/g, "<br>");
  }

  function isNearBottom(el, thresholdPx = 160) {
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < thresholdPx;
  }

  function scrollToBottom() {
    if (!chat) return;
    chat.scrollTop = chat.scrollHeight;
  }

  function nowStamp() {
    try { return new Date().toLocaleString(); } catch { return ""; }
  }

  function stampFromIso(iso) {
    if (!iso) return nowStamp();
    try {
      const d = new Date(String(iso));
      if (isNaN(d.getTime())) return nowStamp();
      return d.toLocaleString();
    } catch {
      return nowStamp();
    }
  }

  // =========================================================
  // Toasts
  // =========================================================
  let __toastWrap = null;

  function getToastWrap() {
    if (__toastWrap) return __toastWrap;
    __toastWrap = document.createElement("div");
    __toastWrap.className = "toast-wrap";
    document.body.appendChild(__toastWrap);
    return __toastWrap;
  }

  function toast(type, title, desc = "", ms = 1700) {
    const wrap = getToastWrap();
    const el = document.createElement("div");
    const cls = type === "err" ? "err" : (type === "warn" ? "warn" : "ok");
    const icon = type === "err" ? "⚠️" : (type === "warn" ? "⚡" : "✅");

    el.className = `toast ${cls}`;
    el.innerHTML = `
      <div class="t-ico">${escapeHtml(icon)}</div>
      <div class="t-body">
        <div class="t-title">${escapeHtml(title || "")}</div>
        ${desc ? `<div class="t-desc">${escapeHtml(desc)}</div>` : ``}
      </div>
    `;
    wrap.appendChild(el);

    window.setTimeout(() => {
      el.style.animation = "toastOut .18s ease forwards";
      window.setTimeout(() => el.remove(), 220);
    }, ms);
  }

  // =========================================================
  // Typing indicator (bot bubble with dots)
  // =========================================================
  let typingRow = null;

  function showTyping() {
    if (!chatInner) return;
    if (typingRow) return;

    typingRow = document.createElement("div");
    typingRow.className = "msg bot";

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = "TD";

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.innerHTML = `
      <div class="bubble-text typing">
        <span>Turbo Dispatch is typing</span>
        <span class="dots">
          <span class="dot"></span><span class="dot"></span><span class="dot"></span>
        </span>
      </div>
      <div class="msg-time">—</div>
    `;

    typingRow.appendChild(avatar);
    typingRow.appendChild(bubble);
    chatInner.appendChild(typingRow);

    if (isNearBottom(chat)) scrollToBottom();
  }

  function hideTyping() {
    if (!typingRow) return;
    typingRow.remove();
    typingRow = null;
  }

  function resetTicketUI() {
    if (ticketStatusPill) {
      ticketStatusPill.textContent = "open";
      ticketStatusPill.classList.remove("pill-danger", "pill-warn");
    }
    if (ticketId) ticketId.textContent = "—";
    if (ticketService) ticketService.textContent = "unknown";
    if (ticketUrgency) ticketUrgency.textContent = "normal";
    if (ticketStatus) ticketStatus.textContent = "open";
    if (ticketAddress) ticketAddress.textContent = "—";
    if (ticketAvailability) ticketAvailability.textContent = "—";
    if (ticketPhone) ticketPhone.textContent = "—";
    if (timeline) timeline.innerHTML = "";
  }

  function updateTicketUI(t) {
    if (!t) return;

    if (ticketStatusPill) ticketStatusPill.textContent = t.status || "open";
    if (ticketId) ticketId.textContent = t.id || "—";
    if (ticketService) ticketService.textContent = t.service || "—";
    if (ticketUrgency) ticketUrgency.textContent = t.urgency || "—";
    if (ticketStatus) ticketStatus.textContent = t.status || "—";
    if (ticketAddress) ticketAddress.textContent = t.address || t.address_raw || "—";
    if (ticketAvailability) ticketAvailability.textContent = t.availability || "—";
    if (ticketPhone) ticketPhone.textContent = t.phone || (t.phone_declined ? "declined" : "—");

    if (ticketStatusPill) {
      ticketStatusPill.classList.remove("pill-danger", "pill-warn");
      if ((t.urgency || "").toLowerCase() === "urgent") ticketStatusPill.classList.add("pill-danger");
    }

    // Timeline (supports detail breadcrumbs)
    if (timeline) {
      timeline.innerHTML = "";
      const arr = Array.isArray(t.timeline) ? t.timeline : [];

      for (let i = arr.length - 1; i >= 0; i--) {
        const e = arr[i] || {};
        const div = document.createElement("div");
        div.className = "event";

        const detailHtml = e.detail
          ? `<div class="event-detail">${textToHtml(String(e.detail))}</div>`
          : "";

        div.innerHTML = `
          <div class="bubble-text">${textToHtml(e.label || "")}</div>
          ${detailHtml}
          <div class="msg-time">${escapeHtml(e.at || "")}</div>
        `;
        timeline.appendChild(div);
      }
    }
  }

  // =========================================================
  // ✅ UI CARD SUPPORT
  // =========================================================
  function isUiCard(ui) {
    if (!ui || typeof ui !== "object") return false;
    const t = String(ui.type || ui.kind || "").toLowerCase();
    return t === "card";
  }

  function cardTone(ui) {
    const t = String(ui?.tone || ui?.variant || "").toLowerCase().trim();
    return t || "neutral";
  }

  function cardTitle(ui) {
    return String(ui?.title || "").trim();
  }

  function cardIcon(ui) {
    return String(ui?.icon || "").trim();
  }

  function cardBodyText(ui) {
    const b = (ui && typeof ui.body === "string") ? ui.body : "";
    if (b && b.trim()) return b;
    const lines = Array.isArray(ui?.lines) ? ui.lines : [];
    const joined = lines.map(x => String(x ?? "")).join("\n");
    return joined.trim() ? joined : "";
  }

  function cardBullets(ui) {
    return Array.isArray(ui?.bullets) ? ui.bullets.map(x => String(x ?? "")) : [];
  }

  // =========================================================
  // ✅ COLOR SWAP MAPPING (CHAT.JS ONLY)
  // =========================================================
  function mapUiTypeToCssClass(type) {
    const t = String(type || "").toLowerCase();
    if (t === "urgent") return "safety";   // urgent => blue
    if (t === "safety") return "danger";   // safety => red
    return t;
  }

  function buildBadge(ui) {
    if (isUiCard(ui)) return "";
    if (!ui || !ui.type || !ui.label) return "";
    const icon = ui.icon ? `<span class="icon">${escapeHtml(ui.icon)}</span>` : "";
    const cssType = mapUiTypeToCssClass(ui.type);
    return `
      <div class="bubble-badge-row">
        <span class="bubble-badge ${escapeHtml(cssType)}">${icon}${escapeHtml(ui.label)}</span>
      </div>
    `;
  }

  function applyUiTypeToBubble(bubble, ui) {
    if (!bubble || !ui) return;

    if (isUiCard(ui)) {
      const tone = cardTone(ui);
      if (tone) bubble.classList.add(mapUiTypeToCssClass(tone));
      return;
    }

    if (!ui.type) return;
    const cssType = mapUiTypeToCssClass(ui.type);
    if (cssType) bubble.classList.add(cssType);
  }

  function bubbleHasSeverityClass(bubble) {
    if (!bubble) return false;
    return (
      bubble.classList.contains("urgent") ||
      bubble.classList.contains("safety") ||
      bubble.classList.contains("warning") ||
      bubble.classList.contains("danger")
    );
  }

  function inferUiTypeFromText(text) {
    const lo = String(text || "").toLowerCase();
    if (lo.includes("urgency:")) return null;
    if (lo.includes("safety warning")) return mapUiTypeToCssClass("safety");
    if (
      lo.includes("urgent — active hazard") ||
      lo.includes("**urgent") ||
      lo.includes("active hazard")
    ) return mapUiTypeToCssClass("urgent");
    return null;
  }

  function splitUrgentHazardIntoTwoBubbles(text) {
    const s = String(text || "");
    const lo = s.toLowerCase();

    if (!lo.includes("active hazard")) return null;

    const match = lo.match(/what['’]s the service address/);
    if (!match || match.index == null) return null;

    const idx = match.index;
    const top = s.slice(0, idx).trim();
    const rest = s.slice(idx).trim();

    if (!top || !rest) return null;
    return { top, rest };
  }

  function renderCardHeaderHtml(ui) {
    const icon = cardIcon(ui);
    const title = cardTitle(ui);
    if (!icon && !title) return "";
    const iconHtml = icon ? `${escapeHtml(icon)} ` : "";
    const safeTitle = title ? escapeHtml(title) : "";
    return `<div class="bubble-title">${iconHtml}${safeTitle}</div>`;
  }

  function renderCardBodyHtml(ui) {
    const body = cardBodyText(ui);
    const bullets = cardBullets(ui);

    let out = "";
    if (body && body.trim()) out += textToHtml(body);

    if (bullets && bullets.length) {
      const list = bullets
        .filter(b => String(b).trim())
        .map(b => `• ${String(b).trim()}`)
        .join("\n");
      if (list.trim()) {
        out += (out ? "<br><br>" : "");
        out += textToHtml(list);
      }
    }
    return out || "";
  }

  function addMessage(role, rawText, ui = null, atIso = null, id = null) {
    if (!chatInner) return;

    const mid = id || `${role}|${atIso || ""}|${rawText || ""}`;
    if (seenMsgIds.has(mid)) return;
    seenMsgIds.add(mid);

    const row = document.createElement("div");
    row.className = `msg ${role === "user" ? "user" : "bot"}`;

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = role === "user" ? "U" : "TD";

    const bubble = document.createElement("div");
    bubble.className = "bubble";

    const isCard = (role !== "user") && isUiCard(ui);

    const badgeHtml = (role === "bot" && !isCard) ? buildBadge(ui) : "";
    if (role !== "user") applyUiTypeToBubble(bubble, ui);

    const headerHtml = isCard ? renderCardHeaderHtml(ui) : "";
    const cardBodyHtml = isCard ? renderCardBodyHtml(ui) : "";

    const contentHtml = isCard
      ? `<div class="bubble-text">${cardBodyHtml || ""}</div>`
      : `<div class="bubble-text">${textToHtml(rawText)}</div>`;

    bubble.innerHTML = `
      ${badgeHtml}
      ${headerHtml}
      ${contentHtml}
      <div class="msg-time">${escapeHtml(stampFromIso(atIso))}</div>
    `;

    row.appendChild(avatar);
    row.appendChild(bubble);
    chatInner.appendChild(row);
  }

  function addBotMessageShell(ui = null, atIso = null) {
    if (!chatInner) return null;

    const row = document.createElement("div");
    row.className = "msg bot";

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = "TD";

    const bubble = document.createElement("div");
    bubble.className = "bubble";

    const iso = atIso || new Date().toISOString();
    const isCard = isUiCard(ui);

    const badgeHtml = isCard ? "" : buildBadge(ui);
    applyUiTypeToBubble(bubble, ui);

    const headerHtml = isCard ? renderCardHeaderHtml(ui) : "";

    bubble.innerHTML = `
      ${badgeHtml}
      ${headerHtml}
      <div class="bubble-text"></div>
      <div class="msg-time">${escapeHtml(stampFromIso(iso))}</div>
    `;

    row.appendChild(avatar);
    row.appendChild(bubble);
    chatInner.appendChild(row);

    return { row, bubble, textEl: bubble.querySelector(".bubble-text"), isCard };
  }

  async function typewriterInto(textEl, fullText) {
    if (!textEl) return;

    const text = String(fullText || "");
    const nearOnStart = isNearBottom(chat);

    const chunks = [];
    const parts = text.split(/(\s+)/);
    for (const p of parts) {
      if (!p) continue;
      chunks.push(p);
    }

    const msPerChar = msPerCharFor(text);
    let out = "";

    for (let i = 0; i < chunks.length; i++) {
      out += chunks[i];
      textEl.innerHTML = textToHtml(out);

      const nearNow = isNearBottom(chat);
      if (nearOnStart && nearNow) {
        if (newMsgPill) newMsgPill.style.display = "none";
        scrollToBottom();
      } else {
        if (newMsgPill) newMsgPill.style.display = "inline-flex";
      }

      const chunkLen = chunks[i].length;
      const isSpace = /^\s+$/.test(chunks[i]);

      const delay = isSpace
        ? clamp(10 + chunkLen * 1.5, 12, 60)
        : clamp(chunkLen * msPerChar, 18, 240);

      await sleep(delay);
    }

    await sleep(endPauseFor(text));
  }

  function normalizePartsAndUi(parts, uiParts) {
    const out = [];
    const pArr = Array.isArray(parts) ? parts : [];
    const uArr = Array.isArray(uiParts) ? uiParts : [];

    const n = Math.max(pArr.length, uArr.length);
    for (let i = 0; i < n; i++) {
      const raw = (i < pArr.length) ? pArr[i] : "";
      const ui = (i < uArr.length) ? uArr[i] : null;

      const text = String(raw ?? "");
      const trimmed = text.trim();

      if (trimmed.length > 0 || ui) {
        out.push({ text: trimmed.length ? text : "", ui });
      }
    }
    return out;
  }

  async function playBotReplyParts(parts, uiParts) {
    const items = normalizePartsAndUi(parts, uiParts);
    if (!items.length) {
      setState("Ready");
      return;
    }

    setState("Typing…");

    for (let i = 0; i < items.length; i++) {
      const part = items[i].text || "";
      const ui = items[i].ui || null;

      if (isUiCard(ui) && !String(part || "").trim()) {
        showTyping();
        await sleep(160);
        hideTyping();

        const shell = addBotMessageShell(ui, new Date().toISOString());
        if (shell && shell.textEl) {
          shell.textEl.innerHTML = renderCardBodyHtml(ui) || "";
        }

        const near = isNearBottom(chat);
        if (near) {
          if (newMsgPill) newMsgPill.style.display = "none";
          scrollToBottom();
        } else {
          if (newMsgPill) newMsgPill.style.display = "inline-flex";
        }

        await sleep(120);
        continue;
      }

      const split = splitUrgentHazardIntoTwoBubbles(part);
      if (split) {
        showTyping();
        await sleep(180);
        hideTyping();

        const shellA = addBotMessageShell({ type: "urgent", label: "URGENT", icon: "🚨" }, new Date().toISOString());
        if (shellA) {
          shellA.bubble.classList.add(mapUiTypeToCssClass("urgent"));
          await typewriterInto(shellA.textEl, split.top);
        }

        await sleep(120);

        showTyping();
        await sleep(140);
        hideTyping();

        const shellB = addBotMessageShell(null, new Date().toISOString());
        if (shellB) {
          shellB.bubble.classList.remove("urgent", "safety", "danger", "warning");
          await typewriterInto(shellB.textEl, split.rest);
        }

        const near2 = isNearBottom(chat);
        if (near2) {
          if (newMsgPill) newMsgPill.style.display = "none";
          scrollToBottom();
        } else {
          if (newMsgPill) newMsgPill.style.display = "inline-flex";
        }

        continue;
      }

      showTyping();
      await sleep(clamp(90 + part.length * 0.9, 120, 420));
      hideTyping();

      const shell = addBotMessageShell(ui, new Date().toISOString());
      if (!shell) continue;

      if (shell?.bubble && !bubbleHasSeverityClass(shell.bubble)) {
        const inferred = inferUiTypeFromText(part);
        if (inferred) shell.bubble.classList.add(inferred);
      }

      if (part) {
        await typewriterInto(shell.textEl, part);
      } else {
        shell.textEl.innerHTML = "";
        await sleep(80);
      }

      await sleep(clamp(80 + part.length * 0.15, 90, 220));

      const near = isNearBottom(chat);
      if (near) {
        if (newMsgPill) newMsgPill.style.display = "none";
        scrollToBottom();
      } else {
        if (newMsgPill) newMsgPill.style.display = "inline-flex";
      }
    }

    setState("Ready");
  }

  // ✅ double-send guard
  let __sending = false;

  async function sendMessage() {
    if (__sending) return;

    const text = (msgInput.value || "").trim();
    if (!text) return;

    if (!cid) {
      toast("err", "Missing client id", "window.TURBO_CLIENT_ID is not set in chat.html");
      return;
    }

    __sending = true;

    const near = isNearBottom(chat);

    addMessage("user", text, null, new Date().toISOString(), null);
    msgInput.value = "";
    msgInput.focus();
    if (near) scrollToBottom();

    setState("Thinking…");
    hideTyping();
    if (sendBtn) sendBtn.disabled = true;

    try {
      const res = await fetch(`/c/${encodeURIComponent(cid)}/api/chat`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, client_id: cid })
      });

      const ct = (res.headers.get("content-type") || "").toLowerCase();
      const data = ct.includes("application/json")
        ? await res.json()
        : { error: await res.text() };

      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);

      let parts = [];
      if (Array.isArray(data.reply_parts)) {
        parts = data.reply_parts;
      } else if (typeof data.reply_parts === "string" && data.reply_parts.trim()) {
        parts = [data.reply_parts];
      } else if (typeof data.reply === "string" && data.reply.trim()) {
        parts = [data.reply];
      } else if (typeof data.message === "string" && data.message.trim()) {
        parts = [data.message];
      }

      let uiParts = [];
      if (Array.isArray(data.ui_parts)) {
        uiParts = data.ui_parts;
      } else if (data.ui_parts && typeof data.ui_parts === "object") {
        uiParts = [data.ui_parts];
      } else if (Array.isArray(data.ui)) {
        uiParts = data.ui;
      } else if (data.ui && typeof data.ui === "object") {
        uiParts = [data.ui];
      }

      await playBotReplyParts(parts, uiParts);

      if (data.ticket) updateTicketUI(data.ticket);

      toast("ok", "Sent", "Reply received");
    } catch (e) {
      hideTyping();
      toast("err", "Error", String(e.message || e));

      addMessage(
        "bot",
        `Sorry — something failed.\n${String(e.message || e)}`,
        { type: "danger", label: "ERROR", icon: "⚠️" },
        new Date().toISOString(),
        null
      );
      scrollToBottom();
      setState("Error");
    } finally {
      hideTyping();
      if (sendBtn) sendBtn.disabled = false;
      __sending = false;
    }
  }

  async function newChat() {
    setState("Resetting…");

    resetTicketUI();
    seenMsgIds.clear();

    chatInner.innerHTML = `
      <div class="msg bot">
        <div class="avatar">TD</div>
        <div class="bubble">
          <div class="bubble-title">Hey — I’m Turbo Dispatch.</div>
          <div class="bubble-text">What’s going on today? (Example: “outlet sparking” or “toilet overflowing”)</div>
          <div class="msg-time">—</div>
        </div>
      </div>
    `;
    if (newMsgPill) newMsgPill.style.display = "none";
    scrollToBottom();

    toast("ok", "New chat", "Fresh ticket started");

    try {
      const res = await fetch(`/c/${encodeURIComponent(cid)}/api/reset`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({})
      });

      const data = await res.json().catch(() => null);
      if (data?.ticket) updateTicketUI(data.ticket);
      else resetTicketUI();
    } catch (_) {
      resetTicketUI();
    }

    setState("Ready");
    if (msgInput) {
      msgInput.value = "";
      msgInput.focus();
    }
  }

  async function hydrate() {
    try {
      const r = await fetch(`/c/${encodeURIComponent(cid)}/api/session`, { credentials: "same-origin" });
      if (!r.ok) return;
      const data = await r.json();

      const msgs = Array.isArray(data.messages) ? data.messages : [];
      if (msgs.length) {
        chatInner.innerHTML = "";

        msgs.sort((a, b) => String(a.at || "").localeCompare(String(b.at || "")));

        for (const m of msgs) {
          const mid = m.id || `${m.role}|${m.at}|${m.text}`;
          addMessage(m.role, m.text, m.ui || null, m.at || null, mid);
        }
      }

      if (data.ticket) updateTicketUI(data.ticket);
      else resetTicketUI();

      scrollToBottom();
      setState("Ready");
    } catch (_) {
      // no-op
    }
  }

  if (sendBtn) {
    sendBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();

      // ✅ mark submit as handled (avoids click+submit double-send)
      __submitJustHandled = true;

      sendMessage();
    });
  }

  if (msgInput) {
    msgInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        e.stopPropagation();

        // ✅ NEW: stop the form submit path from firing send a 2nd time
        __enterJustHandled = true;

        sendMessage();
      }
    });
  }

  if (newChatBtn) newChatBtn.addEventListener("click", newChat);

  if (newMsgPill) {
    newMsgPill.addEventListener("click", () => {
      newMsgPill.style.display = "none";
      scrollToBottom();
    });
  }

  setState("Loading…");
  hydrate();
})();
