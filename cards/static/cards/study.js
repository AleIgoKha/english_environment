// Check the live queue without interrupting the card currently being studied.
const study = document.getElementById("study-live");
if (study) {
  let checking = false;
  async function refreshQueue() {
    if (checking || document.hidden) return;
    checking = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(study.dataset.statusUrl, {
        credentials: "same-origin", signal: controller.signal,
      });
      if (response.redirected) {
        window.location.assign(response.url);
        return;
      }
      if (!response.ok) return;
      const state = await response.json();
      if (state.response_token !== study.dataset.token ||
          String(state.revealed) !== study.dataset.revealed ||
          String(state.can_undo) !== study.dataset.canUndo) {
        window.location.reload();
        return;
      }
      study.querySelector(".study-progress .badge").textContent = `${state.remaining} left`;
    } catch {
      // Keep the current card visible and retry when the connection returns.
    } finally {
      clearTimeout(timeout);
      checking = false;
    }
  }
  setInterval(refreshQueue, 5000);
  document.addEventListener("visibilitychange", refreshQueue);
}
