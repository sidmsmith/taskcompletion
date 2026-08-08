/* Task Check In — full-screen UI */
(function () {
  const state = {
    org: "",
    token: "",
    facility: "",
    task: null, // last loaded task payload from /api/load_task
    selectedLineNumber: null,
  };

  // Hardcoded for now — Putaway is the only task type wired to a real
  // completion call. Revisit once Picking/Cycle Count/Replenishment are
  // wired (each will likely need its own TransactionId, driven by
  // taskType — see mawm_client.DEFAULT_TRANSACTION_BY_TASK_TYPE).
  const TRANSACTION_ID = "Putaway";

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
    actionStatus: document.getElementById("actionStatus"),
    partialLineInfo: document.getElementById("partialLineInfo"),
    partialQtyInput: document.getElementById("partialQtyInput"),
    partialQtyUom: document.getElementById("partialQtyUom"),
    partialQtyHint: document.getElementById("partialQtyHint"),
    partialLineConfirmBtn: document.getElementById("partialLineConfirmBtn"),
    allLinesList: document.getElementById("allLinesList"),
    allLinesConfirmBtn: document.getElementById("allLinesConfirmBtn"),
    warningMessageId: document.getElementById("warningMessageId"),
    warningMessageText: document.getElementById("warningMessageText"),
    warningConfirmBtn: document.getElementById("warningConfirmBtn"),
    reasonCodeInfo: document.getElementById("reasonCodeInfo"),
    reasonCodeSelect: document.getElementById("reasonCodeSelect"),
    reasonCodeHint: document.getElementById("reasonCodeHint"),
    reasonCodeConfirmBtn: document.getElementById("reasonCodeConfirmBtn"),
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
          <td>
            <input
              type="text"
              class="form-control to-location-input"
              data-task-detail-id="${escapeAttr(line.taskDetailId)}"
              data-default-location="${escapeAttr(line.toLocationId)}"
              value="${escapeAttr(line.toLocationId)}"
              autocomplete="off"
            />
          </td>
          <td>${escapeHtml(line.lpnId)}</td>
          <td class="col-qty-wide">${escapeHtml(line.plannedQuantity)}</td>
          <td class="col-qty-wide">${escapeHtml(line.completedQuantity)}</td>
        </tr>`
      )
      .join("");
  }

  /** Completed/Canceled = red, everything else = green (for now). */
  function statusBadgeClass(statusLabel) {
    const text = String(statusLabel || "").trim().toLowerCase();
    if (text === "completed" || text.startsWith("cancel")) return "status-chip status-red";
    return "status-chip status-green";
  }

  function statusBadgeHtml(statusLabel, statusId) {
    const text = statusLabel || statusId || "";
    if (!text) return "";
    return `<span class="badge ${statusBadgeClass(statusLabel)}">${escapeHtml(text)}</span>`;
  }

  /**
   * Reads the currently-typed destination for a line and returns it only
   * if it differs from what was originally loaded (blank/unchanged means
   * "use the default system-directed destination", per
   * task_service.complete_putaway_line()'s dispatch rule).
   */
  function getLocationOverride(taskDetailId) {
    const input = el.linesBody.querySelector(
      '.to-location-input[data-task-detail-id="' + CSS.escape(String(taskDetailId)) + '"]'
    );
    if (!input) return "";
    const value = input.value.trim();
    const original = (input.dataset.defaultLocation || "").trim();
    if (!value || value.toUpperCase() === original.toUpperCase()) return "";
    return value;
  }

  function updateLineActionButtons() {
    const hasSelection = state.selectedLineNumber !== null;
    el.partialLineBtn.disabled = !hasSelection;
    el.fullLineBtn.disabled = !hasSelection;
    el.allLinesBtn.disabled = false;
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
      <span><strong>Status</strong> ${statusBadgeHtml(data.taskStatusLabel, data.taskStatus)}</span>
    `;
    el.resultsStatus.textContent = fmtCount(data.lineCount || 0, "line");
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

  async function callCompleteLine(taskDetailId, mode, quantity, warningOverrides, toLocationId, reasonCodeId) {
    return api("complete_line", {
      org: state.org,
      token: state.token,
      location: state.facility,
      taskId: state.task.taskId,
      taskDetailId,
      mode,
      quantity,
      transactionId: TRANSACTION_ID,
      warningOverrides: warningOverrides || undefined,
      toLocationId: toLocationId || undefined,
      reasonCodeId: reasonCodeId || undefined,
    });
  }

  /**
   * Substitute Location requires a reason code (see
   * task_service.complete_putaway_line()). Fetches the live reason-code
   * list fresh each time (small, static-ish lookup — not worth caching)
   * and shows a required-selection modal. Resolves the selected value,
   * or null if the user cancels / picks nothing and closes the modal.
   */
  async function promptReasonCode(line, toLocationId) {
    el.reasonCodeInfo.textContent =
      "Line " + line.lineNumber + " destination changed to " + toLocationId + ".";
    el.reasonCodeSelect.innerHTML = '<option value="">Loading…</option>';
    el.reasonCodeHint.textContent = "";
    reasonCodeModal.show();
    try {
      const data = await api("preload_putaway_reason_codes", {
        org: state.org,
        token: state.token,
        location: state.facility,
      });
      const entries = data.success ? data.entries || [] : [];
      el.reasonCodeSelect.innerHTML = ['<option value="">-- Select --</option>']
        .concat(
          entries.map(
            (e) => `<option value="${escapeAttr(e.value)}">${escapeHtml(e.key)}</option>`
          )
        )
        .join("");
      if (!entries.length) {
        el.reasonCodeHint.textContent = data.error || "No reason codes available.";
      }
    } catch (e) {
      el.reasonCodeSelect.innerHTML = '<option value="">-- Select --</option>';
      el.reasonCodeHint.textContent = e.message || String(e);
    }

    return new Promise((resolve) => {
      let resolved = false;
      function finish(result) {
        if (resolved) return;
        resolved = true;
        el.reasonCodeConfirmBtn.removeEventListener("click", onConfirm);
        reasonCodeModalEl.removeEventListener("hidden.bs.modal", onHidden);
        resolve(result);
      }
      function onConfirm() {
        const value = el.reasonCodeSelect.value;
        if (!value) {
          el.reasonCodeHint.textContent = "A reason code is required.";
          return;
        }
        reasonCodeModal.hide();
        finish(value);
      }
      function onHidden() {
        finish(null);
      }
      el.reasonCodeConfirmBtn.addEventListener("click", onConfirm);
      reasonCodeModalEl.addEventListener("hidden.bs.modal", onHidden);
    });
  }

  /** Resolves true (Confirm) or false (Cancel / closed / backdrop). */
  function showWarningModal(messageId, messageText) {
    return new Promise((resolve) => {
      let resolved = false;
      function finish(result) {
        if (resolved) return;
        resolved = true;
        el.warningConfirmBtn.removeEventListener("click", onConfirm);
        warningModalEl.removeEventListener("hidden.bs.modal", onHidden);
        resolve(result);
      }
      function onConfirm() {
        warningModal.hide();
        finish(true);
      }
      function onHidden() {
        finish(false);
      }
      el.warningMessageId.textContent = messageId || "";
      el.warningMessageText.textContent = messageText || "";
      el.warningConfirmBtn.addEventListener("click", onConfirm);
      warningModalEl.addEventListener("hidden.bs.modal", onHidden);
      warningModal.show();
    });
  }

  /**
   * Calls complete_line, and if the response comes back with a MAWM
   * warning (`result.warning === true`), shows the Confirm/Cancel modal
   * and — on Confirm — retries with that warning's code added to
   * warningOverrides. Loops in case a second, different warning follows
   * the first confirmation. Returns the final result (success or a plain
   * failure), or `{ success: false, cancelled: true }` if the user
   * cancels out of a warning.
   */
  async function completeLineWithWarningHandling(taskDetailId, mode, quantity, toLocationId, reasonCodeId) {
    const overrides = {};
    let result = await callCompleteLine(taskDetailId, mode, quantity, overrides, toLocationId, reasonCodeId);
    while (result && result.warning) {
      const confirmed = await showWarningModal(result.messageId, result.messageText);
      if (!confirmed) {
        return { success: false, cancelled: true, error: "Cancelled after warning." };
      }
      overrides[result.messageId] = result.messageId;
      result = await callCompleteLine(taskDetailId, mode, quantity, overrides, toLocationId, reasonCodeId);
    }
    return result;
  }

  async function completeFullLine() {
    const line = getSelectedLine();
    if (!line) return;
    const remaining = remainingQty(line);
    if (remaining <= 0) {
      setActionStatus("Line " + line.lineNumber + " is already complete.", "error");
      return;
    }
    const toLocationId = getLocationOverride(line.taskDetailId);
    let reasonCodeId = null;
    if (toLocationId) {
      reasonCodeId = await promptReasonCode(line, toLocationId);
      if (!reasonCodeId) {
        setActionStatus("Cancelled — a reason code is required to change the destination.", "");
        return;
      }
    }
    setBusy(true, "Completing line " + line.lineNumber + "…");
    try {
      const result = await completeLineWithWarningHandling(line.taskDetailId, "full", undefined, toLocationId, reasonCodeId);
      if (!result.success) {
        if (!result.cancelled) setActionStatus(result.error || "Complete failed", "error");
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
    let cancelled = false;
    const failures = [];
    for (let i = 0; i < total; i++) {
      const line = allLinesPending[i];
      const toLocationId = getLocationOverride(line.taskDetailId);
      let reasonCodeId = null;
      if (toLocationId) {
        setBusy(false);
        reasonCodeId = await promptReasonCode(line, toLocationId);
        if (!reasonCodeId) {
          cancelled = true;
          break;
        }
      }
      setBusy(true, "Completing line " + (i + 1) + " of " + total + "…");
      try {
        const result = await completeLineWithWarningHandling(line.taskDetailId, "full", undefined, toLocationId, reasonCodeId);
        if (result.success) {
          succeeded++;
        } else if (result.cancelled) {
          cancelled = true;
          break;
        } else {
          failures.push("Line " + line.lineNumber + ": " + (result.error || "failed"));
        }
      } catch (e) {
        failures.push("Line " + line.lineNumber + ": " + (e.message || String(e)));
      }
    }
    setBusy(false);
    await fetchAndRenderTask(state.task.taskId);
    if (cancelled) {
      setActionStatus("Completed " + fmtCount(succeeded, "line") + " before cancelling.", "");
    } else if (!failures.length) {
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
  el.linesBody.addEventListener("input", (e) => {
    const input = e.target.closest(".to-location-input");
    if (!input) return;
    const value = input.value.trim().toUpperCase();
    const original = (input.dataset.defaultLocation || "").trim().toUpperCase();
    input.classList.toggle("overridden", !!value && value !== original);
  });

  const partialLineModal = window.bootstrap
    ? new window.bootstrap.Modal(document.getElementById("partialLineModal"))
    : null;
  const allLinesModal = window.bootstrap
    ? new window.bootstrap.Modal(document.getElementById("allLinesModal"))
    : null;
  const warningModalEl = document.getElementById("warningModal");
  const warningModal = window.bootstrap ? new window.bootstrap.Modal(warningModalEl) : null;
  const reasonCodeModalEl = document.getElementById("reasonCodeModal");
  const reasonCodeModal = window.bootstrap ? new window.bootstrap.Modal(reasonCodeModalEl) : null;

  el.fullLineBtn.addEventListener("click", completeFullLine);
  el.partialLineBtn.addEventListener("click", openPartialModal);
  el.partialLineConfirmBtn.addEventListener("click", confirmPartialLine);
  el.allLinesBtn.addEventListener("click", openAllLinesModal);
  el.allLinesConfirmBtn.addEventListener("click", confirmAllLines);

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
