"use strict";

const settingsForm = document.getElementById("learning-settings-form");
if (settingsForm) {
  const saveButton = settingsForm.querySelector(".settings-save");
  const resetSaved = () => {
    saveButton.textContent = "Save";
    saveButton.classList.remove("is-saved");
  };
  settingsForm.addEventListener("input", resetSaved);
  settingsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (saveButton.disabled) return;
    const error = document.getElementById("settings-save-error");
    const submitted = new FormData(settingsForm);
    error.hidden = true;
    saveButton.disabled = true;
    saveButton.textContent = "Saving…";
    try {
      const response = await fetch(settingsForm.action, {
        method: "POST", body: submitted, credentials: "same-origin", cache: "no-store",
        redirect: "error", headers: { "Accept": "application/json" },
      });
      const result = await response.json();
      if (!response.ok) {
        error.textContent = Object.values(result.errors || {}).flat().map((item) => item.message).join(" ") || "Could not save. Please try again.";
        error.hidden = false;
        resetSaved();
        return;
      }
      if (result.saved !== true) throw new Error("Save not confirmed");
      const unchanged = settingsForm.elements.review_target.value === submitted.get("review_target");
      saveButton.textContent = unchanged ? "Saved" : "Save";
      saveButton.classList.toggle("is-saved", unchanged);
    } catch {
      resetSaved();
      error.textContent = "Could not save. Check your connection and try again.";
      error.hidden = false;
    } finally {
      saveButton.disabled = false;
    }
  });
}

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    const field = document.getElementById(button.dataset.copyTarget);
    const copyLabel = button.getAttribute("aria-label");
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
    button.textContent = copied ? "Copied" : "Copy";
    button.title = copied ? "" : "Select the value and copy it manually.";
    button.setAttribute("aria-label", copied ? "Copied" : "Select the value and copy it manually.");
    window.setTimeout(() => {
      button.textContent = "Copy";
      button.setAttribute("aria-label", copyLabel);
    }, 2000);
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
