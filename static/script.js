(function () {
  const el = (id) => document.getElementById(id);

  // Chat page
  const chatBox = el("chatBox");
  const sendBtn = el("sendBtn");
  const msgInput = el("messageInput");
  const typingIndicator = el("typingIndicator");
  const clientIdInput = el("clientIdInput");
  const applyClientBtn = el("applyClientBtn");

  // System panel
  const sysStatus = el("sysStatus");
  const sysClient = el("sysClient");
  const emailStatus = el("emailStatus");
  const priorityStatus = el("priorityStatus");

  // Admin page
  const refreshAdminBtn = el("refreshAdminBtn");
  const leadsList = el("leadsList");

  let clientId = "default";

  function setTyping(on) {
    if (!typingIndicator) return;
    typingIndicator.classList.toggle("hidden", !on);
  }

  function addMessage(role, text) {
    if (!chatBox) return;
    const row = document.createElement("div");
    row.className = `msg ${role}`;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text ?? "";
    row.appendChild(bubble);
    chatBox.appendChild(row);
    chatBox.scrollTop = chatBox.scrollHeight;
  }

  async function healthCheck() {
    try {
      const res = await fetch("/health", { cache: "no-store" });
      const data = await res.json();
      if (sysStatus) sysStatus.textContent = data.status === "ok" ? "Online" : "Issue";
      return data;
    } catch {
      if (sysStatus) sysStatus.textContent = "Offline";
      return null;
    }
  }

  function applyClient() {
    const next = (clientIdInput?.value || "").trim() || "default";
    clientId = next;
    if (sysClient) sysClient.textContent = clientId;
    if (chatBox) addMessage("bot", `Client set to: ${clientId}`);
  }

  async function sendChat() {
    const text = (msgInput?.value || "").trim();
    if (!text) return;

    addMessage("user", text);
    msgInput.value = "";
    msgInput.focus();
    setTyping(true);

    try {
      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, client_id: clientId })
      });

      const data = await res.json();

      if (!res.ok) {
        addMessage("bot", `Server error: ${data.error || "unknown_error"}`);
        return;
      }

      addMessage("bot", data.response || "—");
      if (priorityStatus) priorityStatus.textContent = data.priority || "—";
      if (emailStatus) emailStatus.textContent = data.email_status || "—";

    } catch (e) {
      addMessage("bot", "Server error: network_failure");
    } finally {
      setTyping(false);
    }
  }

  // Admin loader
  async function loadAdmin() {
    if (!leadsList) return;
    leadsList.innerHTML = "";
    try {
      const res = await fetch("/admin/data", { cache: "no-store" });
      const data = await res.json();
      const leads = data.leads || [];

      if (leads.length === 0) {
        leadsList.innerHTML = `<div class="item"><div class="itemTitle">No leads yet</div><div class="itemSub">Send a test chat message.</div></div>`;
        return;
      }

      for (const l of leads) {
        const div = document.createElement("div");
        div.className = "item";
        div.innerHTML = `
          <div class="itemTitle">${escapeHtml(l.priority || "NORMAL")} • ${escapeHtml(l.client_id || "default")}</div>
          <div class="itemSub">${escapeHtml(l.time || "")}</div>
          <div class="itemBody">${escapeHtml(l.message || "")}</div>
        `;
        leadsList.appendChild(div);
      }
    } catch {
      leadsList.innerHTML = `<div class="item"><div class="itemTitle">Admin load failed</div><div class="itemSub">Server offline or /admin/data error.</div></div>`;
    }
  }

  function escapeHtml(str) {
    return String(str)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  document.addEventListener("DOMContentLoaded", async () => {
    // Chat page init
    if (chatBox) {
      await healthCheck();
      if (sysClient) sysClient.textContent = clientId;
      addMessage("bot", "Hi — describe your issue and I’ll route it.");
      setTyping(false);
    }

    // Chat bindings (safe)
    if (sendBtn) sendBtn.addEventListener("click", sendChat);
    if (msgInput) msgInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") sendChat();
    });
    if (applyClientBtn) applyClientBtn.addEventListener("click", applyClient);
    if (clientIdInput) clientIdInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") applyClient();
    });

    // Admin bindings
    if (refreshAdminBtn) refreshAdminBtn.addEventListener("click", loadAdmin);
    if (leadsList) loadAdmin();
  });
})();
