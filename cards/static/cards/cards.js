// Selection is temporary browser state. Every change is confirmed on the server.
let refreshSelectionState = () => {};

function setupCardSelection() {
  refreshSelectionState = () => {};
  const selectionForm = document.getElementById("card-selection");
  if (!selectionForm) return;
  const checkboxes = [...selectionForm.querySelectorAll(".word-checkbox")];
  const countLabel = document.getElementById("selection-count");
  const selectAllButton = selectionForm.querySelector(".selection-all");
  const actionButtons = selectionForm.querySelectorAll(".selection-buttons button");
  const resetButton = selectionForm.querySelector('.selection-buttons button[value="reset"]');
  const deleteButton = selectionForm.querySelector('.selection-buttons button[value="delete"]');
  selectionForm.classList.add("selection-ready");

  function updateSelection() {
    const selected = checkboxes.filter((checkbox) => checkbox.checked);
    const count = selected.length;
    const deletedCount = selected.filter((checkbox) => checkbox.dataset.deleted === "true").length;
    resetButton.textContent = deletedCount ? (deletedCount === count ? "Restore" : "Reset / Restore") : "Reset";
    deleteButton.textContent = count && deletedCount === count ? "Permanently delete" : "Delete";
    selectionForm.classList.toggle("selection-active", count > 0);
    countLabel.textContent = `${count} selected`;
    selectAllButton.textContent = count === checkboxes.length ? "Deselect all" : "Select all";
    actionButtons.forEach((button) => { button.disabled = count === 0; });
  }

  selectionForm.querySelectorAll(".select-word").forEach((button) => {
    button.addEventListener("click", () => {
      const checkbox = checkboxes.find((item) => item.value === button.dataset.wordId);
      checkbox.checked = true;
      selectionForm.querySelectorAll(".word-actions").forEach((menu) => { menu.open = false; });
      updateSelection();
      checkbox.focus();
    });
  });
  checkboxes.forEach((checkbox) => checkbox.addEventListener("change", updateSelection));
  selectAllButton.addEventListener("click", () => {
    const selectAll = checkboxes.some((checkbox) => !checkbox.checked);
    checkboxes.forEach((checkbox) => { checkbox.checked = selectAll; });
    updateSelection();
  });
  selectionForm.querySelector(".selection-cancel").addEventListener("click", () => {
    checkboxes.forEach((checkbox) => { checkbox.checked = false; });
    updateSelection();
  });
  refreshSelectionState = updateSelection;
  updateSelection();
}


setupCardSelection();
window.addEventListener("pageshow", () => refreshSelectionState());

const importButton = document.getElementById("import-csv-button");
const importForm = document.getElementById("csv-import-form");
if (importButton && importForm) {
  const fileInput = document.getElementById("csv-import-file");
  const importError = document.getElementById("import-error");
  importButton.addEventListener("click", () => {
    fileInput.value = "";
    importError.hidden = true;
    fileInput.click();
  });
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files[0];
    if (!file) return;
    importError.hidden = true;
    if (!file.name.toLowerCase().endsWith(".csv") || file.size > 2 * 1024 * 1024) {
      importError.textContent = "Choose a .csv file smaller than 2 MB.";
      importError.hidden = false;
      return;
    }
    importButton.disabled = true;
    importButton.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(importForm.action, {
        method: "POST", body: new FormData(importForm),
        headers: {"Accept": "application/json"}, credentials: "same-origin",
      });
      if (response.redirected) {
        window.location.assign(response.url);
        return;
      }
      const result = await response.json();
      if (!response.ok) {
        importError.textContent = result.errors?.join(" ") || "This CSV could not be read.";
        importError.hidden = false;
        return;
      }
      window.location.assign(result.preview_url);
    } catch {
      importError.textContent = "Couldn't upload the CSV. Check your connection and choose the file again.";
      importError.hidden = false;
    } finally {
      importButton.disabled = false;
      importButton.removeAttribute("aria-busy");
    }
  });
}

const filters = document.querySelector(".card-filters");
if (filters) {
  const results = document.getElementById("card-results");
  const errorLabel = document.getElementById("filter-error");
  let timer;
  let pendingRequest;
  let revision = 0;

  async function refreshCards(currentRevision) {
    const url = new URL(filters.action);
    url.search = new URLSearchParams(new FormData(filters)).toString();
    pendingRequest = new AbortController();
    const signal = pendingRequest.signal;
    results.setAttribute("aria-busy", "true");
    errorLabel.hidden = true;
    try {
      const response = await fetch(url, {signal, credentials: "same-origin"});
      if (signal.aborted || currentRevision !== revision) return;
      if (response.redirected) {
        window.location.assign(response.url);
        return;
      }
      if (!response.ok) throw new Error("Filter request failed");
      const html = await response.text();
      if (signal.aborted || currentRevision !== revision) return;
      const documentFragment = new DOMParser().parseFromString(html, "text/html");
      const replacement = documentFragment.getElementById("card-results");
      if (!replacement) throw new Error("Missing card results");
      results.replaceChildren(...replacement.childNodes);
      setupCardSelection();
      window.history.replaceState(null, "", url);
    } catch (error) {
      if (error.name !== "AbortError" && currentRevision === revision) {
        errorLabel.textContent = "Couldn't refresh the list. Check your connection and press Enter to retry.";
        errorLabel.hidden = false;
      }
    } finally {
      if (currentRevision === revision) results.removeAttribute("aria-busy");
    }
  }

  function scheduleRefresh(delay = 0) {
    clearTimeout(timer);
    if (pendingRequest) pendingRequest.abort();
    revision += 1;
    const currentRevision = revision;
    timer = setTimeout(() => refreshCards(currentRevision), delay);
  }

  filters.querySelector("select").addEventListener("change", () => scheduleRefresh());
  filters.querySelector("input[type=search]").addEventListener("input", (event) => {
    if (!event.isComposing) scheduleRefresh(180);
  });
  filters.querySelector("input[type=search]").addEventListener("compositionend", () => scheduleRefresh(180));
  filters.addEventListener("submit", (event) => {
    event.preventDefault();
    scheduleRefresh();
  });
}
