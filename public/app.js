/* Task Check In — full-screen UI */
(function () {
  const state = {
    org: "",
    token: "",
    facility: "",
    task: null, // last loaded task payload from /api/load_task
    selectedLineNumber: null,
    transactions: [], // [{transactionId, strategyId, description}]
    selectedTransactionId: "",
    selectedStrategyId: "",
    defaultTransactionId: "",
  };

  const TRANSACTION_STORAGE_KEY = "tc_transaction_id";

  const el = {
    filtersScreen: document.getElementById("filtersScreen"),
    resultsScreen: document.getElementById("resultsScreen"),
    orgSection: document.getElementById("orgSection"),
    mainUI: document.getElementById("mainUI"),
    org: document.getElementById("org"),
    authBtn: document.getElementById("authBtn"),
    taskIdInput: document.getElementById("taskIdInput"),
    matchHint: document.getElementById("matchHint"),
    status: document.getElementById("status"),
    loadTaskBtn: document.getElementById("loadTaskBtn"),
    backToFilters: document.getElementById("backToFilters"),
    resultsStatus: document.getElementById("resultsStatus"),
    taskMeta: document.getElementById("taskMeta"),
    linesBody: document.getElementById("linesBody"),
    partialLineBtn: document.getElementById("partialLineBtn"),
    fullLineBtn: document.getElementById("fullLineBtn"),
    allLinesBtn: document.getElementById("allLinesBtn"),
    transactionIdSelect: document.getElementById("transactionIdSelect"),
    transactionIdHint: document.getElementById("transactionIdHint"),
    actionStatus: document.getElementById("actionStatus"),
    partialLineInfo: document.getElementById("partialLineInfo"),
    partialQtyInput: document.getElementById("partialQtyInput"),
    partialQtyUom: document.getElementById("partialQtyUom"),
    partialQtyHint: document.getElementById("partialQtyHint"),
    partialLineConfirmBtn: document.getElementById("partialLineConfirmBtn"),
    allLinesList: document.getElementById("allLinesList"),
    allLinesConfirmBtn: document.getElementById("allLinesConfirmBtn"),
    busyOverlay: document.getElementById("busyOverlay"),
    themeLogo: document.getElementById("themeLogo"),
    themeSelectorBtn: document.getElementById("themeSelectorBtn"),
    themeList: document.getElementById("themeList"),
  };

  /** Case-insensitive query params: org/organization, theme, task/taskid/task_id/task-id */
  function parseUrlParams() {
    const params = new URLSearchParams(window.location.search);
    const ci = {};
    for (const [key, value] of params.entries()) {
      ci[String(key).toLowerCase()] = value;
    }
    return {
      org: String(ci.org || ci.organization || "").trim(),
      theme: String(ci.theme || "").trim(),
      task: String(ci.task || ci.taskid || ci.task_id || ci["task-id"] || "").trim(),
    };
  }

  const urlParams = parseUrlParams();

  function setBusy(on, label) {
    el.busyOverlay.classList.toggle("visible", !!on);
    el.busyOverlay.textContent = label || "Working…";
  }

  function setStatus(msg, kind) {
    el.status.textContent = msg || "";
    el.status.className = "status-line flex-grow-1" + (kind ? " " + kind : "");
  }

  function setActionStatus(msg, kind) {
    el.actionStatus.textContent = msg || "";
    el.actionStatus.className = "status-line mb-2" + (kind ? " " + kind : "");
  }

  async function api(action, data) {
    const res = await fetch("/api/" + action, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data || {}),
    });
    let body = {};
    try {
      body = await res.json();
    } catch (_) {
      body = { success: false, error: "Invalid JSON response (" + res.status + ")" };
    }
    if (!res.ok && body && !body.error) {
      body.error = "Request failed (" + res.status + ")";
      body.success = false;
    }
    return body;
  }

  function fmtCount(n, singular, plural) {
    const count = Number(n) || 0;
    const word = count === 1 ? singular : plural || singular + "s";
    return count + " " + word;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function escapeAttr(s) {
    return escapeHtml(s);
  }

  function updateLoadButton() {
    const value = el.taskIdInput.value.trim();
    el.loadTaskBtn.disabled = !value;
    if (!state.token) {
      el.matchHint.textContent = "Authenticate to begin.";
    } else if (!value) {
      el.matchHint.textContent = "Scan or type a Task Id.";
    } else {
      el.matchHint.textContent = "Press Enter or click Load Task.";
    }
  }

  async function authenticate(org, options) {
    options = options || {};
    org = (org || "").trim().toUpperCase();
    if (!org) {
      setStatus("ORG is required", "error");
      return false;
    }
    if (!options.quiet) {
      setBusy(true, "Authenticating…");
      setStatus("Authenticating…");
    }
    try {
      const data = await api("auth", { org });
      if (!data.success) {
        setStatus(data.error || "Auth failed", "error");
        return false;
      }
      state.org = data.org || org;
      state.token = data.token;
      state.facility = state.org + "-DM1";
      el.org.value = state.org;
      el.orgSection.style.display = "none";
      el.mainUI.style.display = "block";
      el.taskIdInput.disabled = false;
      el.taskIdInput.focus();
      const via =
        data.source === "token-file"
          ? "via .token"
          : data.source === "oauth"
            ? "via OAuth"
            : "";
      setStatus("Authenticated " + via + ".", "success");
      updateLoadButton();
      await applyUrlTaskBoot();
      return true;
    } catch (e) {
      setStatus(e.message || String(e), "error");
      return false;
    } finally {
      if (!options.quiet) setBusy(false);
    }
  }

  function renderLines(lines) {
    state.selectedLineNumber = null;
    updateLineActionButtons();
    setActionStatus("");
    el.linesBody.innerHTML = (lines || [])
      .map(
        (line) => `
        <tr class="line-row" data-line-number="${escapeAttr(line.lineNumber)}">
          <td>${escapeHtml(line.lineNumber)}</td>
          <td>${escapeHtml(line.itemId)}</td>
          <td>${escapeHtml(line.description)}</td>
          <td>${escapeHtml(line.fromLocationId)}</td>
          <td>${escapeHtml(line.toLocationId)}</td>
          <td>${escapeHtml(line.lpnId)}</td>
          <td class="col-qty-wide">${escapeHtml(line.plannedQuantity)}</td>
          <td class="col-qty-wide">${escapeHtml(line.completedQuantity)}</td>
        </tr>`
      )
      .join("");
  }

  // --- Transaction ID ---

  function populateTransactionSelect() {
    const options = ['<option value="">-- Select --</option>'].concat(
      state.transactions.map(
        (t) => `<option value="${escapeAttr(t.transactionId)}">${escapeHtml(t.transactionId)}</option>`
      )
    );
    el.transactionIdSelect.innerHTML = options.join("");
  }

  function evaluateTransactionSelection() {
    const value = el.transactionIdSelect.value;
    const match = state.transactions.find((t) => t.transactionId === value);
    state.selectedTransactionId = match ? match.transactionId : "";
    state.selectedStrategyId = match ? match.strategyId : "";
    const valid = !!match;
    el.transactionIdSelect.classList.toggle("invalid", !valid);
    el.transactionIdHint.textContent = valid ? "" : "Required.";
    if (valid) {
      localStorage.setItem(TRANSACTION_STORAGE_KEY + ":" + (state.task ? state.task.taskType : ""), match.transactionId);
    }
    updateLineActionButtons();
  }

  async function preloadTransactions(taskType, taskTransactionId) {
    try {
      const data = await api("preload_task_transactions", {
        org: state.org,
        token: state.token,
        location: state.facility,
        taskType,
        taskTransactionId,
      });
      if (data.success) {
        state.transactions = data.entries || [];
        state.defaultTransactionId = data.defaultTransactionId || "";
      }
    } catch (e) {
      // Non-fatal here — evaluateTransactionSelection() will still correctly
      // leave the field blank/required if nothing loaded.
    }
    populateTransactionSelect();
    applyTransactionIdBoot(taskType);
  }

  function applyTransactionIdBoot(taskType) {
    const known = new Set(state.transactions.map((t) => t.transactionId));
    let value = "";
    const saved = localStorage.getItem(TRANSACTION_STORAGE_KEY + ":" + taskType);
    if (saved && known.has(saved)) {
      value = saved;
    } else if (known.has(state.defaultTransactionId)) {
      value = state.defaultTransactionId;
    }
    el.transactionIdSelect.value = value;
    evaluateTransactionSelection();
  }

  function updateLineActionButtons() {
    const hasSelection = state.selectedLineNumber !== null;
    const transactionOk = !!state.selectedTransactionId;
    el.partialLineBtn.disabled = !hasSelection || !transactionOk;
    el.fullLineBtn.disabled = !hasSelection || !transactionOk;
    el.allLinesBtn.disabled = !transactionOk;
  }

  function selectLine(lineNumber) {
    state.selectedLineNumber = lineNumber;
    el.linesBody.querySelectorAll("tr.line-row").forEach((row) => {
      row.classList.toggle("selected", row.dataset.lineNumber === String(lineNumber));
    });
    updateLineActionButtons();
  }

  function showResults() {
    el.filtersScreen.classList.remove("active");
    el.resultsScreen.classList.add("active");
  }

  function showFilters() {
    el.resultsScreen.classList.remove("active");
    el.filtersScreen.classList.add("active");
    el.taskIdInput.value = "";
    updateLoadButton();
    el.taskIdInput.focus();
  }

  async function fetchAndRenderTask(taskId) {
    const data = await api("load_task", {
      org: state.org,
      token: state.token,
      location: state.facility,
      taskId,
    });
    if (!data.success) {
      setStatus(data.error || "Load failed", "error");
      return false;
    }
    state.task = data;
    renderLines(data.lines);
    el.taskMeta.innerHTML = `
      <span><strong>Task</strong> ${escapeHtml(data.taskId)}</span>
      <span><strong>Type</strong> ${escapeHtml(data.taskTypeLabel || data.taskType)}</span>
      <span><strong>Status</strong> ${escapeHtml(data.taskStatus || "")}</span>
    `;
    el.resultsStatus.textContent = fmtCount(data.lineCount || 0, "line");
    await preloadTransactions(data.taskType, data.taskTransactionId);
    return true;
  }

  async function loadTask() {
    const taskId = el.taskIdInput.value.trim();
    if (!taskId) return;
    setBusy(true, "Loading task…");
    try {
      const ok = await fetchAndRenderTask(taskId);
      if (ok) showResults();
    } catch (e) {
      setStatus(e.message || String(e), "error");
    } finally {
      setBusy(false);
    }
  }

  let urlTaskBootApplied = false;
  async function applyUrlTaskBoot() {
    if (urlTaskBootApplied) return;
    urlTaskBootApplied = true;
    if (!urlParams.task) return;
    el.taskIdInput.value = urlParams.task.toUpperCase();
    updateLoadButton();
    if (!el.loadTaskBtn.disabled) await loadTask();
  }

  function getSelectedLine() {
    if (state.selectedLineNumber === null || !state.task) return null;
    return (state.task.lines || []).find(
      (l) => String(l.lineNumber) === String(state.selectedLineNumber)
    ) || null;
  }

  function remainingQty(line) {
    const rem = Number(line.plannedQuantity || 0) - Number(line.completedQuantity || 0);
    return rem > 0 ? rem : 0;
  }

  async function callCompleteLine(taskDetailId, mode, quantity) {
    return api("complete_line", {
      org: state.org,
      token: state.token,
      location: state.facility,
      taskId: state.task.taskId,
      taskDetailId,
      mode,
      quantity,
      transactionId: state.selectedTransactionId || "",
      strategyId: state.selectedStrategyId || "",
    });
  }

  async function completeFullLine() {
    const line = getSelectedLine();
    if (!line) return;
    const remaining = remainingQty(line);
    if (remaining <= 0) {
      setActionStatus("Line " + line.lineNumber + " is already complete.", "error");
      return;
    }
    setBusy(true, "Completing line " + line.lineNumber + "…");
    try {
      const result = await callCompleteLine(line.taskDetailId, "full");
      if (!result.success) {
        setActionStatus(result.error || "Complete failed", "error");
        return;
      }
      setActionStatus(
        "Completed " + result.quantity + " " + (result.uomId || "") +
          " on line " + line.lineNumber + ".",
        "success"
      );
      await fetchAndRenderTask(state.task.taskId);
    } catch (e) {
      setActionStatus(e.message || String(e), "error");
    } finally {
      setBusy(false);
    }
  }

  function openPartialModal() {
    const line = getSelectedLine();
    if (!line) return;
    const remaining = remainingQty(line);
    if (remaining <= 0) {
      setActionStatus("Line " + line.lineNumber + " is already complete.", "error");
      return;
    }
    el.partialLineInfo.innerHTML =
      "<strong>Line " + escapeHtml(line.lineNumber) + "</strong> — " +
      escapeHtml(line.itemId) + " " + escapeHtml(line.description) +
      "<br/>Remaining: " + escapeHtml(remaining) + " " + escapeHtml(line.uomId || "");
    el.partialQtyInput.value = remaining;
    el.partialQtyInput.max = remaining;
    el.partialQtyInput.min = 0;
    el.partialQtyUom.textContent = line.uomId || "";
    el.partialQtyHint.textContent = "";
    partialLineModal.show();
  }

  async function confirmPartialLine() {
    const line = getSelectedLine();
    if (!line) {
      partialLineModal.hide();
      return;
    }
    const remaining = remainingQty(line);
    const qty = Number(el.partialQtyInput.value);
    if (!qty || qty <= 0) {
      el.partialQtyHint.textContent = "Enter a quantity greater than 0.";
      return;
    }
    if (qty > remaining) {
      el.partialQtyHint.textContent = "Cannot exceed remaining quantity (" + remaining + ").";
      return;
    }
    partialLineModal.hide();
    setBusy(true, "Completing line " + line.lineNumber + "…");
    try {
      const result = await callCompleteLine(line.taskDetailId, "partial", qty);
      if (!result.success) {
        setActionStatus(result.error || "Complete failed", "error");
        return;
      }
      setActionStatus(
        "Completed " + result.quantity + " " + (result.uomId || "") +
          " on line " + line.lineNumber + ".",
        "success"
      );
      await fetchAndRenderTask(state.task.taskId);
    } catch (e) {
      setActionStatus(e.message || String(e), "error");
    } finally {
      setBusy(false);
    }
  }

  let allLinesPending = [];

  function openAllLinesModal() {
    if (!state.task) return;
    allLinesPending = (state.task.lines || []).filter((l) => remainingQty(l) > 0);
    if (!allLinesPending.length) {
      setActionStatus("No outstanding lines to complete.", "");
      return;
    }
    el.allLinesList.innerHTML = allLinesPending
      .map(
        (l) =>
          "<li>Line " + escapeHtml(l.lineNumber) + " — " + escapeHtml(l.itemId) + " " +
          escapeHtml(l.description) + ": " + escapeHtml(remainingQty(l)) + " " + escapeHtml(l.uomId || "") + "</li>"
      )
      .join("");
    allLinesModal.show();
  }

  async function confirmAllLines() {
    allLinesModal.hide();
    const total = allLinesPending.length;
    let succeeded = 0;
    const failures = [];
    for (let i = 0; i < total; i++) {
      const line = allLinesPending[i];
      setBusy(true, "Completing line " + (i + 1) + " of " + total + "…");
      try {
        const result = await callCompleteLine(line.taskDetailId, "full");
        if (result.success) {
          succeeded++;
        } else {
          failures.push("Line " + line.lineNumber + ": " + (result.error || "failed"));
        }
      } catch (e) {
        failures.push("Line " + line.lineNumber + ": " + (e.message || String(e)));
      }
    }
    setBusy(false);
    await fetchAndRenderTask(state.task.taskId);
    if (!failures.length) {
      setActionStatus("Completed " + fmtCount(succeeded, "line") + ".", "success");
    } else {
      setActionStatus(
        "Completed " + succeeded + " of " + total + " lines. Failures: " + failures.join("; "),
        "error"
      );
    }
  }

  // --- Wiring ---
  if (el.authBtn) {
    el.authBtn.addEventListener("click", () => authenticate(el.org.value));
  }
  el.org.addEventListener("keypress", (e) => {
    if (e.key === "Enter") authenticate(el.org.value);
  });
  el.taskIdInput.addEventListener("input", updateLoadButton);
  el.taskIdInput.addEventListener("keypress", (e) => {
    if (e.key === "Enter" && !el.loadTaskBtn.disabled) loadTask();
  });
  el.loadTaskBtn.addEventListener("click", loadTask);
  el.backToFilters.addEventListener("click", showFilters);
  el.linesBody.addEventListener("click", (e) => {
    const row = e.target.closest("tr.line-row");
    if (!row) return;
    selectLine(row.dataset.lineNumber);
  });

  const partialLineModal = window.bootstrap
    ? new window.bootstrap.Modal(document.getElementById("partialLineModal"))
    : null;
  const allLinesModal = window.bootstrap
    ? new window.bootstrap.Modal(document.getElementById("allLinesModal"))
    : null;

  el.fullLineBtn.addEventListener("click", completeFullLine);
  el.partialLineBtn.addEventListener("click", openPartialModal);
  el.partialLineConfirmBtn.addEventListener("click", confirmPartialLine);
  el.allLinesBtn.addEventListener("click", openAllLinesModal);
  el.allLinesConfirmBtn.addEventListener("click", confirmAllLines);

  el.transactionIdSelect.addEventListener("change", evaluateTransactionSelection);

  if (window.InspectionThemes) {
    // Theme=N hides the picker; Theme=<key> (case-insensitive) pre-selects a theme.
    if (urlParams.theme && urlParams.theme.toUpperCase() === "N") {
      el.themeSelectorBtn.style.display = "none";
    } else if (urlParams.theme) {
      const themes = window.InspectionThemes.themes;
      const themeKey = themes[urlParams.theme]
        ? urlParams.theme
        : themes[urlParams.theme.toLowerCase()]
          ? urlParams.theme.toLowerCase()
          : null;
      if (themeKey) localStorage.setItem("selectedTheme", themeKey);
    }
    const themeModalEl = document.getElementById("themeModal");
    const themeModal = window.bootstrap ? new window.bootstrap.Modal(themeModalEl) : null;
    window.InspectionThemes.wireThemePicker({
      themeSelectorBtn: el.themeSelectorBtn,
      themeModal,
      themeList: el.themeList,
      themeLogo: el.themeLogo,
    });
  }

  api("app_opened", {}).catch(() => {});

  // URL boot: Organization/org auto-authenticates (Task deep-link is applied
  // inside authenticate() once auth completes, see applyUrlTaskBoot()).
  if (urlParams.org) {
    el.org.value = urlParams.org.toUpperCase();
    authenticate(urlParams.org);
  } else {
    el.org.focus();
  }
})();
