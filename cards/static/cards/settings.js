"use strict";

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    const field = document.getElementById(button.dataset.copyTarget);
    const status = document.getElementById("mcp-copy-status");
    let copied = false;
    try {
      await navigator.clipboard.writeText(field.value);
      copied = true;
    } catch {
      // Local Wi-Fi previews use HTTP, where the Clipboard API is unavailable.
      field.focus({ preventScroll: true });
      field.select();
      field.setSelectionRange(0, field.value.length);
      try {
        copied = document.execCommand("copy");
      } catch {
        copied = false;
      }
      if (copied) button.focus({ preventScroll: true });
    }
    status.textContent = copied ? "Copied." : "Select the value and copy it manually.";
    button.textContent = copied ? "Copied" : "Copy";
    window.setTimeout(() => { button.textContent = "Copy"; }, 2000);
  });
});

document.querySelectorAll(".mcp-token-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const role = form.dataset.tokenRole;
    if (!window.confirm(`Generate a new ${role} token? This invalidates the previous ${role} token. Update any Codex connections using it.`)) return;
    const button = form.querySelector("button");
    const status = form.querySelector('[role="status"]');
    const field = document.getElementById(form.dataset.tokenTarget);
    const copyButton = field.parentElement.querySelector("[data-copy-target]");
    button.disabled = true;
    field.value = "";
    copyButton.disabled = true;
    status.textContent = "Generating…";
    try {
      const response = await fetch(form.action, {
        method: "POST", body: new FormData(form), credentials: "same-origin",
        cache: "no-store", redirect: "error",
      });
      if (!response.ok) throw new Error("Generation failed");
      const data = await response.json();
      if (typeof data.authorization !== "string" || !data.authorization.startsWith("Bearer ")) throw new Error("Invalid response");
      field.value = data.authorization;
      copyButton.disabled = false;
      button.textContent = `Regenerate ${role} token`;
      status.textContent = "Copy this value now. It disappears when you leave or reload this page.";
    } catch {
      status.textContent = "Could not display the token. It may have changed. Sign in again if needed, then generate a new token.";
    } finally {
      button.disabled = false;
    }
  });
});

// Back/forward navigation must not restore a secret from the page cache.
function clearGeneratedTokens() {
  document.querySelectorAll(".mcp-token-form").forEach((form) => {
    const field = document.getElementById(form.dataset.tokenTarget);
    field.value = "";
    field.parentElement.querySelector("[data-copy-target]").disabled = true;
    form.querySelector('[role="status"]').textContent = "";
  });
}
window.addEventListener("pagehide", clearGeneratedTokens);
window.addEventListener("pageshow", (event) => { if (event.persisted) clearGeneratedTokens(); });
