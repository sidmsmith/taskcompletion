/* Task Check In — full-screen UI */
(function () {
  const state = {
    org: "",
    token: "",
    facility: "",
    // One entry per task/container resolved by the last search (2026-08-08,
    // multi-LPN search) — each carries its own mode/taskId/containerId/
    // status and its own `lines`. Every line is also denormalized with
    // its owning group's identity (line.groupMode/groupTaskId/etc, see
    // task_service.resolve_search_multi()) so nothing else in this file
    // needs to cross-reference back into `groups` for a given row.
    groups: [],
    lastSearchValue: "", // raw search box text, re-used to refresh after a completion
    lastSearchMode: "task", // which endpoint/table the last successful load used — see reloadCurrentSearch()
    selectedTaskDetailId: null, // taskDetailId is globally unique across every group, see resolve_search_multi()'s docstring
    storageLocations: null, // Set of valid location strings once preloaded, see preloadStorageLocations()
    adjustmentReasonCodes: null, // [{key,value}] once preloaded, see preloadAdjustmentReasonCodes()
    // "task" (Task Id/iLPN search, the existing #linesTable) or
    // "cycle_count" (Storage location search, the separate
    // #cycleCountLinesTable — per explicit instruction, ad hoc Cycle
    // Count gets its own table rather than conditional columns on the
    // putaway one, since the two have almost nothing in common: no LPN,
    // no To Location, no planned/completed distinction, and Item is
    // editable here when it isn't anywhere else). Set by
    // classifySearchInput() before a search even runs, so the Load
    // button can already gate on it.
    searchMode: "task",
    // Cycle count selection is tracked by groupKey, not taskDetailId
    // (2026-08-08 — see cycleCountGroupRowsHtml()'s docstring): a
    // location's items complete atomically together, so "select a
    // line" for cycle count really means "select a location."
    selectedCycleCountGroupKey: null,
  };

  function allLines() {
    return state.groups.flatMap((g) => g.lines || []);
  }

  // Mirrors mawm_client.ILPN_CONSUMED_STATUS (2026-08-08, seventh
  // session) — a consumed LPN's inventory already moved to a location
  // record; nothing left on the LPN to adjust or complete. See
  // isConsumedLine() below.
  const ILPN_CONSUMED_STATUS = "9000";

  function isConsumedLine(line) {
    return line.ilpnStatus === ILPN_CONSUMED_STATUS;
  }

  // Fallback only — the real list is preloaded once per session from
  // mawm_client.ADJUSTMENT_REASON_CODES via /api/preload_adjustment_
  // reason_codes (see preloadAdjustmentReasonCodes() below), the single
  // source of truth. This mirrors it just so a row can render sane
  // options in the brief window before that preload resolves (or if it
  // fails outright) — same fallback pattern as storageLocations.
  const FALLBACK_REASON_CODES = [
    { key: "Charity", value: "CH" },
    { key: "Inventory Adjustment", value: "IA" },
    { key: "Inventory Damaged", value: "DM" },
    { key: "Inventory Delete", value: "ID" },
    { key: "Lost in cycle count", value: "LC" },
    { key: "Mass Inventory Movement", value: "MM" },
  ];
  async function preloadAdjustmentReasonCodes() {
    try {
      const data = await api("preload_adjustment_reason_codes", {});
      if (data.success) state.adjustmentReasonCodes = data.entries || [];
    } catch (e) {
      // Leave state.adjustmentReasonCodes null — reasonCodeOptionsHtml()
      // falls back to FALLBACK_REASON_CODES.
    }
  }

  /**
   * Always starts on the "Select Reason" placeholder (2026-08-08, per
   * explicit instruction) — no default reason code is pre-picked
   * anymore, forcing an actual choice. `.reason-code-select`'s own
   * `invalid` class (styled red, see index.html) starts applied in the
   * row templates and is cleared once a real value is chosen — see the
   * delegated `change` handler and isReasonValid().
   */
  function reasonCodeOptionsHtml() {
    const codes = state.adjustmentReasonCodes || FALLBACK_REASON_CODES;
    const realOptions = codes
      .map((c) => `<option value="${escapeAttr(c.value)}">${escapeHtml(c.key)}</option>`)
      .join("");
    return '<option value="" selected>Select Reason</option>' + realOptions;
  }

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
    linesTable: document.getElementById("linesTable"),
    linesBody: document.getElementById("linesBody"),
    cycleCountLinesTable: document.getElementById("cycleCountLinesTable"),
    cycleCountLinesBody: document.getElementById("cycleCountLinesBody"),
    fullLineBtn: document.getElementById("fullLineBtn"),
    allLinesBtn: document.getElementById("allLinesBtn"),
    actionStatus: document.getElementById("actionStatus"),
    allLinesList: document.getElementById("allLinesList"),
    allLinesConfirmBtn: document.getElementById("allLinesConfirmBtn"),
    warningMessageId: document.getElementById("warningMessageId"),
    warningMessageText: document.getElementById("warningMessageText"),
    warningConfirmBtn: document.getElementById("warningConfirmBtn"),
    reasonCodeInfo: document.getElementById("reasonCodeInfo"),
    reasonCodeSelect: document.getElementById("reasonCodeSelect"),
    reasonCodeHint: document.getElementById("reasonCodeHint"),
    reasonCodeConfirmBtn: document.getElementById("reasonCodeConfirmBtn"),
    transactionIdValue: document.getElementById("transactionIdValue"),
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

  const SEARCH_INPUT_SPLIT_RE = /[;,\s]+/;

  /**
   * Classifies the search box's raw text as "task" (Task Id/iLPN
   * tokens), "cycle_count" (all tokens are recognized Storage
   * locations), "empty", or "mixed" — mixing a location with a Task Id/
   * iLPN in the same search is disallowed outright (2026-08-08, per
   * explicit instruction: "the prompt should not allow both lpns, and
   * locations. if both are entered, then the load button should be
   * disabled") rather than guessing which one the user meant.
   *
   * Relies on state.storageLocations (see preloadStorageLocations()) to
   * recognize a location token; before that preload resolves, every
   * token is treated as "task" (same fallback posture
   * validateLocation() already uses elsewhere) since there's nothing
   * yet to check a location against.
   */
  function classifySearchInput(value) {
    const tokens = String(value || "")
      .split(SEARCH_INPUT_SPLIT_RE)
      .map((t) => t.trim().toUpperCase())
      .filter(Boolean);
    if (!tokens.length) return "empty";
    if (!state.storageLocations) return "task";
    const isLocation = (t) => state.storageLocations.has(t);
    const hasLocation = tokens.some(isLocation);
    const hasOther = tokens.some((t) => !isLocation(t));
    if (hasLocation && hasOther) return "mixed";
    return hasLocation ? "cycle_count" : "task";
  }

  function updateLoadButton() {
    const value = el.taskIdInput.value.trim();
    const kind = classifySearchInput(value);
    state.searchMode = kind === "cycle_count" ? "cycle_count" : "task";
    el.loadTaskBtn.disabled = kind === "empty" || kind === "mixed";
    if (!state.token) {
      el.matchHint.textContent = "Authenticate to begin.";
    } else if (kind === "empty") {
      el.matchHint.textContent =
        "Scan or type a Task Id, iLPN, or Storage Location — separate multiple with ; , or a space.";
    } else if (kind === "mixed") {
      el.matchHint.textContent =
        "Enter either Task Ids/iLPNs or Storage Locations, not both — Load Task is disabled until this is one or the other.";
    } else if (kind === "cycle_count") {
      el.matchHint.textContent = "Press Enter or click Load Task to start an ad hoc Cycle Count.";
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
      preloadStorageLocations(); // fire-and-forget, see its own docstring
      preloadAdjustmentReasonCodes(); // fire-and-forget, see its own docstring
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

  /**
   * Single-group results keep the original header exactly as before
   * (Task/Container + Type + Status). Multi-group results (2026-08-08,
   * multi-LPN search) don't have one task to summarize, so this shows a
   * plain count instead — per-row identity lives in the Task/Container
   * column that renderGroups() shows once more than one group is
   * loaded (see the "multi-group" CSS class on #linesTable). Final
   * design for this is explicitly deferred; this is an interim default.
   */
  function renderTaskMeta() {
    const groups = state.groups;
    if (groups.length === 1) {
      const g = groups[0];
      if (g.mode === "no_task") {
        el.taskMeta.innerHTML = `
          <span><strong>Container</strong> ${escapeHtml(g.containerId || "")}</span>
          <span><strong>Type</strong> ${escapeHtml(g.taskTypeLabel || g.taskType)}</span>
          <span>${statusBadgeHtml(g.taskStatusLabel, g.taskStatus)}</span>
        `;
        el.transactionIdValue.textContent = "User Directed";
      } else {
        el.taskMeta.innerHTML = `
          <span><strong>Task</strong> ${escapeHtml(g.taskId)}</span>
          <span><strong>Type</strong> ${escapeHtml(g.taskTypeLabel || g.taskType)}</span>
          <span><strong>Status</strong> ${statusBadgeHtml(g.taskStatusLabel, g.taskStatus)}</span>
        `;
        el.transactionIdValue.textContent = TRANSACTION_ID;
      }
    } else {
      const taskCount = groups.filter((g) => g.mode === "task").length;
      const noTaskCount = groups.filter((g) => g.mode === "no_task").length;
      el.taskMeta.innerHTML = `
        <span><strong>${groups.length} LPNs loaded</strong></span>
        <span>${fmtCount(taskCount, "task")}, ${fmtCount(noTaskCount, "no-task container")}</span>
      `;
      el.transactionIdValue.textContent = TRANSACTION_ID;
    }
  }

  function groupCellHtml(line) {
    const isNoTask = line.groupMode === "no_task";
    const groupLabel = isNoTask
      ? "Container " + escapeHtml(line.groupContainerId)
      : "Task " + escapeHtml(line.groupTaskId);
    return `
          <td class="col-group">
            ${groupLabel}<br/>${statusBadgeHtml(line.groupTaskStatusLabel, line.groupTaskStatus)}
          </td>`;
  }

  /**
   * LPN status badge lives in the LPN column itself (2026-08-08, per
   * explicit instruction: always show it, but without adding a new
   * column) — the existing Task/Container column was the other
   * candidate, but it's hidden by default for the common single-group
   * case (see the "multi-group" CSS class), which would have silently
   * defeated "always."
   */
  function lpnCellHtml(line) {
    const badge = ilpnStatusBadgeHtml(line.ilpnStatusLabel);
    return `<td>${escapeHtml(line.lpnId)}${badge ? "<br/>" + badge : ""}</td>`;
  }

  /**
   * Disabled outright for a consumed line (2026-08-08, per explicit
   * instruction — "should not be able to have their location or qty
   * changed"): its inventory already moved to a location record, so
   * there's nothing left on the LPN for a destination to even mean.
   */
  function toLocationCellHtml(line) {
    const consumed = isConsumedLine(line);
    const title = consumed
      ? ' title="This LPN has been consumed — its location can no longer be updated."'
      : "";
    return `
          <td>
            <input
              type="text"
              class="form-control to-location-input"
              data-task-detail-id="${escapeAttr(line.taskDetailId)}"
              data-default-location="${escapeAttr(line.toLocationId)}"
              value="${escapeAttr(line.toLocationId)}"
              autocomplete="off"
              ${consumed ? "disabled" : ""}${title}
            />
          </td>`;
  }

  /**
   * One row for a normal (single-item) line, or a summary row plus one
   * expandable sub-row per item for a MIXED no-task container
   * (2026-08-08, line.mixedItems — see resolve_search()'s no_task
   * branch). The summary row carries the shared To Location (a
   * container only has one destination) and an aggregate read-only
   * Planned Qty; each item's own Completed Qty/reason code live on its
   * own sub-row, collapsed by default behind the "MIXED" toggle.
   */
  function renderLineRow(line) {
    if (line.mixedItems) {
      // Only worth showing at the summary level if every real item
      // agrees (2026-08-08, per explicit instruction) — a mix of units
      // has nothing coherent to show until expanded.
      const uomIds = line.mixedItems.map((item) => item.uomId || "");
      const commonUom = uomIds.every((u) => u === uomIds[0]) ? uomIds[0] : "";
      const summaryRow = `
        <tr class="line-row" data-task-detail-id="${escapeAttr(line.taskDetailId)}">
          <td>${escapeHtml(line.lineNumber)}</td>
          ${lpnCellHtml(line)}
          ${groupCellHtml(line)}
          <td>
            <button type="button" class="mixed-toggle" data-mixed-target="${escapeAttr(line.taskDetailId)}">
              <i class="fas fa-caret-right"></i> MIXED
            </button>
          </td>
          <td></td>
          <td>${escapeHtml(line.fromLocationId)}</td>
          ${toLocationCellHtml(line)}
          <td class="col-qty-wide">${escapeHtml(line.plannedQuantity)}</td>
          <td class="col-uom">${escapeHtml(commonUom)}</td>
          <td><span class="text-muted">see items below</span></td>
          <td class="col-reason"></td>
        </tr>`;
      const consumedForItems = isConsumedLine(line);
      const itemRows = line.mixedItems
        .map(
          (item) => `
        <tr class="mixed-item-row" data-mixed-parent="${escapeAttr(line.taskDetailId)}" style="display:none">
          <td></td>
          <td></td>
          <td class="col-group"></td>
          <td>${escapeHtml(item.itemId)}</td>
          <td>${escapeHtml(item.description)}</td>
          <td></td>
          <td></td>
          <td class="col-qty-wide">${escapeHtml(item.quantity)}</td>
          <td class="col-uom">${escapeHtml(item.uomId)}</td>
          <td>
            <input
              type="number"
              class="form-control mixed-qty-input"
              data-parent-task-detail-id="${escapeAttr(line.taskDetailId)}"
              data-item-id="${escapeAttr(item.itemId)}"
              data-default-qty="${escapeAttr(item.quantity)}"
              data-uom-factor="${escapeAttr(item.uomFactor)}"
              value="${escapeAttr(item.quantity)}"
              min="0"
              step="any"
              ${consumedForItems ? "disabled" : ""}
            />
          </td>
          <td class="col-reason">
            <select
              class="form-select reason-code-select invalid"
              data-parent-task-detail-id="${escapeAttr(line.taskDetailId)}"
              data-item-id="${escapeAttr(item.itemId)}"
            >
              ${reasonCodeOptionsHtml()}
            </select>
          </td>
        </tr>`
        )
        .join("");
      return summaryRow + itemRows;
    }

    const remaining = remainingQty(line);
    const consumed = isConsumedLine(line);
    return `
        <tr class="line-row" data-task-detail-id="${escapeAttr(line.taskDetailId)}">
          <td>${escapeHtml(line.lineNumber)}</td>
          ${lpnCellHtml(line)}
          ${groupCellHtml(line)}
          <td>${escapeHtml(line.itemId)}</td>
          <td>${escapeHtml(line.description)}</td>
          <td>${escapeHtml(line.fromLocationId)}</td>
          ${toLocationCellHtml(line)}
          <td class="col-qty-wide">${escapeHtml(line.plannedQuantity)}</td>
          <td class="col-uom">${escapeHtml(line.uomId)}</td>
          <td>
            <input
              type="number"
              class="form-control completed-qty-input"
              data-task-detail-id="${escapeAttr(line.taskDetailId)}"
              data-default-qty="${escapeAttr(remaining)}"
              data-uom-factor="${escapeAttr(line.uomFactor)}"
              value="${escapeAttr(remaining)}"
              min="0"
              step="any"
              ${consumed ? 'disabled title="This LPN has been consumed — its quantity can no longer be updated."' : ""}
            />
          </td>
          <td class="col-reason">
            <select class="form-select reason-code-select invalid" data-task-detail-id="${escapeAttr(line.taskDetailId)}">
              ${reasonCodeOptionsHtml()}
            </select>
          </td>
        </tr>`;
  }

  function renderGroups() {
    state.selectedTaskDetailId = null;
    el.cycleCountLinesTable.style.display = "none";
    el.linesTable.style.display = "";
    renderTaskMeta();
    const multiGroup = state.groups.length > 1;
    el.linesTable.classList.toggle("multi-group", multiGroup);
    const lines = allLines();
    el.linesBody.innerHTML = lines.map((line) => renderLineRow(line)).join("");
    el.linesBody
      .querySelectorAll(".to-location-input")
      .forEach((input) => validateLocation(input, true));
    el.linesBody.querySelectorAll(".completed-qty-input").forEach((input) => validateQty(input));
    updateLineActionButtons();
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
   * LPN status badge (2026-08-08) — its own color rule, distinct from
   * statusBadgeClass() above: Consumed is grey (not a problem, just
   * informational — per explicit instruction, not green like an
   * ordinary active status), Lost/Canceled stay red, everything else
   * (In Transit, Pre-Receipt/Partially/fully Allocated, Not Allocated)
   * is green.
   */
  function ilpnStatusBadgeClass(statusLabel) {
    const text = String(statusLabel || "").trim().toLowerCase();
    if (text === "consumed") return "status-chip status-grey";
    if (text === "lost" || text.startsWith("cancel")) return "status-chip status-red";
    return "status-chip status-green";
  }

  function ilpnStatusBadgeHtml(statusLabel) {
    if (!statusLabel) return "";
    return `<span class="badge ${ilpnStatusBadgeClass(statusLabel)}">${escapeHtml(statusLabel)}</span>`;
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

  function getLineByTaskDetailId(taskDetailId) {
    return allLines().find((l) => String(l.taskDetailId) === String(taskDetailId)) || null;
  }

  /**
   * Mirrors getLocationOverride() exactly, same "only send if changed"
   * contract — an untouched box sends nothing, so the backend does a
   * plain full completion, no adjustment call at all. An edited value
   * now (2026-08-08) means "correct the LPN's actual quantity first,
   * via Modify iLPN, then complete" — see
   * task_service.adjust_ilpn_quantities()/complete_putaway_line()'s
   * docstrings. Deliberately not "always send the box's value" — that
   * would trigger a pointless adjustment call (itself a real inventory
   * transaction) even when nothing actually changed.
   */
  function getCompletedQtyOverride(taskDetailId) {
    const input = el.linesBody.querySelector(
      '.completed-qty-input[data-task-detail-id="' + CSS.escape(String(taskDetailId)) + '"]'
    );
    if (!input) return undefined;
    const value = Number(input.value);
    const original = Number(input.dataset.defaultQty || 0);
    if (!Number.isFinite(value) || value === original) return undefined;
    return value;
  }

  /**
   * Builds the `itemAdjustments` array a completion call sends (2026-08-08)
   * — empty if nothing changed. For a MIXED container, sends every
   * item's *current* box value unconditionally (not just changed ones)
   * — task_service.adjust_ilpn_quantities() already compares against
   * live on-hand and silently skips true no-ops, so there's no need to
   * detect "changed" client-side for each item individually. For a
   * normal single-item line, reuses getCompletedQtyOverride()'s
   * "only if changed" value.
   */
  /**
   * `desiredQty` sent to the backend is always base units (2026-08-08
   * — see task_service._package_conversion_factor()'s docstring): every
   * quantity shown/edited in the UI is in the item's *display* pack
   * unit (e.g. "10 Units" for 240 base units at a 24x factor), so the
   * box's raw value is multiplied back up by its own
   * `data-uom-factor` here before it's ever sent anywhere — MAWM
   * itself and adjust_ilpn_quantities() only deal in base units.
   */
  function collectItemAdjustments(line) {
    if (line.mixedItems) {
      return line.mixedItems.map((item) => {
        const input = el.linesBody.querySelector(
          '.mixed-qty-input[data-parent-task-detail-id="' +
            CSS.escape(line.taskDetailId) +
            '"][data-item-id="' +
            CSS.escape(item.itemId) +
            '"]'
        );
        const select = el.linesBody.querySelector(
          '.reason-code-select[data-parent-task-detail-id="' +
            CSS.escape(line.taskDetailId) +
            '"][data-item-id="' +
            CSS.escape(item.itemId) +
            '"]'
        );
        const factor = input ? Number(input.dataset.uomFactor) || 1 : item.uomFactor || 1;
        const displayValue = input ? Number(input.value) : item.quantity;
        const baseValue = Number.isFinite(displayValue) ? displayValue * factor : item.quantity * factor;
        return {
          itemId: item.itemId,
          desiredQty: baseValue,
          reasonCode: select ? select.value : "",
        };
      });
    }
    const override = getCompletedQtyOverride(line.taskDetailId);
    if (override === undefined) return [];
    const input = el.linesBody.querySelector(
      '.completed-qty-input[data-task-detail-id="' + CSS.escape(line.taskDetailId) + '"]'
    );
    const factor = (input && Number(input.dataset.uomFactor)) || 1;
    const reasonSelect = el.linesBody.querySelector(
      '.reason-code-select[data-task-detail-id="' + CSS.escape(line.taskDetailId) + '"]'
    );
    return [
      {
        itemId: line.itemId,
        desiredQty: override * factor,
        reasonCode: reasonSelect ? reasonSelect.value : "",
      },
    ];
  }

  function isLocationValid(taskDetailId) {
    const input = el.linesBody.querySelector(
      '.to-location-input[data-task-detail-id="' + CSS.escape(String(taskDetailId)) + '"]'
    );
    return !!input && input.dataset.locationValid === "true";
  }

  /**
   * Completed Qty just needs to be a non-negative number — no upper
   * bound anymore (2026-08-08). Before the Modify iLPN adjustment
   * mechanism, this box could only ever express a *smaller* quantity on
   * the same move, so it was capped at "remaining." Now an edited value
   * triggers an inventory adjustment before completion (see
   * task_service.adjust_ilpn_quantities()), which can correct the
   * quantity in either direction — found MORE units than expected is
   * just as valid a correction as found fewer.
   */
  function validateQty(input) {
    const value = Number(input.value);
    const valid = Number.isFinite(value) && value >= 0;
    input.dataset.qtyValid = valid ? "true" : "false";
    input.classList.toggle("invalid", !valid);
    updateLineActionButtons();
  }

  /**
   * Takes the line object, not just its taskDetailId (2026-08-08) — a
   * MIXED row's summary line has no single `.completed-qty-input` of
   * its own (its quantity is an aggregate, edited per-item in the
   * expandable sub-rows instead), so it short-circuits valid here;
   * gating for a MIXED completion happens server-side when the
   * adjustment + putaway sequence actually runs.
   */
  function isQtyValid(line) {
    if (line.mixedItems) return true;
    const input = el.linesBody.querySelector(
      '.completed-qty-input[data-task-detail-id="' + CSS.escape(String(line.taskDetailId)) + '"]'
    );
    return !!input && input.dataset.qtyValid === "true";
  }

  /**
   * A reason code is only required once its Completed Qty box is
   * actually overridden (see `.overridden` — the select is invisible
   * otherwise) — but once shown, it must be a real selection, not the
   * "Select Reason" placeholder (2026-08-08, per explicit instruction).
   * Checks every mixed sub-item independently, since each one has its
   * own qty box and reason select.
   */
  function isReasonValid(line) {
    if (line.mixedItems) {
      return line.mixedItems.every((item) => {
        const qtyInput = el.linesBody.querySelector(
          '.mixed-qty-input[data-parent-task-detail-id="' +
            CSS.escape(line.taskDetailId) +
            '"][data-item-id="' +
            CSS.escape(item.itemId) +
            '"]'
        );
        if (!qtyInput || !qtyInput.classList.contains("overridden")) return true;
        const reasonSelect = el.linesBody.querySelector(
          '.reason-code-select[data-parent-task-detail-id="' +
            CSS.escape(line.taskDetailId) +
            '"][data-item-id="' +
            CSS.escape(item.itemId) +
            '"]'
        );
        return !!(reasonSelect && reasonSelect.value);
      });
    }
    const qtyInput = el.linesBody.querySelector(
      '.completed-qty-input[data-task-detail-id="' + CSS.escape(line.taskDetailId) + '"]'
    );
    if (!qtyInput || !qtyInput.classList.contains("overridden")) return true;
    const reasonSelect = el.linesBody.querySelector(
      '.reason-code-select[data-task-detail-id="' + CSS.escape(line.taskDetailId) + '"]'
    );
    return !!(reasonSelect && reasonSelect.value);
  }

  /**
   * Loads every active Storage location for the current facility once
   * per session (2026-08-08, replacing a debounced live API call per
   * keystroke, per explicit instruction) into state.storageLocations —
   * a plain uppercased Set of both LocationId and DisplayLocation
   * values, so validateLocation() can check it synchronously. Called
   * fire-and-forget right after auth; validateLocation() falls back to
   * the old live per-keystroke call if this hasn't resolved yet (or
   * failed) when the user starts typing.
   */
  async function preloadStorageLocations() {
    state.storageLocations = null;
    try {
      const data = await api("preload_putaway_locations", {
        org: state.org,
        token: state.token,
        location: state.facility,
      });
      if (!data.success) return;
      const set = new Set();
      (data.entries || []).forEach((e) => {
        if (e.locationId) set.add(String(e.locationId).toUpperCase());
        if (e.displayLocation) set.add(String(e.displayLocation).toUpperCase());
      });
      state.storageLocations = set;
      revalidateAllLocations();
    } catch (e) {
      // Leave state.storageLocations null — validateLocation() falls
      // back to the live per-keystroke check.
    }
  }

  function revalidateAllLocations() {
    el.linesBody
      .querySelectorAll(".to-location-input")
      .forEach((input) => validateLocation(input, true));
  }

  /**
   * A To Location must always resolve to a real, active Storage location
   * before any of the 3 completion buttons are usable — per explicit
   * instruction, this applies to every Putaway line, not just the
   * no-open-task case. `dataset.locationValid` starts unset (falsy) on
   * every row until validateLocation() resolves, so buttons stay
   * disabled until that first check completes, not just on override.
   */
  const locationValidateTimers = new WeakMap();

  function validateLocation(input, immediate) {
    clearTimeout(locationValidateTimers.get(input));

    // A disabled input (2026-08-08 — a consumed line's To Location,
    // see isConsumedLine()) has nothing actionable about it, so it
    // shouldn't show a red "needs fixing" state — the disabled/greyed
    // styling alone already communicates "nothing to do here."
    if (input.disabled) {
      input.classList.remove("invalid");
      return;
    }

    // Fast path: state.storageLocations is a preloaded Set, so this is
    // a synchronous, in-memory check — runs on every keystroke with no
    // artificial delay, no network round trip.
    if (state.storageLocations) {
      const value = input.value.trim();
      const valid = !!value && state.storageLocations.has(value.toUpperCase());
      input.dataset.locationValid = valid ? "true" : "false";
      input.classList.toggle("invalid", !valid);
      updateLineActionButtons();
      return;
    }

    // Fallback: the preload hasn't resolved yet (or failed) — same
    // debounced live lookup this app used before 2026-08-08.
    const run = async () => {
      const value = input.value.trim();
      if (!value) {
        input.dataset.locationValid = "false";
        input.classList.add("invalid");
        updateLineActionButtons();
        return;
      }
      try {
        const data = await api("validate_putaway_location", {
          org: state.org,
          token: state.token,
          location: state.facility,
          locationText: value,
        });
        const valid = !!(data.success && data.valid);
        input.dataset.locationValid = valid ? "true" : "false";
        input.classList.toggle("invalid", !valid);
      } catch (e) {
        input.dataset.locationValid = "false";
        input.classList.add("invalid");
      }
      updateLineActionButtons();
    };
    if (immediate) {
      run();
      return;
    }
    locationValidateTimers.set(input, setTimeout(run, 400));
  }

  /**
   * Spans every group currently on screen, not just one task
   * (2026-08-08, multi-LPN search + "Complete All" scope, per explicit
   * instruction: Complete All acts on everything visible regardless of
   * which task/container each line belongs to).
   */
  function allOutstandingLinesValid() {
    const outstanding = allLines().filter((l) => remainingQty(l) > 0);
    if (!outstanding.length) return true; // let the click through to show "nothing to do"
    return outstanding.every(
      (l) =>
        !isConsumedLine(l) &&
        isLocationValid(l.taskDetailId) &&
        isQtyValid(l) &&
        isReasonValid(l)
    );
  }

  function updateLineActionButtons() {
    const selectedLine = getSelectedLine();
    const hasSelection = !!selectedLine;
    const selectedValid =
      hasSelection &&
      !isConsumedLine(selectedLine) &&
      isLocationValid(selectedLine.taskDetailId) &&
      isQtyValid(selectedLine) &&
      isReasonValid(selectedLine);
    el.fullLineBtn.disabled = !hasSelection || !selectedValid;
    el.allLinesBtn.disabled = !allOutstandingLinesValid();
  }

  function selectLine(taskDetailId) {
    state.selectedTaskDetailId = taskDetailId;
    el.linesBody.querySelectorAll("tr.line-row").forEach((row) => {
      row.classList.toggle("selected", row.dataset.taskDetailId === String(taskDetailId));
    });
    updateLineActionButtons();
  }

  // ---------------------------------------------------------------------
  // Ad hoc Cycle Count (2026-08-08, ninth session) — its own table
  // (#cycleCountLinesTable, per explicit instruction) and its own
  // parallel set of render/select/validate/complete functions rather
  // than branching the putaway ones above, since almost nothing about a
  // cycle count line resembles a putaway line: no location override, no
  // reason code, no remaining-quantity concept, and no confirm-warning
  // modal (a quantity-mismatch warning is auto-overridden server-side —
  // see task_service.complete_cycle_count_line()). Reuses
  // state.groups/allLines()/state.selectedTaskDetailId/
  // getLineByTaskDetailId() as-is — a taskDetailId is unique regardless
  // of mode, and the two modes are never loaded at the same time (see
  // classifySearchInput()).
  // ---------------------------------------------------------------------

  /**
   * Matches the two-arrow "cycle count pending" icon shown in the real
   * WM UI next to a locked location (2026-08-08, per explicit
   * instruction, screenshot provided) — no exact source asset was
   * found on disk, so this uses Font Awesome's closest equivalent
   * (already loaded in this app) rather than inventing/guessing at a
   * custom icon. Always rendered (not conditionally), display toggled
   * via setCycleCountLockIcon() so a later poll/completion result can
   * show or hide it live without re-rendering the row — "before or
   * after count," per explicit instruction: the initial search already
   * carries the location's current lock state (see
   * task_service.resolve_cycle_count_location()), so this can be
   * showing before any count is even started.
   */
  function cycleCountLockIconHtml(taskDetailId, locked) {
    return (
      ' <i class="fas fa-arrows-rotate cc-lock-icon" data-task-detail-id="' +
      escapeAttr(taskDetailId) +
      '" title="Cycle count pending — location locked" style="display:' +
      (locked ? "inline-block" : "none") +
      '"></i>'
    );
  }

  function cycleCountLineRowHtml(line, isSubRow) {
    const rowClass = isSubRow ? "mixed-item-row" : "cc-line-row line-row";
    const extraAttrs = isSubRow
      ? ` data-mixed-parent="${escapeAttr(line.groupKey)}" style="display:none"`
      : "";
    return `
        <tr class="${rowClass}" data-task-detail-id="${escapeAttr(line.taskDetailId)}" data-group-key="${escapeAttr(line.groupKey)}"${extraAttrs}>
          <td>${isSubRow ? "" : escapeHtml(line.lineNumber)}</td>
          <td>${isSubRow ? "" : escapeHtml(line.locationId) + cycleCountLockIconHtml(line.taskDetailId, line.locationLocked)}</td>
          <td>
            <input
              type="text"
              class="form-control cc-item-input"
              data-task-detail-id="${escapeAttr(line.taskDetailId)}"
              value="${escapeAttr(line.itemId)}"
              autocomplete="off"
            />
          </td>
          <td><div class="col-desc-narrow" title="${escapeAttr(line.description)}">${escapeHtml(line.description)}</div></td>
          <td class="col-qty-wide">
            <input
              type="number"
              class="form-control cc-qty-input invalid"
              data-task-detail-id="${escapeAttr(line.taskDetailId)}"
              value=""
              step="any"
            />
          </td>
          <td class="col-cc-result cc-result" data-task-detail-id="${escapeAttr(line.taskDetailId)}"></td>
        </tr>`;
  }

  /**
   * A location with more than one genuinely distinct item (see
   * task_service.resolve_cycle_count_location()'s ItemId-dedup
   * docstring — this is only reachable for real different items, never
   * duplicate records for the same one) renders as the same MIXED
   * accordion pattern already used for multi-item no-task containers,
   * per explicit instruction.
   *
   * **Sub-rows are not independently completable** (2026-08-08,
   * confirmed live and per explicit instruction) — MAWM only finalizes
   * a location's count once every item present has been submitted
   * under the same CountRunId; completing just one item leaves it
   * parked at "Count Initiated" forever, and calling endCount before
   * every item is addressed is explicitly rejected (`INM::230`, "Not
   * all the Items in the Location are counted" — a real WARNING MAWM
   * returns, not a silent "missing item = 0" that would risk a false
   * tolerance failure). So selection/completion happens at the
   * **group** level everywhere in this file (selectCycleCountGroup(),
   * getSelectedCycleCountGroup(), etc) — a single-item location is
   * just a "group of 1," no special-casing needed. Every row (summary
   * and sub-rows alike) carries `data-group-key` for this.
   */
  function cycleCountGroupRowsHtml(group) {
    const lines = group.lines || [];
    if (lines.length <= 1) {
      return lines.map((line) => cycleCountLineRowHtml(line, false)).join("");
    }
    const groupKey = group.groupKey;
    const summaryRow = `
        <tr class="cc-line-row" data-task-detail-id="${escapeAttr(groupKey)}" data-group-key="${escapeAttr(groupKey)}">
          <td>${escapeHtml(lines[0].lineNumber)}</td>
          <td>${escapeHtml(group.locationId) + cycleCountLockIconHtml(groupKey, group.locationLocked)}</td>
          <td colspan="4">
            <button type="button" class="mixed-toggle" data-mixed-target="${escapeAttr(groupKey)}">
              <i class="fas fa-caret-right"></i> MIXED (${lines.length} items)
            </button>
          </td>
        </tr>`;
    const itemRows = lines.map((line) => cycleCountLineRowHtml(line, true)).join("");
    return summaryRow + itemRows;
  }

  function renderCycleCountTaskMeta() {
    const groups = state.groups;
    if (groups.length === 1) {
      el.taskMeta.innerHTML = `<span><strong>Location</strong> ${escapeHtml(groups[0].locationId)}</span>`;
    } else {
      el.taskMeta.innerHTML = `<span><strong>${groups.length} locations loaded</strong></span>`;
    }
    el.transactionIdValue.textContent = "Cycle Count Active-API";
  }

  function renderCycleCountGroups() {
    state.selectedCycleCountGroupKey = null;
    el.linesTable.style.display = "none";
    el.cycleCountLinesTable.style.display = "";
    renderCycleCountTaskMeta();
    el.cycleCountLinesBody.innerHTML = state.groups.map((g) => cycleCountGroupRowsHtml(g)).join("");
    updateCycleCountLineActionButtons();
  }

  function getCycleCountQtyInput(taskDetailId) {
    return el.cycleCountLinesBody.querySelector(
      '.cc-qty-input[data-task-detail-id="' + CSS.escape(String(taskDetailId)) + '"]'
    );
  }

  function getCycleCountItemInput(taskDetailId) {
    return el.cycleCountLinesBody.querySelector(
      '.cc-item-input[data-task-detail-id="' + CSS.escape(String(taskDetailId)) + '"]'
    );
  }

  /**
   * Qty must be an explicitly entered number — including 0 — not just
   * "non-blank" (2026-08-08, per explicit instruction: "the qty should
   * be empty and be forced to enter a number, even if 0"). A blank
   * input's `.value` is the empty string, which Number() coerces to 0,
   * so the raw string is checked first — otherwise an untouched box
   * would silently pass as a real "0" entry.
   */
  function isCycleCountQtyValid(taskDetailId) {
    const input = getCycleCountQtyInput(taskDetailId);
    if (!input) return false;
    const raw = input.value.trim();
    if (raw === "") return false;
    return Number.isFinite(Number(raw));
  }

  function isCycleCountItemValid(taskDetailId) {
    const input = getCycleCountItemInput(taskDetailId);
    return !!input && !!input.value.trim();
  }

  function validateCycleCountQty(input) {
    const raw = input.value.trim();
    const valid = raw !== "" && Number.isFinite(Number(raw));
    input.classList.toggle("invalid", !valid);
    updateCycleCountLineActionButtons();
  }

  function isCycleCountLineDone(line) {
    const resultCell = el.cycleCountLinesBody.querySelector(
      '.cc-result[data-task-detail-id="' + CSS.escape(String(line.taskDetailId)) + '"]'
    );
    return !!resultCell && resultCell.dataset.done === "true";
  }

  /**
   * Group-level gating (2026-08-08, per explicit instruction and live
   * confirmation — see cycleCountGroupRowsHtml()'s docstring for why
   * sub-rows can't complete independently) — "done"/"valid" for a
   * location means every one of its items is done/valid, not just one.
   * A single-item location is just a group of 1, so this applies
   * uniformly without checking line count anywhere.
   */
  function isCycleCountGroupDone(group) {
    return (group.lines || []).every((l) => isCycleCountLineDone(l));
  }

  function isCycleCountGroupValid(group) {
    return (group.lines || []).every(
      (l) => isCycleCountItemValid(l.taskDetailId) && isCycleCountQtyValid(l.taskDetailId)
    );
  }

  function allOutstandingCycleCountGroups() {
    return state.groups.filter((g) => !isCycleCountGroupDone(g));
  }

  function allOutstandingCycleCountGroupsValid() {
    const outstanding = allOutstandingCycleCountGroups();
    if (!outstanding.length) return true; // let the click through to show "nothing to do"
    return outstanding.every((g) => isCycleCountGroupValid(g));
  }

  function getSelectedCycleCountGroup() {
    if (!state.selectedCycleCountGroupKey) return null;
    return state.groups.find((g) => g.groupKey === state.selectedCycleCountGroupKey) || null;
  }

  function updateCycleCountLineActionButtons() {
    const selectedGroup = getSelectedCycleCountGroup();
    const hasSelection = !!selectedGroup;
    const selectedValid = hasSelection && !isCycleCountGroupDone(selectedGroup) && isCycleCountGroupValid(selectedGroup);
    el.fullLineBtn.disabled = !hasSelection || !selectedValid;
    el.allLinesBtn.disabled = !allOutstandingCycleCountGroupsValid();
  }

  function selectCycleCountGroup(groupKey) {
    state.selectedCycleCountGroupKey = groupKey;
    el.cycleCountLinesBody.querySelectorAll("tr[data-group-key]").forEach((row) => {
      row.classList.toggle("selected", row.dataset.groupKey === String(groupKey));
    });
    updateCycleCountLineActionButtons();
  }

  /**
   * `message` may contain HTML (the strikethrough on a not-yet-applied
   * counted qty — see cycleCountResultText()), so this uses innerHTML,
   * not textContent — cycleCountResultText() is responsible for
   * escaping any free-text portion (MAWM's own error/failure-reason
   * strings) before it gets here.
   */
  function setCycleCountResultCell(taskDetailId, message, kind, done) {
    const cell = el.cycleCountLinesBody.querySelector(
      '.cc-result[data-task-detail-id="' + CSS.escape(String(taskDetailId)) + '"]'
    );
    if (!cell) return;
    cell.innerHTML = message;
    cell.className = "col-reason cc-result" + (kind ? " " + kind : "");
    cell.dataset.done = done ? "true" : "false";
    if (done) {
      const qtyInput = getCycleCountQtyInput(taskDetailId);
      const itemInput = getCycleCountItemInput(taskDetailId);
      if (qtyInput) qtyInput.disabled = true;
      if (itemInput) itemInput.disabled = true;
    }
  }

  function setCycleCountLockIcon(taskDetailId, locked) {
    const icon = el.cycleCountLinesBody.querySelector(
      '.cc-lock-icon[data-task-detail-id="' + CSS.escape(String(taskDetailId)) + '"]'
    );
    if (icon) icon.style.display = locked ? "inline-block" : "none";
  }

  /**
   * "($50)" — the dollar variance in smaller grey text to the right of
   * the quantity variance (2026-08-08, per explicit instruction).
   * Always parenthesized regardless of sign (2026-08-08, revised per
   * explicit instruction — not just accounting-style parens-for-
   * negative anymore), rounded to whole dollars.
   */
  function formatVarianceValueHtml(varianceValue) {
    if (varianceValue == null) return "";
    const rounded = Math.round(Math.abs(Number(varianceValue)));
    return ' <span class="cc-variance-value">($' + rounded + ")</span>";
  }

  /** Applies one item's result (status cell only — no lock icon, see
   * applyCycleCountLocationResultToGroup() for that, since the lock is
   * a location-level property carried at the outer response level, not
   * per-item).
   */
  function applyCycleCountResultToRow(taskDetailId, result) {
    setCycleCountResultCell(taskDetailId, cycleCountResultText(result), cycleCountResultKind(result), result.success);
  }

  /**
   * Applies a complete_cycle_count_location()/
   * check_cycle_count_location_status() response — `{results: [...],
   * locationLocked|locationLockedAfter}` — to every line in the group
   * (2026-08-08, per explicit instruction: a location's items complete
   * atomically together, not independently — see
   * cycleCountGroupRowsHtml()'s docstring). The lock icon can be keyed
   * either by the group's own key (MIXED summary row) or a line's own
   * taskDetailId (single-item "group of 1") depending on which row
   * actually rendered it — setCycleCountLockIcon() no-ops harmlessly
   * for whichever key doesn't match a real icon, so both are always
   * attempted rather than branching on group size here.
   */
  function applyCycleCountLocationResultToGroup(group, response) {
    (response.results || []).forEach((result) => {
      const line = (group.lines || []).find((l) => l.itemId === result.itemId);
      if (line) applyCycleCountResultToRow(line.taskDetailId, result);
    });
    const locked = response.locationLocked !== undefined ? response.locationLocked : response.locationLockedAfter;
    if (locked !== undefined) {
      setCycleCountLockIcon(group.groupKey, locked);
      (group.lines || []).forEach((l) => setCycleCountLockIcon(l.taskDetailId, locked));
    }
  }

  const CYCLE_COUNT_POLL_INTERVAL_MS = 2000;
  const CYCLE_COUNT_POLL_MAX_ATTEMPTS = 30;

  /**
   * Booking is asynchronous — a count that will eventually book (within
   * tolerance) can sit at "Count Initiated"/"Pending Booking" for a few
   * seconds first (2026-08-08, per explicit instruction: show that
   * in-flight state immediately, then poll a few times to pick up
   * "Booked" once it lands, instead of the original request blocking).
   * Also covers a count that's genuinely stuck (out of tolerance) —
   * there's no way to tell those two cases apart from the status text
   * alone, so this always polls up to CYCLE_COUNT_POLL_MAX_ATTEMPTS
   * times regardless of which status came back, then just stops,
   * leaving whatever the last poll showed. Fire-and-forget: the caller
   * doesn't await this, so Complete All isn't blocked waiting for each
   * line's booking to resolve before moving to the next line.
   *
   * **Widened 2026-08-08 after a real near-miss** — the original 8
   * attempts * 1.8s (~14.4s) window was measured live to be too short:
   * running Complete All across 3 locations, one within-tolerance
   * count's real booking took ~14.7s (confirmed via
   * inventoryCountRun's Created/UpdatedTimestamp) — just past the old
   * cutoff, so polling gave up moments before the resolution landed,
   * leaving the row stuck showing "Count not booked" even though it
   * had, in fact, booked. Now 30 * 2s = 60s, comfortably clear of that
   * measurement; check_cycle_count_status() is a cheap read, so a
   * longer window costs little even for the out-of-tolerance case that
   * never resolves and just polls uselessly until it gives up.
   */
  async function pollCycleCountGroupStatus(group, initialResponse) {
    if (initialResponse.success || !initialResponse.countRunId) return;
    const itemIds = (group.lines || []).map((l) => {
      const itemInput = getCycleCountItemInput(l.taskDetailId);
      return itemInput ? itemInput.value.trim() : l.itemId;
    });
    for (let attempt = 0; attempt < CYCLE_COUNT_POLL_MAX_ATTEMPTS; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, CYCLE_COUNT_POLL_INTERVAL_MS));
      if (isCycleCountGroupDone(group)) return; // already resolved (or resubmitted) by something else
      let response;
      try {
        response = await api("check_cycle_count_location_status", {
          org: state.org,
          token: state.token,
          location: state.facility,
          locationId: group.locationId,
          itemIds,
          countRunId: initialResponse.countRunId,
        });
      } catch (e) {
        return; // quietly stop polling -- the rows still show the last known state
      }
      applyCycleCountLocationResultToGroup(group, response);
      if (response.success) {
        setActionStatus("Location " + group.locationId + " booked.", "success");
        updateCycleCountLineActionButtons();
        return;
      }
    }
  }

  /**
   * Submits every item in the group together, atomically (2026-08-08,
   * per explicit instruction and live confirmation — see
   * cycleCountGroupRowsHtml()'s docstring). A single-item location is
   * just a group of 1, so this is the only completion path for cycle
   * count now — there is no more per-line completion call.
   */
  async function completeCycleCountGroupAction(group) {
    const itemAdjustments = (group.lines || []).map((line) => {
      const itemInput = getCycleCountItemInput(line.taskDetailId);
      const qtyInput = getCycleCountQtyInput(line.taskDetailId);
      return {
        itemId: itemInput ? itemInput.value.trim() : line.itemId,
        quantity: qtyInput ? Number(qtyInput.value) : null,
      };
    });
    return api("complete_cycle_count_location", {
      org: state.org,
      token: state.token,
      location: state.facility,
      locationId: group.locationId,
      itemAdjustments,
    });
  }

  /**
   * The real outcome (2026-08-08, revised after live investigation —
   * see task_service.complete_cycle_count_line()'s docstring) is one of
   * three cases, not just success/failure: booked (applied), pending
   * supervisor booking (out of tolerance — the location is locked, not
   * an error exactly, but not applied either), or a genuine failure. A
   * "Pending Booking" result leaves the row retryable (not marked
   * done) since it's plausible the count itself should be re-entered,
   * not just resubmitted as-is.
   *
   * **Three stacked lines, 2026-08-08, per explicit instruction** —
   * status / before→after / variance, replacing the old single-line
   * message plus two separate Previous Qty/Variance columns (folded
   * into this wider column instead). The not-yet-applied counted qty
   * is struck through for any non-`success` status that still carries
   * real qty data (in-flight statuses like "Count Initiated" included,
   * not just "Pending Booking" specifically — none of them have
   * actually applied yet). Returns HTML (see setCycleCountResultCell()'s
   * innerHTML usage) — free-text portions (MAWM's own error/failure-
   * reason strings) are escaped, but the numeric qty/variance fields
   * aren't (never user-typed free text, always straight from the
   * count-result API response).
   */
  function cycleCountResultText(result) {
    if (!result.status) {
      return '<div class="cc-result-line">' + escapeHtml(result.bookingFailureReason || result.error || "Failed") + "</div>";
    }
    const lines = ['<div class="cc-result-line cc-result-status">' + escapeHtml(result.status) + "</div>"];
    if (result.previousQty != null && result.countedQty != null) {
      const countedHtml = result.success
        ? escapeHtml(result.countedQty)
        : "<s>" + escapeHtml(result.countedQty) + "</s>";
      lines.push('<div class="cc-result-line">' + escapeHtml(result.previousQty) + " → " + countedHtml + "</div>");
    }
    if (result.varianceQty != null) {
      lines.push(
        '<div class="cc-result-line cc-result-variance">' +
          escapeHtml(result.varianceQty) +
          formatVarianceValueHtml(result.varianceValue) +
          "</div>"
      );
    }
    return lines.join("");
  }

  function cycleCountResultKind(result) {
    if (result.success) return "success";
    if (result.status === "Pending Booking") return "pending";
    return "error";
  }

  async function completeCycleCountLine() {
    const group = getSelectedCycleCountGroup();
    if (!group) return;
    if (isCycleCountGroupDone(group)) return;
    setBusy(true, "Completing " + group.locationId + "…");
    try {
      const response = await completeCycleCountGroupAction(group);
      applyCycleCountLocationResultToGroup(group, response);
      if (!response.success) {
        setActionStatus(response.error || "Complete failed", "error");
        pollCycleCountGroupStatus(group, response); // fire-and-forget
        return;
      }
      setActionStatus("Completed " + group.locationId + ".", "success");
      updateCycleCountLineActionButtons();
    } catch (e) {
      (group.lines || []).forEach((l) => setCycleCountResultCell(l.taskDetailId, escapeHtml(e.message || String(e)), "error", false));
      setActionStatus(e.message || String(e), "error");
    } finally {
      setBusy(false);
    }
  }

  let allCycleCountGroupsPending = [];

  function openAllCycleCountLinesModal() {
    allCycleCountGroupsPending = allOutstandingCycleCountGroups();
    if (!allCycleCountGroupsPending.length) {
      setActionStatus("No outstanding lines to complete.", "");
      return;
    }
    if (!allOutstandingCycleCountGroupsValid()) {
      setActionStatus("Enter an Item and Qty for every line before completing all.", "error");
      return;
    }
    el.allLinesList.innerHTML = allCycleCountGroupsPending
      .map((g) => {
        const itemSummaries = (g.lines || [])
          .map((l) => {
            const itemInput = getCycleCountItemInput(l.taskDetailId);
            const qtyInput = getCycleCountQtyInput(l.taskDetailId);
            const itemId = itemInput ? itemInput.value.trim() : l.itemId;
            const qty = qtyInput ? qtyInput.value : "";
            return escapeHtml(itemId) + ": " + escapeHtml(qty);
          })
          .join(", ");
        return "<li>Location " + escapeHtml(g.locationId) + " — " + itemSummaries + "</li>";
      })
      .join("");
    allLinesModal.show();
  }

  function cycleCountFailureLabel(group, message) {
    return "Location " + group.locationId + ": " + message;
  }

  async function confirmAllCycleCountLines() {
    allLinesModal.hide();
    const total = allCycleCountGroupsPending.length;
    let succeeded = 0;
    const failures = [];
    for (let i = 0; i < total; i++) {
      const group = allCycleCountGroupsPending[i];
      setBusy(true, "Completing " + (i + 1) + " of " + total + "…");
      try {
        const response = await completeCycleCountGroupAction(group);
        applyCycleCountLocationResultToGroup(group, response);
        if (response.success) {
          succeeded++;
        } else {
          failures.push(cycleCountFailureLabel(group, response.error || "failed"));
          pollCycleCountGroupStatus(group, response); // fire-and-forget, doesn't block the rest of the loop
        }
      } catch (e) {
        failures.push(cycleCountFailureLabel(group, e.message || String(e)));
        (group.lines || []).forEach((l) => setCycleCountResultCell(l.taskDetailId, escapeHtml(e.message || String(e)), "error", false));
      }
    }
    setBusy(false);
    updateCycleCountLineActionButtons();
    if (!failures.length) {
      setActionStatus("Completed " + fmtCount(succeeded, "location", "locations") + ".", "success");
    } else {
      // Not a final tally (2026-08-08) — booking is asynchronous, so a
      // location counted here as an "issue" may still book moments
      // later via pollCycleCountGroupStatus()'s background poll
      // updating its own rows; this banner just reflects what was
      // known the instant the loop finished, not necessarily what's
      // true now.
      setActionStatus(
        "Booked " + succeeded + " of " + total + " locations immediately. " +
          fmtCount(failures.length, "location", "locations") +
          " still processing or needs attention (status will keep updating): " +
          failures.join("; "),
        ""
      );
    }
  }

  function showResults() {
    el.filtersScreen.classList.remove("active");
    el.resultsScreen.classList.add("active");
  }

  /**
   * Keeps the search box's value on "Scan Another" (2026-08-08, per
   * explicit instruction — especially useful for repeat testing) —
   * previously cleared it. Selects the text so typing/scanning
   * immediately replaces it, same as a fresh field would feel, without
   * losing the last value if the user just wants to reload it.
   */
  function showFilters() {
    el.resultsScreen.classList.remove("active");
    el.filtersScreen.classList.add("active");
    updateLoadButton();
    el.taskIdInput.focus();
    el.taskIdInput.select();
  }

  async function fetchAndRenderTask(searchValue) {
    const data = await api("load_task", {
      org: state.org,
      token: state.token,
      location: state.facility,
      taskId: searchValue,
    });
    if (!data.success) {
      setStatus(data.error || "Load failed", "error");
      return false;
    }
    state.groups = data.groups || [];
    state.lastSearchValue = searchValue;
    state.lastSearchMode = "task";
    renderGroups();
    let statusText = fmtCount(allLines().length, "line");
    if (data.notFound && data.notFound.length) {
      statusText += " — not found: " + data.notFound.join(", ");
    }
    el.resultsStatus.textContent = statusText;
    return true;
  }

  /** Cycle-count counterpart to fetchAndRenderTask() — same shape, different endpoint/table. */
  async function fetchAndRenderCycleCount(searchValue) {
    const data = await api("search_cycle_count", {
      org: state.org,
      token: state.token,
      location: state.facility,
      locations: searchValue,
    });
    if (!data.success) {
      setStatus(data.error || "Load failed", "error");
      return false;
    }
    state.groups = data.groups || [];
    state.lastSearchValue = searchValue;
    state.lastSearchMode = "cycle_count";
    renderCycleCountGroups();
    let statusText = fmtCount(allLines().length, "line");
    if (data.notFound && data.notFound.length) {
      statusText += " — not found: " + data.notFound.join(", ");
    }
    el.resultsStatus.textContent = statusText;
    return true;
  }

  async function loadTask() {
    const searchValue = el.taskIdInput.value.trim();
    if (!searchValue) return;
    setBusy(true, "Loading…");
    // Only a genuinely new search clears the action status now (bug fix,
    // 2026-08-08: this used to live in renderGroups()/renderLines(), so
    // it also fired — and wiped the just-shown "Completed X" message —
    // every time a completion refreshed the same search via
    // reloadCurrentSearch()).
    setActionStatus("");
    try {
      const ok =
        state.searchMode === "cycle_count"
          ? await fetchAndRenderCycleCount(searchValue)
          : await fetchAndRenderTask(searchValue);
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

  /** Re-runs the same raw search text to refresh everything currently loaded. */
  async function reloadCurrentSearch() {
    if (!state.lastSearchValue) return;
    if (state.lastSearchMode === "cycle_count") {
      await fetchAndRenderCycleCount(state.lastSearchValue);
    } else {
      await fetchAndRenderTask(state.lastSearchValue);
    }
  }

  function getSelectedLine() {
    if (state.selectedTaskDetailId === null) return null;
    return getLineByTaskDetailId(state.selectedTaskDetailId);
  }

  function remainingQty(line) {
    const rem = Number(line.plannedQuantity || 0) - Number(line.completedQuantity || 0);
    return rem > 0 ? rem : 0;
  }

  /**
   * `line` (not just a taskDetailId) is required now (2026-08-08,
   * multi-LPN search) — its own groupMode/groupTaskId carry which
   * task/container it belongs to, since that's no longer one global
   * value for the whole page. `mode` is always "full" now — Partial
   * Complete was removed; `itemAdjustments` (see
   * collectItemAdjustments()) is what corrects the quantity before
   * completing, when it's not empty.
   */
  async function callCompleteLine(line, itemAdjustments, warningOverrides, toLocationId, reasonCodeId) {
    const isNoTask = line.groupMode === "no_task";
    return api("complete_line", {
      org: state.org,
      token: state.token,
      location: state.facility,
      taskId: isNoTask ? "" : line.groupTaskId,
      taskDetailId: isNoTask ? "" : line.taskDetailId,
      containerId: isNoTask ? line.lpnId : undefined,
      lpnId: line.lpnId || undefined,
      mode: "full",
      itemAdjustments: itemAdjustments && itemAdjustments.length ? itemAdjustments : undefined,
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
  async function completeLineWithWarningHandling(line, itemAdjustments, toLocationId, reasonCodeId) {
    const overrides = {};
    let result = await callCompleteLine(line, itemAdjustments, overrides, toLocationId, reasonCodeId);
    while (result && result.warning) {
      const confirmed = await showWarningModal(result.messageId, result.messageText);
      if (!confirmed) {
        return { success: false, cancelled: true, error: "Cancelled after warning." };
      }
      overrides[result.messageId] = result.messageId;
      result = await callCompleteLine(line, itemAdjustments, overrides, toLocationId, reasonCodeId);
    }
    return result;
  }

  /**
   * The single completion button (2026-08-08 — Partial Complete was
   * removed; its job is now done by editing the row's Completed Qty
   * box, or a MIXED row's per-item boxes, before clicking this — see
   * collectItemAdjustments()). Editable for no-task rows too now
   * (2026-08-08) — the old "no partial concept for a container" limit
   * only applied to the DMM AcceptContainer step itself, which never
   * changes; a different quantity now gets corrected via Modify iLPN
   * *before* that step runs, so it applies there just as well as to a
   * task-mode line.
   */
  async function completeLine() {
    const line = getSelectedLine();
    if (!line) return;
    if (isConsumedLine(line)) {
      setActionStatus(
        "This LPN has already been consumed and can no longer be updated.",
        "error"
      );
      return;
    }
    const remaining = remainingQty(line);
    if (remaining <= 0) {
      setActionStatus("Line " + line.lineNumber + " is already complete.", "error");
      return;
    }
    const isNoTask = line.groupMode === "no_task";
    const itemAdjustments = collectItemAdjustments(line);
    const toLocationId = getLocationOverride(line.taskDetailId);
    if (isNoTask && !toLocationId) {
      setActionStatus("Enter a destination location first.", "error");
      return;
    }
    let reasonCodeId = null;
    if (toLocationId && !isNoTask) {
      reasonCodeId = await promptReasonCode(line, toLocationId);
      if (!reasonCodeId) {
        setActionStatus("Cancelled — a reason code is required to change the destination.", "");
        return;
      }
    }
    setBusy(true, "Completing line " + line.lineNumber + "…");
    try {
      const result = await completeLineWithWarningHandling(line, itemAdjustments, toLocationId, reasonCodeId);
      if (!result.success) {
        if (!result.cancelled) setActionStatus(result.error || "Complete failed", "error");
        return;
      }
      if (result.adjustmentSuccess === false) {
        // 2026-08-08: putaway itself succeeded (the LPN really did
        // move) but the quantity correction afterward failed — a
        // materially different situation from a plain failure, so it
        // gets its own message rather than either a bare "Completed"
        // (misleading — the quantity is still wrong) or a bare error
        // (misleading — the line isn't actually stuck/failed).
        setActionStatus(
          "Line " + line.lineNumber + " completed, but the quantity correction failed: " +
            (result.adjustmentError || "unknown error") + ". Correct it manually.",
          "error"
        );
      } else {
        setActionStatus(
          "Completed " + result.quantity + " " + (result.uomId || "") +
            " on line " + line.lineNumber + ".",
          "success"
        );
      }
      await reloadCurrentSearch();
    } catch (e) {
      setActionStatus(e.message || String(e), "error");
    } finally {
      setBusy(false);
    }
  }

  let allLinesPending = [];

  /**
   * Spans every group on screen (2026-08-08, per explicit instruction —
   * "Complete All" means everything currently visible, not just one
   * task/container).
   */
  function openAllLinesModal() {
    const multiGroup = state.groups.length > 1;
    allLinesPending = allLines().filter((l) => remainingQty(l) > 0);
    if (!allLinesPending.length) {
      setActionStatus("No outstanding lines to complete.", "");
      return;
    }
    el.allLinesList.innerHTML = allLinesPending
      .map((l) => {
        const groupPrefix = multiGroup
          ? (l.groupMode === "no_task" ? "Container " + l.groupContainerId : "Task " + l.groupTaskId) + " — "
          : "";
        return (
          "<li>" + escapeHtml(groupPrefix) + "Line " + escapeHtml(l.lineNumber) + " — " + escapeHtml(l.itemId) + " " +
          escapeHtml(l.description) + ": " + escapeHtml(remainingQty(l)) + " " + escapeHtml(l.uomId || "") + "</li>"
        );
      })
      .join("");
    allLinesModal.show();
  }

  async function confirmAllLines() {
    allLinesModal.hide();
    const total = allLinesPending.length;
    let succeeded = 0;
    let cancelled = false;
    const failures = [];
    // 2026-08-08: a line whose putaway succeeded but whose quantity
    // correction afterward failed counts toward `succeeded` (the LPN
    // really did move) but also gets its own note here — distinct from
    // `failures`, which means the line itself didn't complete at all.
    const adjustmentIssues = [];
    for (let i = 0; i < total; i++) {
      const line = allLinesPending[i];
      const isNoTask = line.groupMode === "no_task";
      const toLocationId = getLocationOverride(line.taskDetailId);
      if (isNoTask && !toLocationId) {
        cancelled = true;
        break;
      }
      const itemAdjustments = collectItemAdjustments(line);
      let reasonCodeId = null;
      if (toLocationId && !isNoTask) {
        setBusy(false);
        reasonCodeId = await promptReasonCode(line, toLocationId);
        if (!reasonCodeId) {
          cancelled = true;
          break;
        }
      }
      setBusy(true, "Completing line " + (i + 1) + " of " + total + "…");
      try {
        const result = await completeLineWithWarningHandling(line, itemAdjustments, toLocationId, reasonCodeId);
        if (result.success) {
          succeeded++;
          if (result.adjustmentSuccess === false) {
            adjustmentIssues.push(
              "Line " + line.lineNumber + ": quantity correction failed (" +
                (result.adjustmentError || "unknown error") + ")"
            );
          }
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
    await reloadCurrentSearch();
    const issues = failures.concat(adjustmentIssues);
    if (cancelled) {
      setActionStatus("Completed " + fmtCount(succeeded, "line") + " before cancelling.", "");
    } else if (!issues.length) {
      setActionStatus("Completed " + fmtCount(succeeded, "line") + ".", "success");
    } else {
      setActionStatus(
        "Completed " + succeeded + " of " + total + " lines. Issues: " + issues.join("; "),
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
    const toggle = e.target.closest(".mixed-toggle");
    if (toggle) {
      const target = toggle.dataset.mixedTarget;
      const expanded = toggle.classList.toggle("expanded");
      const icon = toggle.querySelector("i");
      if (icon) icon.className = expanded ? "fas fa-caret-down" : "fas fa-caret-right";
      el.linesBody
        .querySelectorAll('.mixed-item-row[data-mixed-parent="' + CSS.escape(target) + '"]')
        .forEach((row) => {
          row.style.display = expanded ? "" : "none";
        });
      return;
    }
    const row = e.target.closest("tr.line-row");
    if (!row) return;
    selectLine(row.dataset.taskDetailId);
  });
  el.linesBody.addEventListener("input", (e) => {
    const locInput = e.target.closest(".to-location-input");
    if (locInput) {
      const value = locInput.value.trim().toUpperCase();
      const original = (locInput.dataset.defaultLocation || "").trim().toUpperCase();
      locInput.classList.toggle("overridden", !!value && value !== original);
      validateLocation(locInput);
      return;
    }
    const qtyInput = e.target.closest(".completed-qty-input");
    if (qtyInput) {
      const value = Number(qtyInput.value);
      const original = Number(qtyInput.dataset.defaultQty || 0);
      const overridden = Number.isFinite(value) && value !== original;
      qtyInput.classList.toggle("overridden", overridden);
      validateQty(qtyInput);
      toggleReasonSelect(qtyInput, overridden);
      return;
    }
    // Mixed-item sub-row quantity (2026-08-08) — same override/reason-
    // select behavior as the normal Completed Qty box. The quantity
    // itself still has no gating role in updateLineActionButtons() (see
    // isQtyValid()'s docstring), but its reason code does once shown
    // (see isReasonValid()) — the min="0" input attribute plus this
    // class is just visual feedback for the quantity, not a submission
    // block on its own.
    const mixedQtyInput = e.target.closest(".mixed-qty-input");
    if (mixedQtyInput) {
      const value = Number(mixedQtyInput.value);
      const original = Number(mixedQtyInput.dataset.defaultQty || 0);
      const overridden = Number.isFinite(value) && value !== original;
      mixedQtyInput.classList.toggle("overridden", overridden);
      mixedQtyInput.classList.toggle("invalid", !(Number.isFinite(value) && value >= 0));
      toggleReasonSelect(mixedQtyInput, overridden);
      // Bug fixed 2026-08-08 (live-tested): unlike validateQty() for the
      // single-item box, nothing here re-evaluated button state, so
      // revealing an invalid reason select didn't actually disable
      // Complete Line/Complete All until something else happened to
      // call updateLineActionButtons() afterward.
      updateLineActionButtons();
    }
  });

  /**
   * A shown reason-code select starts on the "Select Reason" placeholder
   * (2026-08-08, per explicit instruction) — red/`invalid` until the
   * user actually picks a real code, which also gates the completion
   * buttons (see isReasonValid()) so a quantity can't be submitted with
   * no reason attached.
   */
  el.linesBody.addEventListener("change", (e) => {
    const select = e.target.closest(".reason-code-select");
    if (!select) return;
    select.classList.toggle("invalid", !select.value);
    updateLineActionButtons();
  });

  function toggleReasonSelect(qtyInput, visible) {
    const row = qtyInput.closest("tr");
    const reasonSelect = row ? row.querySelector(".reason-code-select") : null;
    if (reasonSelect) reasonSelect.classList.toggle("visible", visible);
  }

  const allLinesModal = window.bootstrap
    ? new window.bootstrap.Modal(document.getElementById("allLinesModal"))
    : null;
  const warningModalEl = document.getElementById("warningModal");
  const warningModal = window.bootstrap ? new window.bootstrap.Modal(warningModalEl) : null;
  const reasonCodeModalEl = document.getElementById("reasonCodeModal");
  const reasonCodeModal = window.bootstrap ? new window.bootstrap.Modal(reasonCodeModalEl) : null;

  el.fullLineBtn.addEventListener("click", () => {
    if (state.lastSearchMode === "cycle_count") completeCycleCountLine();
    else completeLine();
  });
  el.allLinesBtn.addEventListener("click", () => {
    if (state.lastSearchMode === "cycle_count") openAllCycleCountLinesModal();
    else openAllLinesModal();
  });
  el.allLinesConfirmBtn.addEventListener("click", () => {
    if (state.lastSearchMode === "cycle_count") confirmAllCycleCountLines();
    else confirmAllLines();
  });

  el.cycleCountLinesBody.addEventListener("click", (e) => {
    const toggle = e.target.closest(".mixed-toggle");
    if (toggle) {
      const target = toggle.dataset.mixedTarget;
      const expanded = toggle.classList.toggle("expanded");
      const icon = toggle.querySelector("i");
      if (icon) icon.className = expanded ? "fas fa-caret-down" : "fas fa-caret-right";
      el.cycleCountLinesBody
        .querySelectorAll('.mixed-item-row[data-mixed-parent="' + CSS.escape(target) + '"]')
        .forEach((row) => {
          row.style.display = expanded ? "" : "none";
        });
      return;
    }
    const row = e.target.closest("tr.cc-line-row, tr.mixed-item-row");
    if (!row) return;
    selectCycleCountGroup(row.dataset.groupKey);
  });
  el.cycleCountLinesBody.addEventListener("input", (e) => {
    const qtyInput = e.target.closest(".cc-qty-input");
    if (qtyInput) {
      validateCycleCountQty(qtyInput);
      return;
    }
    const itemInput = e.target.closest(".cc-item-input");
    if (itemInput) {
      updateCycleCountLineActionButtons();
    }
  });

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
