// Renders the agent's A2A base URL from window.location.origin (so it survives
// reverse proxies) and wires up two copy buttons:
//   - "Base URL" copies the rendered URL.
//   - "API key" mints a fresh key via POST /agents/<id>/keys, then copies the
//     plaintext to the clipboard. The displayed value stays masked because the
//     server only reveals the plaintext in the mint response (one-shot).
(function () {
  function buildUrls(agentId) {
    return { base: `${window.location.origin}/agents/${agentId}` };
  }

  async function writeClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "absolute";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
  }

  function flash(button, label, ms = 1200) {
    const original = button.dataset.originalLabel || button.textContent;
    button.dataset.originalLabel = original;
    button.textContent = label;
    button.disabled = true;
    setTimeout(() => {
      button.textContent = original;
      button.disabled = false;
    }, ms);
  }

  async function copyText(text, button) {
    try {
      await writeClipboard(text);
      flash(button, "Copied");
    } catch (_err) {
      flash(button, "Failed");
    }
  }

  async function mintAndCopyKey(button, agentId, csrfToken) {
    const original = button.textContent;
    button.textContent = "Minting...";
    button.disabled = true;
    try {
      const fd = new FormData();
      fd.append("csrf_token", csrfToken);
      fd.append("label", "web-a2a");
      const resp = await fetch(`/agents/${agentId}/keys`, {
        method: "POST",
        body: fd,
        credentials: "same-origin",
      });
      if (!resp.ok) throw new Error(`mint failed: ${resp.status}`);
      const html = await resp.text();
      const match = html.match(/<code class="api-key-once__value">([^<]+)<\/code>/);
      if (!match) throw new Error("key not found in response");
      await writeClipboard(match[1]);
      button.textContent = original;
      button.disabled = false;
      flash(button, "Copied");
    } catch (_err) {
      button.textContent = original;
      button.disabled = false;
      flash(button, "Failed");
    }
  }

  function init() {
    document.querySelectorAll("[data-a2a-endpoints]").forEach((card) => {
      const agentId = card.getAttribute("data-agent-id");
      const csrfToken = card.getAttribute("data-csrf-token") || "";
      if (!agentId) return;

      const urls = buildUrls(agentId);
      card.querySelectorAll("[data-endpoint]").forEach((el) => {
        const which = el.getAttribute("data-endpoint");
        if (urls[which]) el.textContent = urls[which];
      });

      card.querySelectorAll("[data-copy-target]").forEach((btn) => {
        const which = btn.getAttribute("data-copy-target");
        btn.addEventListener("click", () => {
          if (which === "api-key") {
            mintAndCopyKey(btn, agentId, csrfToken);
          } else if (urls[which]) {
            copyText(urls[which], btn);
          }
        });
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
