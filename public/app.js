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
    lastSearchMode: "task", // which table the last successful load used — see reloadCurrentSearch()
    // True when the last load came from a real WM-scheduled Cycle Count
    // TaskId typed into the Task Id/iLPN box (task_service.resolve_search()'s
    // mode:"cycle_count" branch — see fetchAndRenderTask()), as opposed to
    // ad hoc's own Storage-location search. Both render through the same
    // cycle-count table/lastSearchMode value ("cycle_count"), but reload
    // needs to know which endpoint to hit again — see reloadCurrentSearch().
    lastSearchIsTaskedCycleCount: false,
    // Putaway's own multi-select (2026-08-10) — a Set of taskDetailIds,
    // each globally unique across every group per
    // resolve_search_multi()'s docstring. Plain click toggles
    // membership (see selectLine()); 0 or 1 selected behaves exactly
    // as the old single-select model did (immediate "Complete Line",
    // no confirm modal), 2+ relabels the button "Complete Lines" and
    // reuses the same confirm modal "Complete All" already shows,
    // scoped to just the selection (see openSelectedLinesModal()).
    selectedTaskDetailIds: new Set(),
    storageLocations: null, // Set of valid location strings once preloaded, see preloadStorageLocations()
    adjustmentReasonCodes: null, // [{key,value}] once preloaded, see preloadAdjustmentReasonCodes()
    pickReasonCodes: null, // [{key,value}] once preloaded, see preloadPickReasonCodes()
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
    // Pick row selection is tracked separately from
    // state.selectedTaskDetailIds (2026-08-10, thirteenth session) — a
    // split line (see pickSplits below) means one taskDetailId can now
    // render as more than one row, so taskDetailId alone is no longer
    // a unique row identifier the way resolve_search_multi() documents
    // for the rest of this file. See getPickRows()'s `splitId`. A Set,
    // not a single value (2026-08-10, multi-select) — same toggle/
    // relabel/reuse-the-Complete-All-modal model as Putaway's own
    // selectedTaskDetailIds, see that field's docstring.
    selectedPickSplitIds: new Set(),
    // taskDetailId -> [{splitId, quantity}, ...] (2026-08-10) — only
    // present once a line has been explicitly split via splitPickRow();
    // absent means "render as one row covering the full line," the
    // pre-split default. Quantities are independently editable per
    // split, not linked/auto-balanced — see splitPickRow()'s docstring
    // for why.
    pickSplits: {},
    // splitId -> groupKey (2026-08-10) — set when a row is dragged to a
    // different tote group; overrides pickGroupKey()'s natural
    // (slot-or-per-line) grouping for just that row. Cleared implicitly
    // by a fresh search (new state.groups), never explicitly reset.
    pickGroupOverride: {},
    // splitId -> the last complete_pick_line()-shaped result (2026-08-10)
    // — completion state now lives here, not just in the DOM, because
    // splitting/dragging/adding a tote all require re-running
    // renderPickGroups() (grouping itself changes), which would
    // otherwise wipe already-completed rows' status on every such
    // action the way a plain re-render did before this session.
    pickRowStatus: {},
    // Synthetic keys for empty tote groups created via "+ Add Tote"
    // (2026-08-10) — a drop target needs to exist before anything's
    // been dragged into it. Array, not a Set, so insertion order is
    // preserved for tie-broken sorting (see renderPickGroups()).
    extraToteGroups: [],
    // groupKey -> {value, locked, status, statusLabel} (2026-08-10) —
    // the source of truth for every TOTE textbox's own state, since
    // renderPickGroups() now re-runs on every split/drag/add-tote
    // action and would otherwise reset each box to empty on every
    // re-render (it did, briefly, before this was added — see
    // getToteGroupState()). `locked` mirrors what used to be a
    // DOM-only `disabled` flag, set once any row in that group has
    // actually committed (see updatePickToteStatus()).
    toteGroupState: {},
    // taskDetailId -> integer (2026-08-10) — mints unique splitIds
    // across repeated splits of the same line; see nextSplitId().
    pickSplitCounters: {},
    // Array of tote-kind groupKeys currently rendered on the Pick screen
    // (2026-08-10) — repopulated by renderPickGroups() on every render.
    // Used by duplicateToteGroupKeys() to scan across groups without a
    // DOM query; see that function's docstring.
    toteGroupKeysOnScreen: [],
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
   * Every PICK_EXCEPTION reason code, not a hand-picked subset
   * (2026-08-10, per explicit instruction) — the user is manually
   * testing more of the 10 real codes over time and will report results
   * back in chat, so the dropdown always shows the full list;
   * TESTED_PICK_REASON_CODES below just marks which ones are confirmed
   * so far. See pickReasonCodeOptionsHtml().
   */
  async function preloadPickReasonCodes() {
    try {
      const data = await api("preload_pick_reason_codes", {
        org: state.org,
        token: state.token,
        location: state.facility,
      });
      if (data.success) state.pickReasonCodes = data.entries || [];
    } catch (e) {
      // Leave state.pickReasonCodes null — pickReasonCodeOptionsHtml()
      // renders just the placeholder until this resolves.
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
    pickLinesTable: document.getElementById("pickLinesTable"),
    pickLinesBody: document.getElementById("pickLinesBody"),
    addToteBtn: document.getElementById("addToteBtn"),
    fullLineBtn: document.getElementById("fullLineBtn"),
    fullLineBtnLabel: document.getElementById("fullLineBtnLabel"),
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
    taskScanBtn: document.getElementById("taskScanBtn"),
    taskScannerRegion: document.getElementById("taskScannerRegion"),
    taskScanConfirmPanel: document.getElementById("taskScanConfirmPanel"),
    taskScanResultInput: document.getElementById("taskScanResultInput"),
    taskScanConfirmStatus: document.getElementById("taskScanConfirmStatus"),
    taskScanUseBtn: document.getElementById("taskScanUseBtn"),
    taskScanRetryBtn: document.getElementById("taskScanRetryBtn"),
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

  /**
   * Shared between Putaway and Pick (2026-08-10, multi-select — Cycle
   * Count keeps its own single-selection-by-location-group model,
   * unchanged). "Complete Line" relabels to "Complete Lines" once 2+
   * rows are toggled into the selection; back to singular at 0 or 1.
   */
  function setFullLineBtnLabel(selectedCount) {
    if (el.fullLineBtnLabel) {
      el.fullLineBtnLabel.textContent = selectedCount >= 2 ? "Complete Lines" : "Complete Line";
    }
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
      el.matchHint.textContent = "";
    } else if (kind === "mixed") {
      el.matchHint.textContent =
        "Enter either Task Ids/iLPNs or Storage Locations, not both — Confirm is disabled until this is one or the other.";
    } else if (kind === "cycle_count") {
      el.matchHint.textContent = "Press Enter or click Confirm to start an ad hoc Cycle Count.";
    } else {
      el.matchHint.textContent = "Press Enter or click Confirm.";
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
      preloadPickReasonCodes(); // fire-and-forget, see its own docstring
      el.org.value = state.org;
      el.orgSection.style.display = "none";
      el.mainUI.style.display = "block";
      el.taskIdInput.disabled = false;
      if (el.taskScanBtn) el.taskScanBtn.disabled = false;
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
          <td>
            <span class="item-cell">
              ${itemImageCellHtml(item.itemImageUrl)}
              <span>${escapeHtml(item.itemId)}</span>
            </span>
          </td>
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
          <td>
            <span class="item-cell">
              ${itemImageCellHtml(line.itemImageUrl)}
              <span>${escapeHtml(line.itemId)}</span>
            </span>
          </td>
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
    state.selectedTaskDetailIds.clear();
    el.cycleCountLinesTable.style.display = "none";
    el.pickLinesTable.style.display = "none";
    el.linesTable.style.display = "";
    el.addToteBtn.style.display = "none";
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

  function isLineReadyToComplete(line) {
    return (
      !!line &&
      !isConsumedLine(line) &&
      isLocationValid(line.taskDetailId) &&
      isQtyValid(line) &&
      isReasonValid(line)
    );
  }

  function updateLineActionButtons() {
    const selectedLines = getSelectedLines();
    setFullLineBtnLabel(selectedLines.length);
    el.fullLineBtn.disabled = !selectedLines.length || !selectedLines.every(isLineReadyToComplete);
    el.allLinesBtn.disabled = !allOutstandingLinesValid();
  }

  /**
   * Plain click toggles membership (2026-08-10, multi-select) — no
   * modifier key needed. Clicking an already-selected line deselects
   * just that one; clicking a new one adds to whatever's already
   * selected, rather than replacing it the way single-select used to.
   */
  function selectLine(taskDetailId) {
    const id = String(taskDetailId);
    if (state.selectedTaskDetailIds.has(id)) state.selectedTaskDetailIds.delete(id);
    else state.selectedTaskDetailIds.add(id);
    el.linesBody.querySelectorAll("tr.line-row").forEach((row) => {
      row.classList.toggle("selected", state.selectedTaskDetailIds.has(row.dataset.taskDetailId));
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
  // state.groups/allLines()/getLineByTaskDetailId() as-is — a
  // taskDetailId is unique regardless
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
            <span class="item-cell">
              ${itemImageCellHtml(line.itemImageUrl)}
              <input
                type="text"
                class="form-control cc-item-input"
                data-task-detail-id="${escapeAttr(line.taskDetailId)}"
                value="${escapeAttr(line.itemId)}"
                autocomplete="off"
              />
            </span>
          </td>
          <td><div class="col-desc-narrow" title="${escapeAttr(line.description)}">${escapeHtml(line.description)}</div></td>
          <td class="col-qty-wide">
            <input
              type="number"
              class="form-control cc-qty-input${line.quantity == null ? " invalid" : ""}"
              data-task-detail-id="${escapeAttr(line.taskDetailId)}"
              value="${line.quantity == null ? "" : escapeAttr(line.quantity)}"
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
      const g = groups[0];
      // Tasked groups (real WM-scheduled Cycle Count TaskId, see
      // fetchAndRenderTask()) show the real Task Id/status, same as the
      // generic task view — ad hoc has neither, so it keeps the plain
      // Location-only header it always had.
      el.taskMeta.innerHTML = g.isTasked
        ? `
          <span><strong>Task</strong> ${escapeHtml(g.taskId)}</span>
          <span><strong>Location</strong> ${escapeHtml(g.locationId)}</span>
          <span><strong>Status</strong> ${statusBadgeHtml(g.taskStatusLabel, g.taskStatus)}</span>
        `
        : `<span><strong>Location</strong> ${escapeHtml(g.locationId)}</span>`;
    } else {
      el.taskMeta.innerHTML = `<span><strong>${groups.length} locations loaded</strong></span>`;
    }
    el.transactionIdValue.textContent = "Cycle Count Active-API";
  }

  function renderCycleCountGroups() {
    state.selectedCycleCountGroupKey = null;
    el.linesTable.style.display = "none";
    el.pickLinesTable.style.display = "none";
    el.cycleCountLinesTable.style.display = "";
    el.addToteBtn.style.display = "none";
    renderCycleCountTaskMeta();
    el.cycleCountLinesBody.innerHTML = state.groups.map((g) => cycleCountGroupRowsHtml(g)).join("");
    // A view-only group (task_service._resolve_cycle_count_task()'s
    // isReadOnly, 2026-08-09 — a Cycle Count task that's already
    // Completed) carries its historical result on each line already,
    // not from a live poll. Applying it here with done=true reuses
    // setCycleCountResultCell()'s existing side effect of disabling
    // the item/qty inputs and marking the line done — no separate
    // read-only styling/gating needed, since isCycleCountGroupDone()
    // already keeps a done group out of both Complete Line and
    // Complete All.
    (state.groups || []).forEach((g) => {
      (g.lines || []).forEach((line) => {
        if (line.result) {
          setCycleCountResultCell(line.taskDetailId, cycleCountResultText(line.result), cycleCountResultKind(line.result), true);
        }
      });
    });
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

  /**
   * `true` when empty (2026-08-08) — openAllCycleCountLinesModal()
   * already checks emptiness itself before calling this, so that case
   * never actually reaches the check below there. Only
   * updateCycleCountLineActionButtons() relies on the empty-input
   * behavior, and it ANDs this with its own outstanding-count check
   * (see below, 2026-08-09 fix) rather than trusting this return value
   * alone — so an all-done batch (e.g. every group already completed,
   * or a re-searched already-Completed task) correctly greys the
   * button out instead of leaving it clickable-but-a-no-op, which the
   * user found confusing live.
   */
  function allOutstandingCycleCountGroupsValid() {
    const outstanding = allOutstandingCycleCountGroups();
    if (!outstanding.length) return true;
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
    const outstanding = allOutstandingCycleCountGroups();
    el.allLinesBtn.disabled = !outstanding.length || !allOutstandingCycleCountGroupsValid();
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
          taskId: group.taskId || "",
        });
      } catch (e) {
        return; // quietly stop polling -- the rows still show the last known state
      }
      applyCycleCountLocationResultToGroup(group, response);
      // Real finding, 2026-08-09: a tasked group's Task Status can
      // close well before (or without ever) the count itself booking
      // — see CLAUDE.md's "task status alone is never proof" caveat —
      // so this needs its own live refresh here, from the same poll
      // tick, rather than assuming it tracks count status.
      if (response.taskStatus !== undefined) {
        group.taskStatus = response.taskStatus;
        group.taskStatusLabel = response.taskStatusLabel;
        renderCycleCountTaskMeta();
      }
      if (response.success) {
        setActionStatus("Location " + group.locationId + " booked.", "success");
        updateCycleCountLineActionButtons();
        return;
      }
      // Real bug found live, 2026-08-09: the banner used to only ever
      // update on this success branch, so it stayed frozen at whatever
      // the very first response said (e.g. "Still processing (Count
      // Initiated)...") even once the real status moved on — including
      // to a genuinely stuck Pending Booking, which the row itself
      // correctly showed via applyCycleCountLocationResultToGroup()
      // above but the banner never reflected. Refresh it every tick so
      // it tracks whatever's actually true right now.
      setActionStatus(cycleCountGroupResponseSummary(response), "");
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
    // No isTasked flag anymore (2026-08-09) — the backend always calls
    // trigger_end_count() regardless of how the location/task was
    // found, so this app no longer needs to tell it which case it is.
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

  /**
   * A group-level response's top-level `error` only exists for an
   * early hard failure (e.g. initiateCount itself failed) — when it's
   * the per-item results that aren't booked yet (the common case right
   * after submission, before polling catches up), there's no top-level
   * error to fall back to (2026-08-08, found live: the action-status
   * banner showed a bare "Complete failed" for a perfectly healthy
   * in-flight multi-item count). Summarizes each item's own status
   * instead so the banner reflects reality while the poll runs.
   */
  function cycleCountGroupResponseSummary(response) {
    if (response.error) return response.error;
    const results = response.results || [];
    if (!results.length) return "Complete failed";
    // "Pending Booking" is confirmed live — repeatedly, across every
    // out-of-tolerance test this session, single-item and multi-item,
    // ad hoc and tasked — to be a permanent terminal state, not a
    // transient one: MAWM never resolves it on its own, it just sits
    // locked until a supervisor manually books or rejects it (see
    // CLAUDE.md's Cycle Count sections). When every item in the group
    // already shows it, say so plainly instead of "status will keep
    // updating," which the user found actively misleading here
    // (2026-08-09) — nothing further will update on its own.
    if (results.length && results.every((r) => r.status === "Pending Booking")) {
      return "Out of tolerance — pending supervisor booking. Won't resolve on its own.";
    }
    const statuses = results.map((r) => r.status || (r.success ? "Booked" : "Unknown")).join(", ");
    return "Still processing (" + statuses + ") — status will keep updating.";
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
        setActionStatus(cycleCountGroupResponseSummary(response), "");
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
          failures.push(cycleCountFailureLabel(group, cycleCountGroupResponseSummary(response)));
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
    const groups = data.groups || [];
    // A real WM-scheduled Cycle Count TaskId typed into this same box
    // resolves server-side into cycle-count-shaped groups
    // (task_service.resolve_search()'s mode:"cycle_count" branch,
    // 2026-08-09) — route those through the cycle-count table exactly
    // like ad hoc, rather than the generic task rendering that would
    // otherwise misrender a Cycle Count task's own itemless TaskDetail.
    // A real Pick TaskId or oLPN (2026-08-10) resolves into
    // mode:"pick" groups the same way — routed to its own table below.
    // Mixed batches (some cycle-count, some pick, some plain task)
    // aren't specially handled — falls back to the generic table, a
    // known limitation.
    const isTaskedCycleCount = groups.length > 0 && groups.every((g) => g.mode === "cycle_count");
    const isPick = groups.length > 0 && groups.every((g) => g.mode === "pick");
    // A genuinely new search must not carry over the previous search's
    // Pick split/drag/tote state (2026-08-10, thirteenth session, found
    // live — a stale state.extraToteGroups entry from an earlier Pick
    // search rendered as a phantom extra header on an unrelated later
    // one). Reset here regardless of what this search turns out to be,
    // not just when it's Pick — the whole point is nothing should
    // survive past the task/oLPN box being searched again.
    state.pickSplits = {};
    state.pickGroupOverride = {};
    state.pickRowStatus = {};
    state.extraToteGroups = [];
    state.toteGroupState = {};
    state.pickSplitCounters = {};
    state.groups = groups;
    state.lastSearchValue = searchValue;
    state.lastSearchMode = isTaskedCycleCount ? "cycle_count" : isPick ? "pick" : "task";
    state.lastSearchIsTaskedCycleCount = isTaskedCycleCount;
    if (isTaskedCycleCount) {
      renderCycleCountGroups();
    } else if (isPick) {
      renderPickGroups();
    } else {
      renderGroups();
    }
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
    state.lastSearchIsTaskedCycleCount = false;
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
    // Ad hoc cycle count and tasked cycle count share lastSearchMode
    // ("cycle_count", so every other button/completion check below
    // doesn't need to distinguish them) but came from different
    // endpoints — lastSearchIsTaskedCycleCount is the only place that
    // still needs to tell them apart, to re-fetch from the right one.
    if (state.lastSearchMode === "cycle_count" && !state.lastSearchIsTaskedCycleCount) {
      await fetchAndRenderCycleCount(state.lastSearchValue);
    } else {
      await fetchAndRenderTask(state.lastSearchValue);
    }
  }

  function getSelectedLines() {
    return Array.from(state.selectedTaskDetailIds)
      .map((id) => getLineByTaskDetailId(id))
      .filter(Boolean);
  }

  /** The single selected line, or null when 0 or 2+ are selected (see
   * getSelectedLines()) — completeLine() only ever gets called for the
   * 0-or-1 case, the 2+ case routes to openSelectedLinesModal()
   * instead, so returning null here for 2+ is a safe, unreachable-in-
   * practice fallback rather than a real behavior. */
  function getSelectedLine() {
    const lines = getSelectedLines();
    return lines.length === 1 ? lines[0] : null;
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

  function renderAllLinesModalList(pending) {
    const multiGroup = state.groups.length > 1;
    el.allLinesList.innerHTML = pending
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
  }

  /**
   * Spans every group on screen (2026-08-08, per explicit instruction —
   * "Complete All" means everything currently visible, not just one
   * task/container).
   */
  function openAllLinesModal() {
    allLinesPending = allLines().filter((l) => remainingQty(l) > 0);
    if (!allLinesPending.length) {
      setActionStatus("No outstanding lines to complete.", "");
      return;
    }
    renderAllLinesModalList(allLinesPending);
    allLinesModal.show();
  }

  /**
   * Multi-select's "Complete Lines" (2026-08-10) — same modal, same
   * confirmAllLines() submission loop as "Complete All", just scoped
   * to the lines the user actually toggled on instead of every
   * outstanding line. Selection itself isn't explicitly cleared here —
   * confirmAllLines() already ends with reloadCurrentSearch(), a full
   * re-fetch/re-render that resets state.selectedTaskDetailIds as a
   * side effect (see renderGroups()), the same way it already reset
   * the old single-select field before this feature existed.
   */
  function openSelectedLinesModal() {
    allLinesPending = getSelectedLines().filter((l) => remainingQty(l) > 0);
    if (!allLinesPending.length) {
      setActionStatus("No outstanding lines to complete.", "");
      return;
    }
    renderAllLinesModalList(allLinesPending);
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

  // ---------------------------------------------------------------------
  // Picking (2026-08-10, eleventh session) — its own table
  // (#pickLinesTable) but a much simpler completion model than Putaway
  // or Cycle Count: no warning-confirm modal, no reason code, no
  // location override — commitPickMove() is a plain source/destination/
  // quantity commit. Confirmed live: synchronous (no polling needed,
  // unlike Cycle Count's async booking) and one call closes the task
  // automatically when it's the last open line (no separate "end"/
  // "trigger" call, unlike Cycle Count). Lines are INDEPENDENT
  // (confirmed live: completing one doesn't require the others to be
  // addressed first, unlike Cycle Count's atomic multi-item locations),
  // so selection/completion is per-LINE like Putaway, not per-GROUP
  // like Cycle Count — see state.selectedPickSplitIds's own docstring
  // for why Pick ended up with its own dedicated field instead of
  // reusing Putaway's state.selectedTaskDetailIds directly (a split
  // line's own splitId isn't the same thing as a taskDetailId).
  //
  // Locked down server-side (task_service._classify_pick_task()) to
  // `TaskExecutionMode: "PICK_INTO_OLPN"` tasks with every line sourced
  // from a plain `LOCATION` — an iLPN-sourced/full-container line hits
  // a real, unresolved MAWM validation bug regardless of payload shape
  // (see CLAUDE.md's Picking section), so those tasks are refused with
  // a clear reason at search time and never reach this rendering code.
  // ---------------------------------------------------------------------

  /**
   * Codes the user has manually re-tested through the real WM UI/API and
   * confirmed clean — no unexpected warnings or blocking prompts (see
   * CLAUDE.md's "Picking: short-pick exception mechanism confirmed"
   * section for the first three and the location-tainting caveat on
   * "Short Pick and Lock Location"). Update this set as the user reports
   * more over time (2026-08-10, per explicit instruction) — the dropdown
   * itself always lists all 10 real PICK_EXCEPTION codes
   * (state.pickReasonCodes), this only controls the "*" marker.
   */
  const TESTED_PICK_REASON_CODES = new Set([
    "PickCancel",
    "Short Pick and Lock Location",
    "Short pick carton",
  ]);

  function pickReasonCodeOptionsHtml() {
    const codes = state.pickReasonCodes || [];
    const options = codes
      .map((c) => {
        const label = c.key + (TESTED_PICK_REASON_CODES.has(c.value) ? " *" : "");
        return `<option value="${escapeAttr(c.value)}">${escapeHtml(label)}</option>`;
      })
      .join("");
    return '<option value="" selected>Select Reason Code</option>' + options;
  }

  /**
   * A short pick (Completed Qty entered below Planned Qty) requires an
   * explicit reason-code selection before it can submit (2026-08-10, per
   * explicit instruction — mirrors Putaway's own reason-code-select
   * pattern: `.reason-code-select`/`.overridden`/`toggleReasonSelect()`/
   * the "invalid" placeholder are all reused as-is, scoped to
   * el.pickLinesBody instead of el.linesBody). A plain full-quantity
   * pick never shows or requires this — see the qty `input` handler
   * below, which is the only place `.overridden`/visibility gets set for
   * Pick rows.
   *
   * Renders a `row` (see getPickRows()), not a raw line — everything
   * interactive keys off `row.splitId`, the only value guaranteed
   * unique once a line's been split (2026-08-10). "Planned Qty" still
   * shows the *line's* original full quantity (the overall ask);
   * "Completed Qty" defaults to *this row's* own quantity (its slice,
   * whether split or the whole thing) and is independently editable.
   * Split/drag (see splitPickRow()/the dragstart handler below) are
   * only offered on a not-yet-done tote-destined row — an oLPN row's
   * destination is already fixed by the task data, nothing to split or
   * move.
   */

  // Custom inline SVG, not a FontAwesome icon (2026-08-10) — the
  // previous fa-code-branch read as a USB symbol at this size, per
  // explicit feedback. Up arrow / bar / down arrow, echoing the
  // reference image the user provided — "one line, splitting apart in
  // two directions." `fill="currentColor"` so it follows
  // .pick-split-btn's own `color` (hover/etc. all work for free).
  const PICK_SPLIT_ICON_SVG =
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true">' +
    '<polygon points="12,1 17,7 7,7" />' +
    '<rect x="10" y="6" width="4" height="4" />' +
    '<rect x="6" y="11" width="12" height="2" rx="1" />' +
    '<rect x="10" y="14" width="4" height="4" />' +
    '<polygon points="12,23 7,17 17,17" />' +
    "</svg>";

  /**
   * Item thumbnail (2026-08-10) — same `.item-image-wrap`/
   * `.item-image-thumb`/`.item-image-wrap--empty` markup and CSS as
   * receivingworkbench's own `itemImageCellHtml()`, so this reads
   * identically if both apps are ever open side by side. Picking-only
   * for now per explicit instruction ("we can do this now for picking
   * only but most likely will add it to our other screens as well") —
   * Putaway/Cycle Count don't call this yet. Unlike
   * receivingworkbench's version, a broken/unreachable `imageUrl`
   * (non-empty but the image itself 404s) falls back to the same
   * empty-state look instead of the browser's default broken-image
   * icon — see the delegated `error`-event listener (capture phase,
   * since `error` doesn't bubble) near the bottom of this section.
   */
  function itemImageCellHtml(imageUrl) {
    if (imageUrl) {
      return (
        '<span class="item-image-wrap item-image-wrap--inline" data-image-url="' +
        escapeAttr(imageUrl) +
        '"><img class="item-image-thumb" src="' +
        escapeAttr(imageUrl) +
        '" alt="" loading="lazy" /></span>'
      );
    }
    return '<span class="item-image-wrap item-image-wrap--empty">—</span>';
  }

  function pickLineRowHtml(row) {
    const isDone = isPickRowDone(row.splitId);
    const tracked = state.pickRowStatus[row.splitId];
    const qtyValue = isDone ? (tracked && tracked.completedQuantity != null ? tracked.completedQuantity : row.quantity) : row.quantity;
    const canSplitOrDrag = row.isToteDestined && !isDone;
    return `
        <tr class="pick-line-row line-row" data-split-id="${escapeAttr(row.splitId)}" data-task-detail-id="${escapeAttr(row.taskDetailId)}" ${canSplitOrDrag ? 'draggable="true"' : ""}>
          <td class="col-line">${escapeHtml(row.lineNumber)}</td>
          <td class="col-loc">${escapeHtml(row.sourceLocationId)}</td>
          <td class="col-item">
            <span class="item-cell">
              ${itemImageCellHtml(row.itemImageUrl)}
              <span>${escapeHtml(row.itemId)}</span>
            </span>
          </td>
          <td><div class="col-desc-narrow" title="${escapeAttr(row.description)}">${escapeHtml(row.description)}</div></td>
          <td class="col-qty-wide">${escapeHtml(row.plannedQuantity)}</td>
          <td class="col-uom">${escapeHtml(row.uomTypeId)}</td>
          <td class="col-qty-wide">
            <input
              type="number"
              class="form-control pick-qty-input"
              data-split-id="${escapeAttr(row.splitId)}"
              data-default-qty="${escapeAttr(row.quantity)}"
              value="${escapeAttr(qtyValue)}"
              step="any"
              ${isDone ? "disabled" : ""}
            />
          </td>
          <td class="col-split">
            ${canSplitOrDrag ? `<button type="button" class="pick-split-btn" data-split-id="${escapeAttr(row.splitId)}" title="Split this line across totes">${PICK_SPLIT_ICON_SVG}</button>` : ""}
          </td>
          <td class="col-reason">
            <select class="form-select reason-code-select invalid" data-split-id="${escapeAttr(row.splitId)}">
              ${pickReasonCodeOptionsHtml()}
            </select>
          </td>
          <td class="col-reason pick-result" data-split-id="${escapeAttr(row.splitId)}" data-done="${isDone}">${isDone ? "Completed" : ""}</td>
        </tr>`;
  }

  /**
   * A full-width divider row shown once per distinct oLPN before its
   * own lines (2026-08-10, per explicit instruction — a single
   * PICK_INTO_OLPN task can span multiple distinct oLPNs, confirmed
   * live this session with real examples, not just a hypothetical
   * pick-cart concern). No confirmed oLPN status-code label mapping
   * exists yet (see task_service._resolve_pick_task()'s docstring) —
   * statusBadgeHtml(null, status) shows the raw code in a badge rather
   * than guessing at a translation.
   *
   * Type/Size always shown (2026-08-10, twelfth session — the oLPN's
   * own `ContainerTypeId`/`ContainerSizeId`, e.g. "BOX"/"MED"), Slot
   * shown only when `slotId` is populated — a cart-picking task per
   * explicit instruction, keyed purely off whether the data itself
   * carries a slot, never off TaskExecutionMode/TransactionId text
   * (see task_service._resolve_pick_task()'s slot_by_olpn comment).
   *
   * `PlannedSlotId`'s own text format is NOT consistent across real
   * cart plans — confirmed live 2026-08-10 against 3 different tasks:
   * a plain number ("2"), a pre-labeled string ("Slot 1"), and a
   * terse lowercase form ("slot1"). Blindly prepending "Slot " would
   * double up on the already-labeled case ("Slot Slot 1", seen live
   * before this fix), so this only adds the "Slot " prefix when the
   * raw value doesn't already start with it (case-insensitive).
   */
  function pickOlpnSlotLabel(slotId) {
    if (!slotId) return "";
    return /^slot\b/i.test(slotId) ? slotId : `Slot ${slotId}`;
  }

  /**
   * The grouping key for a line. An oLPN-destined line groups by its
   * real, already-known `olpnId` (unchanged). A tote-destined line's
   * grouping depends on *why* it doesn't have an oLPN yet (2026-08-10,
   * per explicit correction — see CLAUDE.md's "one tote, not one per
   * line" note): on a cart, each slot is a physically distinct tote —
   * a picker genuinely needs a separate one per slot, so it groups by
   * `plannedSlotId`. On a plain non-cart "Pick To Tote" task, there's
   * no slot at all — those lines all go into *one* tote by default
   * (packing splits them out into their eventual separate oLPNs later,
   * not this app's concern), so every slot-less tote-destined line on
   * the same task shares one default group key. Splitting that default
   * group into more totes is still available via the Split action —
   * this is only the *starting* grouping, not a hard limit.
   */
  function pickGroupKey(line) {
    if (line.isToteDestined) {
      return line.plannedSlotId ? "slot:" + line.plannedSlotId : "tote-default:" + line.groupTaskId;
    }
    return line.olpnId || "";
  }

  /**
   * A row's *actual* group — the natural key (pickGroupKey()) unless
   * the user dragged it elsewhere (2026-08-10, thirteenth session,
   * split/drag UI — see state.pickGroupOverride's docstring). Rows,
   * not lines, are what get grouped/rendered from here on; see
   * getPickRows().
   */
  function pickRowGroupKey(row) {
    return state.pickGroupOverride[row.splitId] || pickGroupKey(row);
  }

  /**
   * A row is "done" if this app has already tracked a real completion
   * result for it (state.pickRowStatus — set by completePickRow()),
   * or — only for the default, never-split case, where splitId equals
   * the real taskDetailId — if the original search already showed it
   * completed (line.status). A synthetic split row can never fall into
   * the second case: splitting only ever happens on a not-yet-done
   * line (see splitPickRow()), and its splitId never matches a real
   * taskDetailId, so getLineByTaskDetailId() correctly returns null.
   */
  function isPickRowDone(splitId) {
    const tracked = state.pickRowStatus[splitId];
    if (tracked) return !!tracked.success;
    const line = getLineByTaskDetailId(splitId);
    return !!line && line.status === "8000";
  }

  /**
   * Expands allLines() into render rows (2026-08-10) — one row per
   * line by default (covering its full remaining quantity), or one row
   * per entry in state.pickSplits[taskDetailId] once a line has been
   * explicitly split. `splitId` is the unique per-row key everything
   * else in this section (selection, qty/reason inputs, completion,
   * done-tracking) keys off from now on — taskDetailId is no longer
   * unique once a line is split, so it's kept on the row only for
   * building the completion payload (the backend commit still targets
   * the real taskDetailId; confirmed live this session that MAWM
   * correctly handles multiple independent partial commits against the
   * same TaskDetailId, each with its own quantity/target container —
   * see CLAUDE.md's incremental-split-picking section).
   */
  function getPickRows() {
    const rows = [];
    allLines().forEach((line) => {
      const splits = state.pickSplits[line.taskDetailId];
      if (splits && splits.length) {
        splits.forEach((part) => {
          rows.push({ ...line, splitId: part.splitId, quantity: part.quantity });
        });
      } else {
        rows.push({ ...line, splitId: line.taskDetailId, quantity: line.plannedQuantity });
      }
    });
    return rows;
  }

  function getPickRowBySplitId(splitId) {
    return getPickRows().find((r) => r.splitId === String(splitId)) || null;
  }

  /** Lazily-initialized per-group TOTE textbox state — see
   * state.toteGroupState's docstring. Always returns the same object
   * for a given groupKey across calls, so callers can mutate it
   * in-place (e.g. `getToteGroupState(key).value = "..."`). */
  function getToteGroupState(groupKey) {
    if (!state.toteGroupState[groupKey]) {
      state.toteGroupState[groupKey] = {
        value: "",
        locked: false,
        status: "",
        statusLabel: "",
        // Live validation (2026-08-10) — see scheduleToteValidation().
        // `validated` is `null` (unknown/not yet checked) until a real
        // check resolves; `true`/`false` after. Resets to `null`
        // whenever the value changes, so a stale confirmation from a
        // previous value can never be mistaken for the current one.
        validated: null,
        validating: false,
        validationMessage: "",
      };
    }
    return state.toteGroupState[groupKey];
  }

  /** Every tote-kind groupKey on screen (state.toteGroupKeysOnScreen,
   * populated by renderPickGroups()) whose own trimmed, uppercased
   * value collides with another group's — a cart's slot-based totes
   * and any user-added tote (see "+ Add Tote") all share one flat
   * namespace on a single task screen (2026-08-10, per explicit
   * instruction: "we should not allow the same toteIDs on a single
   * screen, whether its a cart or if a user adds a tote"). Includes
   * locked (already-committed) groups in the scan — a locked box's own
   * display never shows invalid (see pickGroupHeaderRowHtml()), but its
   * value still has to block a *new* box from reusing it. Uppercased
   * comparison since this is a client-side UX guard, not a data
   * operation — the backend's own reuse check (validate_pick_tote_id())
   * is the real source of truth. */
  function duplicateToteGroupKeys() {
    const byValue = new Map();
    (state.toteGroupKeysOnScreen || []).forEach((key) => {
      const raw = getToteGroupState(key).value;
      const norm = String(raw || "").trim().toUpperCase();
      if (!norm) return;
      if (!byValue.has(norm)) byValue.set(norm, []);
      byValue.get(norm).push(key);
    });
    const dupes = new Set();
    byValue.forEach((keys) => {
      if (keys.length > 1) keys.forEach((k) => dupes.add(k));
    });
    return dupes;
  }

  /** Re-renders every tote group's header display (2026-08-10) — a
   * value change in one group can flip another group's duplicate
   * status (see duplicateToteGroupKeys()), so a single-group render
   * isn't enough once duplicates are possible. Cheap: at most a
   * handful of groups per task. */
  function refreshToteValidationDisplays() {
    (state.toteGroupKeysOnScreen || []).forEach((key) => renderToteValidationState(key));
  }

  /** Mints a unique splitId for a new piece of a split line
   * (2026-08-10) — see splitPickRow(). */
  function nextSplitId(taskDetailId) {
    const n = (state.pickSplitCounters[taskDetailId] || 0) + 1;
    state.pickSplitCounters[taskDetailId] = n;
    return taskDetailId + "#" + n;
  }

  /**
   * A repeated column-header row rendered once per group (2026-08-10,
   * per explicit instruction to test this layout instead of one fixed
   * header at the top of the table) — same 10 columns as
   * #pickLinesTable's own (now-hidden, see .pick-repeats-headers)
   * <thead>, kept in sync by hand since there's no single source of
   * truth for both. The blank `col-split` header (between Picked
   * Qty and Reason Code) is the Split icon's own column — see
   * pickLineRowHtml(). "Required Qty"/"Picked Qty" (2026-08-10, renamed
   * from "Planned Qty"/"Completed Qty" per explicit instruction) —
   * Pick-only; Putaway's #linesTable keeps its own original labels.
   */
  function pickColumnHeaderRowHtml() {
    return `
        <tr class="pick-column-header">
          <td class="col-line">Line</td>
          <td class="col-loc">Source Location</td>
          <td class="col-item">Item</td>
          <td class="col-desc-narrow">Description</td>
          <td class="col-qty-wide">Required Qty</td>
          <td class="col-uom"></td>
          <td class="col-qty-wide">Picked Qty</td>
          <td class="col-split"></td>
          <td class="col-reason">Reason Code</td>
          <td class="col-reason">Status</td>
        </tr>`;
  }

  /**
   * One group's header row — either an oLPN (id, Type/Size, Slot,
   * status badge — unchanged from before) or a TOTE (a required
   * textbox instead of an id, since the real container doesn't exist
   * until the picker enters one; see isPickToteValid()). `info` comes
   * from renderPickGroups()'s own groupInfoFor() — for a tote group it
   * includes state.toteGroupState's own fields (value/locked/status/
   * statusLabel), so a re-render (now routine once split/drag/add-tote
   * are in play, see getPickRows()) doesn't reset an already-typed or
   * already-committed tote box back to empty. Also a drop target for
   * dragging a line into this group — see the dragover/drop listeners
   * near the bottom of this section — and its own drop target even
   * when empty (an "+ Add Tote" group with zero rows so far). A tote
   * box also gates on duplicateToteGroupKeys() (2026-08-10) — see that
   * function's docstring.
   */
  function pickGroupHeaderRowHtml(groupKey, info) {
    const slotLabel = pickOlpnSlotLabel(info.slotId);
    // Slot shown before TOTE/oLPN, bold/larger via .pick-olpn-slot
    // (2026-08-10, per explicit instruction — a cart picker locates a
    // group by its slot first, the container label second).
    const slotHtml = slotLabel ? `<span class="pick-olpn-slot">${escapeHtml(slotLabel)}</span>` : "";
    if (info.kind === "tote") {
      const hasValue = !!(info.value && info.value.trim());
      const isDuplicate = !info.locked && duplicateToteGroupKeys().has(groupKey);
      // Not locked yet: invalid until a real check confirms it (empty,
      // still checking, confirmed bad, or a duplicate of another tote
      // group already on this screen all render red — only
      // `validated === true` with no duplicate clears it). Locked
      // (already committed): always fine, it already proved itself via
      // a real commit.
      const isInvalid = !info.locked && (!hasValue || info.validated !== true || isDuplicate);
      const message = info.locked
        ? ""
        : isDuplicate
          ? "Duplicate tote id — already used on this screen"
          : info.validating
            ? "Checking…"
            : info.validationMessage || "";
      return `
        <tr class="pick-olpn-header pick-tote-header" data-group-key="${escapeAttr(groupKey)}">
          <td colspan="10">
            ${slotHtml}<strong>TOTE</strong>
            <input
              type="text"
              class="form-control tote-id-input${isInvalid ? " invalid" : ""}${info.validating ? " checking" : ""}"
              data-group-key="${escapeAttr(groupKey)}"
              placeholder="Enter tote id"
              value="${escapeAttr(info.value || "")}"
              ${info.locked ? "disabled" : ""}
            />
            <span class="tote-validation-msg">${escapeHtml(message)}</span>
            <span class="pick-olpn-status">${info.status ? statusBadgeHtml(info.statusLabel, info.status) : ""}</span>
          </td>
        </tr>`;
    }
    const typeSize = [info.containerTypeId, info.containerSizeId].filter(Boolean).join(" / ");
    return `
        <tr class="pick-olpn-header" data-olpn-id="${escapeAttr(info.containerId)}" data-group-key="${escapeAttr(groupKey)}">
          <td colspan="10">
            ${slotHtml}<strong>oLPN</strong> ${escapeHtml(info.containerId)}
            ${typeSize ? `<span class="pick-olpn-type-size">${escapeHtml(typeSize)}</span>` : ""}
            <span class="pick-olpn-status">${info.status ? statusBadgeHtml(info.statusLabel, info.status) : ""}</span>
          </td>
        </tr>`;
  }

  /**
   * Refreshes one oLPN's status badge in place after a completion
   * (2026-08-10) — confirmed live an oLPN's own status can change the
   * moment a line commits (e.g. "1000" -> "7200", i.e. "Created" ->
   * "Packed" — see mawm_client.OLPN_STATUS_LABELS), same reasoning as
   * the task-status live-update fix earlier this session. Updates both
   * the DOM (no full re-render needed) and state.groups so a later
   * reload/re-render still has the right value.
   */
  function updatePickOlpnStatus(olpnId, status, statusLabel) {
    if (!olpnId) return;
    state.groups.forEach((g) => {
      if (g.olpnStatuses && g.olpnStatuses[olpnId] !== undefined) {
        // Merge, not replace (2026-08-10) — slotId/containerTypeId/
        // containerSizeId don't come back from a line completion, only
        // status/statusLabel do; a plain replace would silently drop
        // them and make the Slot/Type-Size header disappear after the
        // first completion on that oLPN.
        g.olpnStatuses[olpnId] = { ...g.olpnStatuses[olpnId], status, statusLabel };
      }
    });
    const header = el.pickLinesBody.querySelector(
      'tr.pick-olpn-header[data-olpn-id="' + CSS.escape(String(olpnId)) + '"] .pick-olpn-status'
    );
    if (header) header.innerHTML = status ? statusBadgeHtml(statusLabel, status) : "";
  }

  /**
   * Mirrors updatePickOlpnStatus() for a tote group (2026-08-10) — once
   * a tote-destined line actually commits, the real iLPN now exists
   * (see mawm_client.commit_pick_move()'s docstring), so the textbox
   * gets locked to whatever value just succeeded (further edits to a
   * container that's already real would be misleading) and a status
   * badge appears next to it, same visual language as an oLPN group.
   * Updates state.toteGroupState first (so a later re-render — routine
   * now, see getPickRows()'s docstring — keeps this locked/labeled
   * instead of reverting), then patches the live DOM directly for
   * immediate feedback without waiting on a full re-render.
   */
  function updatePickToteStatus(groupKey, toteId, status, statusLabel) {
    if (!groupKey) return;
    clearTimeout(toteValidateTimers[groupKey]);
    const gs = getToteGroupState(groupKey);
    gs.value = toteId || gs.value;
    gs.locked = true;
    gs.validated = true;
    gs.validating = false;
    gs.validationMessage = "";
    gs.status = status;
    gs.statusLabel = statusLabel;
    const header = el.pickLinesBody.querySelector(
      'tr.pick-tote-header[data-group-key="' + CSS.escape(String(groupKey)) + '"]'
    );
    if (!header) return;
    const input = header.querySelector(".tote-id-input");
    if (input) {
      input.value = gs.value;
      input.disabled = true;
      input.classList.remove("invalid");
    }
    let badge = header.querySelector(".pick-olpn-status");
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "pick-olpn-status";
      header.querySelector("td").appendChild(badge);
    }
    badge.innerHTML = status ? statusBadgeHtml(statusLabel, status) : "";
  }

  function renderPickTaskMeta() {
    const groups = state.groups;
    if (groups.length === 1) {
      const g = groups[0];
      el.taskMeta.innerHTML = `
        <span><strong>Task</strong> ${escapeHtml(g.taskId)}</span>
        <span><strong>Status</strong> ${statusBadgeHtml(g.taskStatusLabel, g.taskStatus)}</span>
        <span><strong>oLPN</strong> ${escapeHtml((g.olpnIds || []).join(", "))}</span>
      `;
      el.transactionIdValue.textContent = g.taskTransactionId || "Picking";
    } else {
      el.taskMeta.innerHTML = `<span><strong>${groups.length} tasks loaded</strong></span>`;
      el.transactionIdValue.textContent = "Picking";
    }
  }

  /**
   * Grouped by container (oLPN, or — since 2026-08-10 — tote, see
   * pickRowGroupKey()), not one flat row list — each group gets its own
   * header row followed by its own repeated column-header row (see
   * pickColumnHeaderRowHtml() — per explicit instruction to test this
   * layout instead of one fixed header at the top) and just its own
   * rows. Renders `getPickRows()` (a line expanded per its splits, if
   * any — see that function's docstring), not raw lines directly, so
   * split parts of the same line can end up in different groups. Row
   * ordering *within* a group follows the order rows were produced in
   * (task-detail sequence, split parts immediately after their
   * siblings), not re-sorted.
   *
   * Group ORDER (2026-08-10, thirteenth session, per explicit
   * instruction: "group and sort by slot and then by olpn/tote") —
   * groups sort by slot number first (when present; extracted from the
   * slot text, not a bare Number() — see slotSortKey()), then by the
   * group's own label (its oLPN id, or its group key for a tote group
   * with no real id yet) as a stable secondary/tie-break — this always
   * runs now, not just for slotted tasks, so even a plain multi-oLPN
   * task with no slots gets a deterministic (alphabetical) order
   * instead of first-appearance order.
   *
   * Called far more often now than when first built (2026-08-10) — any
   * split, drag, or "+ Add Tote" click re-runs this, not just a fresh
   * search — which is exactly why completion/tote state moved out of
   * the DOM and into state.pickRowStatus/state.toteGroupState: without
   * that, every one of those actions would silently wipe already-
   * completed rows and already-typed tote ids.
   */
  function renderPickGroups() {
    state.selectedPickSplitIds.clear();
    el.linesTable.style.display = "none";
    el.cycleCountLinesTable.style.display = "none";
    el.pickLinesTable.style.display = "";
    renderPickTaskMeta();

    const rows = getPickRows();
    const groupKeys = [];
    const byGroup = new Map();
    rows.forEach((row) => {
      const key = pickRowGroupKey(row);
      if (!byGroup.has(key)) {
        byGroup.set(key, []);
        groupKeys.push(key);
      }
      byGroup.get(key).push(row);
    });
    // Empty drop targets from "+ Add Tote" (2026-08-10) — a group has
    // to exist before anything's been dragged into it.
    state.extraToteGroups.forEach((key) => {
      if (!byGroup.has(key)) {
        byGroup.set(key, []);
        groupKeys.push(key);
      }
    });

    el.addToteBtn.style.display = rows.some((r) => r.isToteDestined) ? "" : "none";

    const groupInfoFor = (key) => {
      const groupRows = byGroup.get(key) || [];
      const firstRow = groupRows[0];
      if (!firstRow || firstRow.isToteDestined) {
        return { kind: "tote", containerId: "", slotId: firstRow ? firstRow.plannedSlotId : "", ...getToteGroupState(key) };
      }
      const owningGroup = state.groups.find((g) => g.taskId === firstRow.groupTaskId);
      const olpnInfo =
        owningGroup && owningGroup.olpnStatuses ? owningGroup.olpnStatuses[firstRow.olpnId] || {} : {};
      return { kind: "olpn", containerId: firstRow.olpnId, ...olpnInfo };
    };

    // Feeds duplicateToteGroupKeys() (2026-08-10) — every tote-kind
    // group currently on screen, so a duplicate scan never needs a DOM
    // query and works the instant a value changes, before any
    // debounced validation resolves.
    state.toteGroupKeysOnScreen = groupKeys.filter((key) => groupInfoFor(key).kind === "tote");

    // slotId's own text isn't always a bare number (see
    // pickOlpnSlotLabel()'s docstring — "2", "Slot 1", "slot1" all seen
    // live), so sorting pulls out the first digit run rather than
    // Number()-ing the whole string directly.
    const slotSortKey = (slotId) => {
      const match = String(slotId || "").match(/\d+/);
      return match ? Number(match[0]) : NaN;
    };
    // A group with no real container id yet — an empty "+ Add Tote"
    // group, or an ungrouped tote line with no slot, keyed
    // synthetically by its own splitId (see pickGroupKey()) — has
    // nothing meaningful to sort by; comparing "" to "" ties
    // (localeCompare returns 0), so Array.sort's stability keeps these
    // in their original order instead of an arbitrary ordering the
    // synthetic key itself would otherwise produce.
    const groupLabel = (key, info) => info.containerId || "";

    groupKeys.sort((a, b) => {
      const infoA = groupInfoFor(a);
      const infoB = groupInfoFor(b);
      const slotA = slotSortKey(infoA.slotId);
      const slotB = slotSortKey(infoB.slotId);
      const aHasSlot = Number.isFinite(slotA);
      const bHasSlot = Number.isFinite(slotB);
      if (aHasSlot && bHasSlot && slotA !== slotB) return slotA - slotB;
      if (aHasSlot !== bHasSlot) return aHasSlot ? -1 : 1;
      return groupLabel(a, infoA).localeCompare(groupLabel(b, infoB));
    });

    el.pickLinesBody.innerHTML = groupKeys
      .map((key) => {
        const groupRows = byGroup.get(key) || [];
        const info = groupInfoFor(key);
        const header = key ? pickGroupHeaderRowHtml(key, info) : "";
        return header + pickColumnHeaderRowHtml() + groupRows.map((row) => pickLineRowHtml(row)).join("");
      })
      .join("");
    updatePickLineActionButtons();
  }

  function getPickQtyInput(splitId) {
    return el.pickLinesBody.querySelector(
      '.pick-qty-input[data-split-id="' + CSS.escape(String(splitId)) + '"]'
    );
  }

  /** Capped at this row's own required qty (2026-08-10, per explicit
   * instruction: "we should not allow a user to submit a qty > the
   * planned qty... we need to account for a split line as well") —
   * the cap is `data-default-qty`, the row's own slice (its full
   * remaining amount if not split, or just its own piece if it has
   * been — see pickLineRowHtml()), not the line's original full
   * Required Qty column value. The spinner's up-arrow is never blocked
   * (see the qty `input` handler's `.exceeds` class) — only submission
   * is gated, here and via updatePickLineActionButtons(). */
  function isPickQtyValid(splitId) {
    const input = getPickQtyInput(splitId);
    if (!input) return false;
    const raw = input.value.trim();
    if (raw === "") return false;
    const value = Number(raw);
    if (!Number.isFinite(value)) return false;
    const cap = Number(input.dataset.defaultQty);
    if (Number.isFinite(cap) && value > cap) return false;
    return true;
  }

  /** A short pick = entered qty strictly less than Planned Qty — see the
   * qty `input` handler, the only place `.overridden` is set for Pick
   * rows (unlike Putaway, an equal or larger entry never counts). Also
   * true, harmlessly, for a split row whose own slice is naturally less
   * than the line's original Planned Qty — the reason-code prompt this
   * triggers is a reasonable "why are you not doing the whole line as
   * one pick" nudge, not a bug; the picker can just note the split
   * reason or a real short reason, whichever applies. */
  function isPickShort(splitId) {
    const input = getPickQtyInput(splitId);
    return !!input && input.classList.contains("overridden");
  }

  function getPickReasonSelect(splitId) {
    return el.pickLinesBody.querySelector(
      '.reason-code-select[data-split-id="' + CSS.escape(String(splitId)) + '"]'
    );
  }

  /** Only required once a line is actually short (see isPickShort()) —
   * a full-quantity pick never needs a reason code. */
  function isPickReasonValid(splitId) {
    if (!isPickShort(splitId)) return true;
    const select = getPickReasonSelect(splitId);
    return !!(select && select.value);
  }

  /** A tote-destined row requires its group's TOTE textbox to hold a
   * *confirmed-valid*, non-duplicate tote id before it can complete
   * (2026-08-10, tightened from "just non-empty" once live validation
   * was added — see scheduleToteValidation() — then again to also
   * reject a duplicate of another tote group already on this screen,
   * see duplicateToteGroupKeys()) — an oLPN-destined row always
   * passes, since its container id is already known. A locked group
   * (already committed at least once) is always fine regardless of
   * `validated`/duplicate status, since it already proved itself via a
   * real commit. Takes a row (not just a splitId) since it needs
   * pickRowGroupKey(), which needs the row's own
   * isToteDestined/plannedSlotId/olpnId. */
  function isPickToteValid(row) {
    if (!row || !row.isToteDestined) return true;
    const key = pickRowGroupKey(row);
    const gs = getToteGroupState(key);
    if (gs.locked) return true;
    if (duplicateToteGroupKeys().has(key)) return false;
    return gs.validated === true;
  }

  /** Plain click toggles membership (2026-08-10, multi-select) — same
   * model as Putaway's selectLine(), see its docstring. */
  function selectPickRow(splitId) {
    const id = String(splitId);
    if (state.selectedPickSplitIds.has(id)) state.selectedPickSplitIds.delete(id);
    else state.selectedPickSplitIds.add(id);
    el.pickLinesBody.querySelectorAll("tr.pick-line-row").forEach((row) => {
      row.classList.toggle("selected", state.selectedPickSplitIds.has(row.dataset.splitId));
    });
    updatePickLineActionButtons();
  }

  function getSelectedPickRows() {
    return Array.from(state.selectedPickSplitIds)
      .map((id) => getPickRowBySplitId(id))
      .filter(Boolean);
  }

  function isPickRowReadyToComplete(row) {
    return (
      !!row &&
      !isPickRowDone(row.splitId) &&
      isPickQtyValid(row.splitId) &&
      isPickReasonValid(row.splitId) &&
      isPickToteValid(row)
    );
  }

  function outstandingPickRows() {
    return getPickRows().filter((r) => !isPickRowDone(r.splitId));
  }

  function updatePickLineActionButtons() {
    const selectedRows = getSelectedPickRows();
    setFullLineBtnLabel(selectedRows.length);
    el.fullLineBtn.disabled = !selectedRows.length || !selectedRows.every(isPickRowReadyToComplete);
    const outstanding = outstandingPickRows();
    el.allLinesBtn.disabled =
      !outstanding.length ||
      !outstanding.every((r) => isPickQtyValid(r.splitId) && isPickReasonValid(r.splitId) && isPickToteValid(r));
  }

  function setPickResultCell(splitId, message, kind, done) {
    const cell = el.pickLinesBody.querySelector(
      '.pick-result[data-split-id="' + CSS.escape(String(splitId)) + '"]'
    );
    if (!cell) return;
    cell.textContent = message;
    cell.className = "col-reason pick-result" + (kind ? " " + kind : "");
    cell.dataset.done = done ? "true" : "false";
    if (done) {
      const qtyInput = getPickQtyInput(splitId);
      if (qtyInput) qtyInput.disabled = true;
      const reasonSelect = getPickReasonSelect(splitId);
      if (reasonSelect) reasonSelect.disabled = true;
      const splitBtn = el.pickLinesBody.querySelector(
        '.pick-split-btn[data-split-id="' + CSS.escape(String(splitId)) + '"]'
      );
      if (splitBtn) splitBtn.remove();
    }
  }

  /**
   * Splits a not-yet-done tote-destined row into two sibling rows
   * within the *same* group (2026-08-10, per explicit instruction:
   * split first, then drag either half to a different tote — never one
   * gesture trying to do both). Defaults to a ceil/floor half split of
   * whatever quantity is currently in the box (not necessarily the
   * line's original Planned Qty, if it's already been split once);
   * both halves are independently editable afterward, not linked —
   * confirmed live this session that MAWM doesn't require split
   * quantities to sum to any particular total (each partial commit is
   * evaluated against whatever's actually remaining at commit time,
   * not a client-side total), so there's nothing to enforce here.
   * Re-splitting an already-split row works the same way: its one
   * entry in state.pickSplits is replaced by two new ones.
   */
  function splitPickRow(splitId) {
    const row = getPickRowBySplitId(splitId);
    if (!row || isPickRowDone(splitId)) return;
    const qtyInput = getPickQtyInput(splitId);
    const currentQty = qtyInput ? Number(qtyInput.value) : row.quantity;
    if (!Number.isFinite(currentQty) || currentQty <= 0) {
      setActionStatus("Enter a valid quantity before splitting this line.", "error");
      return;
    }
    const part1Qty = Math.ceil(currentQty / 2);
    const part2Qty = currentQty - part1Qty;
    const groupKey = pickRowGroupKey(row);
    const newId1 = nextSplitId(row.taskDetailId);
    const newId2 = nextSplitId(row.taskDetailId);

    const existing = state.pickSplits[row.taskDetailId] || [{ splitId: row.taskDetailId, quantity: row.quantity }];
    const remaining = existing.filter((s) => s.splitId !== splitId);
    remaining.push({ splitId: newId1, quantity: part1Qty }, { splitId: newId2, quantity: part2Qty });
    state.pickSplits[row.taskDetailId] = remaining;

    state.pickGroupOverride[newId1] = groupKey;
    state.pickGroupOverride[newId2] = groupKey;
    delete state.pickGroupOverride[splitId];

    renderPickGroups();
  }

  function pickResultKind(result) {
    return result.success ? "success" : "error";
  }

  function pickResultText(result) {
    if (result.success) {
      return "Completed " + result.completedQuantity + (result.taskStatus === "8000" ? " — task closed" : "");
    }
    return result.error || "Failed";
  }

  /**
   * Commits one row (2026-08-10) — only ever called for the 0-or-1-
   * selected case; 2+ routes to openSelectedPickLinesModal() instead
   * (same split as Putaway's completeLine()/openSelectedLinesModal()).
   * The backend commit always targets the
   * row's real, shared taskDetailId (a split row's own splitId is a
   * frontend-only concept, never sent) with just this row's own
   * quantity and, for a tote-destined row, this row's *group's* tote
   * id (see pickRowGroupKey()) — confirmed live this session that
   * MAWM correctly accepts repeated independent partial commits
   * against the same taskDetailId, each with its own quantity and
   * target container (see CLAUDE.md's incremental-split section), so
   * two split rows completing separately is not a new backend
   * capability, just a new frontend way of driving an already-real one.
   */
  async function completePickRow() {
    const selectedRows = getSelectedPickRows();
    const row = selectedRows.length === 1 ? selectedRows[0] : null;
    if (!row) return;
    if (isPickRowDone(row.splitId)) return;
    const qtyInput = getPickQtyInput(row.splitId);
    const quantity = qtyInput ? Number(qtyInput.value) : null;
    const short = isPickShort(row.splitId);
    const reasonSelect = short ? getPickReasonSelect(row.splitId) : null;
    const groupKey = pickRowGroupKey(row);
    const toteValue = row.isToteDestined ? getToteGroupState(groupKey).value.trim() : null;
    setBusy(true, "Completing line " + row.lineNumber + "…");
    try {
      const result = await api("complete_pick_line", {
        org: state.org,
        token: state.token,
        location: state.facility,
        taskId: row.groupTaskId,
        taskDetailId: row.taskDetailId,
        sourceContainerId: row.sourceContainerId,
        sourceContainerType: row.sourceContainerTypeId,
        olpnId: row.olpnId,
        transactionId: row.groupTaskTransactionId,
        quantity,
        exceptionMove: short,
        reasonCodeId: reasonSelect ? reasonSelect.value : null,
        targetContainerId: toteValue,
      });
      state.pickRowStatus[row.splitId] = result;
      setPickResultCell(row.splitId, pickResultText(result), pickResultKind(result), result.success);
      setActionStatus(
        result.success ? "Completed line " + row.lineNumber + "." : result.error || "Complete failed",
        result.success ? "success" : "error"
      );
      if (result.taskStatus) {
        state.groups.forEach((g) => {
          if (g.taskId === row.groupTaskId) {
            g.taskStatus = result.taskStatus;
            g.taskStatusLabel = result.taskStatusLabel;
          }
        });
        renderPickTaskMeta();
      }
      if (result.olpnId) updatePickOlpnStatus(result.olpnId, result.olpnStatus, result.olpnStatusLabel);
      if (result.toteId) updatePickToteStatus(groupKey, result.toteId, result.toteStatus, result.toteStatusLabel);
      updatePickLineActionButtons();
    } catch (e) {
      state.pickRowStatus[row.splitId] = { success: false, error: e.message || String(e) };
      setPickResultCell(row.splitId, e.message || String(e), "error", false);
      setActionStatus(e.message || String(e), "error");
    } finally {
      setBusy(false);
    }
  }

  let allPickLinesPending = [];

  /** Shared by "Complete All" and multi-select's "Complete Lines"
   * (2026-08-10) — `candidates` is either every outstanding row or
   * just the selected ones; everything else (validation, the modal
   * list, showing it) is identical either way. */
  function preparePickLinesModal(candidates) {
    allPickLinesPending = candidates;
    if (!allPickLinesPending.length) {
      setActionStatus("No outstanding lines to complete.", "");
      return;
    }
    if (!allPickLinesPending.every((r) => isPickQtyValid(r.splitId))) {
      setActionStatus(
        "Enter a Picked Qty (not exceeding the Required Qty) for every line before completing.",
        "error"
      );
      return;
    }
    if (!allPickLinesPending.every((r) => isPickReasonValid(r.splitId))) {
      setActionStatus("Choose a Reason Code for every short-picked line before completing.", "error");
      return;
    }
    if (!allPickLinesPending.every((r) => isPickToteValid(r))) {
      setActionStatus(
        "Enter and confirm a valid, unique Tote Id for every tote-destined line before completing.",
        "error"
      );
      return;
    }
    el.allLinesList.innerHTML = allPickLinesPending
      .map((r) => {
        const qtyInput = getPickQtyInput(r.splitId);
        const qty = qtyInput ? qtyInput.value : r.quantity;
        return `<li>Line ${escapeHtml(r.lineNumber)} — ${escapeHtml(r.itemId)}: ${escapeHtml(qty)} ${escapeHtml(r.uomTypeId)}</li>`;
      })
      .join("");
    allLinesModal.show();
  }

  function openAllPickLinesModal() {
    preparePickLinesModal(outstandingPickRows());
  }

  /** Multi-select's "Complete Lines" for Pick (2026-08-10) — see
   * Putaway's openSelectedLinesModal() for the equivalent; selection
   * clearing likewise happens for free via renderPickGroups()'s own
   * reset, reached through confirmAllPickLines()'s existing reload. */
  function openSelectedPickLinesModal() {
    preparePickLinesModal(getSelectedPickRows().filter((r) => !isPickRowDone(r.splitId)));
  }

  /**
   * Groups pending rows by task before submitting (2026-08-10) — a
   * multi-search batch (several Pick TaskIds/oLPNs typed at once) can
   * span more than one task, and complete_pick_task() commits all the
   * lines of *one* task per call, so this fans out one call per task
   * rather than assuming everything on screen belongs to a single one.
   *
   * Results come back from complete_pick_task() in the same order
   * `lineCompletions` was submitted (a plain sequential loop
   * server-side — see task_service.complete_pick_task()) — matched
   * back to each row by array index, not by taskDetailId, since two
   * split rows in the same batch legitimately share one taskDetailId
   * (see splitPickRow()) and the backend has no concept of splitId at
   * all; it only ever sees the real taskDetailId per call.
   */
  async function confirmAllPickLines() {
    allLinesModal.hide();
    const byTask = new Map();
    allPickLinesPending.forEach((r) => {
      const key = r.groupTaskId;
      if (!byTask.has(key)) byTask.set(key, []);
      byTask.get(key).push(r);
    });
    const total = allPickLinesPending.length;
    let succeeded = 0;
    const failures = [];
    setBusy(true, "Completing " + total + " line(s)…");
    for (const [taskId, rows] of byTask.entries()) {
      const lineCompletions = rows.map((r) => {
        const qtyInput = getPickQtyInput(r.splitId);
        const short = isPickShort(r.splitId);
        const reasonSelect = short ? getPickReasonSelect(r.splitId) : null;
        const groupKey = pickRowGroupKey(r);
        const toteValue = r.isToteDestined ? getToteGroupState(groupKey).value.trim() : null;
        return {
          taskDetailId: r.taskDetailId,
          sourceContainerId: r.sourceContainerId,
          sourceContainerType: r.sourceContainerTypeId,
          olpnId: r.olpnId,
          transactionId: r.groupTaskTransactionId,
          quantity: qtyInput ? Number(qtyInput.value) : null,
          exceptionMove: short,
          reasonCodeId: reasonSelect ? reasonSelect.value : null,
          targetContainerId: toteValue,
        };
      });
      try {
        const response = await api("complete_pick_task", {
          org: state.org,
          token: state.token,
          location: state.facility,
          taskId,
          lineCompletions,
        });
        (response.results || []).forEach((r, idx) => {
          const row = rows[idx];
          if (!row) return;
          state.pickRowStatus[row.splitId] = r;
          setPickResultCell(row.splitId, pickResultText(r), pickResultKind(r), r.success);
          if (r.olpnId) updatePickOlpnStatus(r.olpnId, r.olpnStatus, r.olpnStatusLabel);
          if (r.toteId) updatePickToteStatus(pickRowGroupKey(row), r.toteId, r.toteStatus, r.toteStatusLabel);
          if (r.success) succeeded++;
          else failures.push("Line " + row.taskDetailId + ": " + (r.error || "failed"));
        });
        const g = state.groups.find((gr) => gr.taskId === taskId);
        const last = (response.results || [])[response.results.length - 1];
        if (g && last && last.taskStatus) {
          g.taskStatus = last.taskStatus;
          g.taskStatusLabel = last.taskStatusLabel;
        }
      } catch (e) {
        rows.forEach((r) => failures.push("Line " + r.taskDetailId + ": " + (e.message || String(e))));
      }
    }
    renderPickTaskMeta();
    // Unlike Putaway, this doesn't end with a full reload/re-render
    // (would lose split/drag/tote-in-progress state on other rows —
    // see renderPickGroups()'s own docstring), so selection has to be
    // cleared explicitly here rather than getting it for free the way
    // Putaway's reloadCurrentSearch() does (2026-08-10, multi-select).
    state.selectedPickSplitIds.clear();
    el.pickLinesBody.querySelectorAll("tr.pick-line-row.selected").forEach((row) => {
      row.classList.remove("selected");
    });
    updatePickLineActionButtons();
    setBusy(false);
    setActionStatus(
      failures.length
        ? succeeded + " of " + total + " completed. Failures: " + failures.join("; ")
        : "Completed all " + total + " line(s).",
      failures.length ? "error" : "success"
    );
  }

  /**
   * Live tote validation (2026-08-10, per explicit instruction —
   * "we should probably add a tote validation routine to ensure that
   * someone enters a valid tote number"). Debounced 1s per group (own
   * timer per groupKey, same pattern as validateLocation()'s
   * per-input WeakMap, just keyed by string instead of element) so
   * typing doesn't fire a real API call per keystroke. Two real checks
   * happen server-side — see task_service.validate_pick_tote_id():
   * does this id already exist as a real iLPN (only reusable once
   * Consumed or "above"), or if not, is it at least a valid barcode
   * format. Complete Line/Complete All are gated on
   * `validated === true` (see isPickToteValid()), not just a
   * non-empty value.
   */
  const toteValidateTimers = {};

  function scheduleToteValidation(groupKey) {
    clearTimeout(toteValidateTimers[groupKey]);
    const gs = getToteGroupState(groupKey);
    gs.validated = null;
    gs.validationMessage = "";
    if (!gs.value.trim()) {
      gs.validating = false;
      // A value change here can also resolve/create a duplicate for
      // *another* group (2026-08-10 — see duplicateToteGroupKeys()), so
      // every group's display needs a refresh, not just this one's.
      refreshToteValidationDisplays();
      updatePickLineActionButtons();
      return;
    }
    gs.validating = true;
    refreshToteValidationDisplays();
    updatePickLineActionButtons();
    toteValidateTimers[groupKey] = setTimeout(() => runToteValidation(groupKey), 1000);
  }

  async function runToteValidation(groupKey) {
    const gs = getToteGroupState(groupKey);
    const value = gs.value.trim();
    if (!value) {
      gs.validating = false;
      refreshToteValidationDisplays();
      return;
    }
    try {
      const result = await api("validate_pick_tote", {
        org: state.org,
        token: state.token,
        location: state.facility,
        toteId: value,
      });
      // A later keystroke may have already superseded this response —
      // don't let a stale check confirm/reject the wrong value.
      if (getToteGroupState(groupKey).value.trim() !== value) return;
      gs.validated = !!result.valid;
      gs.validationMessage = result.valid ? "" : result.error || "Invalid tote";
    } catch (e) {
      if (getToteGroupState(groupKey).value.trim() !== value) return;
      gs.validated = false;
      gs.validationMessage = e.message || String(e);
    }
    gs.validating = false;
    refreshToteValidationDisplays();
    updatePickLineActionButtons();
  }

  function renderToteValidationState(groupKey) {
    const header = el.pickLinesBody.querySelector(
      'tr.pick-tote-header[data-group-key="' + CSS.escape(String(groupKey)) + '"]'
    );
    if (!header) return;
    const gs = getToteGroupState(groupKey);
    const isDuplicate = !gs.locked && duplicateToteGroupKeys().has(groupKey);
    const input = header.querySelector(".tote-id-input");
    if (input) {
      const isInvalid = !gs.locked && (!gs.value.trim() || gs.validated !== true || isDuplicate);
      input.classList.toggle("invalid", isInvalid);
      input.classList.toggle("checking", gs.validating);
    }
    let msg = header.querySelector(".tote-validation-msg");
    if (!msg) {
      msg = document.createElement("span");
      msg.className = "tote-validation-msg";
      header.querySelector("td").appendChild(msg);
    }
    msg.textContent = gs.locked
      ? ""
      : isDuplicate
        ? "Duplicate tote id — already used on this screen"
        : gs.validating
          ? "Checking…"
          : gs.validationMessage || "";
  }

  /**
   * Barcode/QR scanning on the main Task Id/iLPN/Location search box
   * (2026-08-10, corrected same-day — originally built against the Pick
   * tote textbox, per explicit correction: "the barcode button and 500
   * character textbox is for the initial prompt screen, not the tote
   * textbox"). Same overall UX shape as the inspection app's own
   * openBarcodeScanner()/handleBarcodeScan()/applyConfirmedBarcode()
   * (scan → editable confirm panel → "Use this ID"/"Scan again"), which
   * only decodes 1D formats via Quagga2. QR/2D support is added the
   * same way `driver_pickup` combines engines — jsQR polls the same
   * video element Quagga2 attaches to, via its own canvas/getImageData
   * loop, racing Quagga2 rather than running as a sequential fallback
   * (see the barcode-qr-scanning pattern doc in mawm_api_library).
   * Whichever engine decodes first wins; both stop once a value is
   * accepted into the confirm panel.
   *
   * The confirm panel's own input is intentionally editable and
   * unbounded up to 500 chars (matching el.taskIdInput's own maxlength)
   * rather than auto-applied on first decode: a QR code may encode a
   * longer string (e.g. ~20 locations/LPNs) that only *contains* the
   * real id, so the picker trims it down here before it's ever written
   * into the search box.
   */
  let quaggaTaskScanActive = false;
  let quaggaTaskDetectedHandler = null;
  let qrTaskScanInterval = null;
  let lastTaskScanValue = "";
  let lastTaskScanAt = 0;

  const taskScanModalEl = document.getElementById("taskScanModal");
  const taskScanModal = window.bootstrap ? new window.bootstrap.Modal(taskScanModalEl) : null;

  function resetTaskScanConfirmPanel() {
    if (el.taskScanConfirmPanel) el.taskScanConfirmPanel.hidden = true;
    if (el.taskScanResultInput) el.taskScanResultInput.value = "";
    if (el.taskScanConfirmStatus) el.taskScanConfirmStatus.textContent = "";
  }

  function updateTaskScanConfirmStatus(value) {
    if (!el.taskScanConfirmStatus) return;
    const trimmed = String(value || "").trim();
    if (!trimmed) {
      el.taskScanConfirmStatus.textContent = "";
      el.taskScanConfirmStatus.className = "task-scan-confirm-status mb-2";
      return;
    }
    el.taskScanConfirmStatus.textContent = "Will fill the search field with this value";
    el.taskScanConfirmStatus.className = "task-scan-confirm-status mb-2 text-success";
  }

  function showTaskScanConfirmPanel(raw) {
    if (!el.taskScanConfirmPanel || !el.taskScanResultInput) return;
    el.taskScanResultInput.value = raw;
    el.taskScanConfirmPanel.hidden = false;
    updateTaskScanConfirmStatus(raw);
    el.taskScanResultInput.focus();
    el.taskScanResultInput.select();
  }

  function stopTaskScanner() {
    if (quaggaTaskScanActive && typeof Quagga !== "undefined") {
      try {
        if (quaggaTaskDetectedHandler) {
          Quagga.offDetected(quaggaTaskDetectedHandler);
          quaggaTaskDetectedHandler = null;
        }
        Quagga.stop();
      } catch (err) {
        console.warn("[TASK SCAN] Quagga stop failed:", err);
      }
      quaggaTaskScanActive = false;
    }
    if (qrTaskScanInterval) {
      clearInterval(qrTaskScanInterval);
      qrTaskScanInterval = null;
    }
    if (el.taskScannerRegion) el.taskScannerRegion.innerHTML = "";
  }

  function handleTaskScanDetected(decodedText) {
    const raw = String(decodedText || "").trim();
    if (!raw) return;
    const now = Date.now();
    if (raw === lastTaskScanValue && now - lastTaskScanAt < 2500) return;
    lastTaskScanValue = raw;
    lastTaskScanAt = now;
    stopTaskScanner();
    showTaskScanConfirmPanel(raw);
  }

  /** jsQR polling loop (2026-08-10) — runs alongside Quagga2 against the
   * same <video> element Quagga2 attaches to inside el.taskScannerRegion
   * (Quagga2 owns the camera stream; this just reads frames from its
   * video via its own canvas/getImageData, same approach as
   * driver_pickup's startQRCodeScanning()). Retries every 500ms until
   * Quagga2's video element actually exists, then polls every 300ms
   * while the scanner is active. */
  function startTaskQrScanning() {
    if (!window.jsQR) return;
    const video = el.taskScannerRegion ? el.taskScannerRegion.querySelector("video") : null;
    if (!video) {
      setTimeout(() => {
        if (quaggaTaskScanActive) startTaskQrScanning();
      }, 500);
      return;
    }
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d");
    qrTaskScanInterval = setInterval(() => {
      if (!quaggaTaskScanActive || video.readyState !== video.HAVE_ENOUGH_DATA) return;
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const imageData = context.getImageData(0, 0, canvas.width, canvas.height);
      const code = window.jsQR(imageData.data, imageData.width, imageData.height);
      if (code && code.data) handleTaskScanDetected(code.data);
    }, 300);
  }

  function applyConfirmedTaskScan() {
    const raw = el.taskScanResultInput ? el.taskScanResultInput.value.trim() : "";
    if (!raw) return;
    if (taskScanModal) taskScanModal.hide();
    el.taskIdInput.value = raw;
    // Real 'input' event, not a direct assignment (2026-08-10) — the
    // existing el.taskIdInput "input" listener (updateLoadButton) already
    // does exactly what a scanned value needs (classification + enabling
    // Confirm); dispatching through it keeps this one code path instead
    // of duplicating that logic.
    el.taskIdInput.dispatchEvent(new Event("input", { bubbles: true }));
    el.taskIdInput.focus();
  }

  function openTaskScanner() {
    if (typeof Quagga === "undefined") {
      setStatus("Barcode scanning is not available in this browser", "error");
      return;
    }
    lastTaskScanValue = "";
    lastTaskScanAt = 0;
    resetTaskScanConfirmPanel();
    if (taskScanModal) taskScanModal.show();
    stopTaskScanner();
    if (!el.taskScannerRegion) return;

    quaggaTaskDetectedHandler = (result) => {
      const code = result && result.codeResult && result.codeResult.code;
      if (code) handleTaskScanDetected(code);
    };

    Quagga.init(
      {
        inputStream: {
          name: "Live",
          type: "LiveStream",
          target: el.taskScannerRegion,
          constraints: {
            width: { min: 640 },
            height: { min: 480 },
            facingMode: "environment",
          },
        },
        locator: { patchSize: "medium", halfSample: true },
        numOfWorkers: Math.min(2, navigator.hardwareConcurrency || 2),
        decoder: { readers: ["code_128_reader", "code_39_reader"] },
        locate: true,
      },
      (err) => {
        if (err) {
          console.warn("[TASK SCAN] Quagga init failed:", err);
          stopTaskScanner();
          if (taskScanModal) taskScanModal.hide();
          setStatus("Could not start barcode camera", "error");
          return;
        }
        quaggaTaskScanActive = true;
        Quagga.onDetected(quaggaTaskDetectedHandler);
        Quagga.start();
        startTaskQrScanning();
      }
    );
  }

  if (el.taskScanBtn) el.taskScanBtn.addEventListener("click", openTaskScanner);
  if (el.taskScanUseBtn) el.taskScanUseBtn.addEventListener("click", applyConfirmedTaskScan);
  if (el.taskScanRetryBtn) el.taskScanRetryBtn.addEventListener("click", openTaskScanner);
  if (el.taskScanResultInput) {
    el.taskScanResultInput.addEventListener("input", () => {
      updateTaskScanConfirmStatus(el.taskScanResultInput.value);
    });
    el.taskScanResultInput.addEventListener("keypress", (e) => {
      if (e.key === "Enter") applyConfirmedTaskScan();
    });
  }
  if (taskScanModalEl) {
    taskScanModalEl.addEventListener("hidden.bs.modal", () => {
      stopTaskScanner();
      resetTaskScanConfirmPanel();
    });
  }

  // `error` doesn't bubble on <img>, so this listens on the capture
  // phase instead of the usual delegated-on-bubble pattern the rest of
  // this file uses (2026-08-10, item thumbnails — see
  // itemImageCellHtml()'s docstring).
  el.pickLinesBody.addEventListener(
    "error",
    (e) => {
      const img = e.target;
      if (!img.classList || !img.classList.contains("item-image-thumb")) return;
      const wrap = img.closest(".item-image-wrap");
      if (!wrap) return;
      wrap.classList.remove("item-image-wrap--inline");
      wrap.classList.add("item-image-wrap--empty");
      wrap.textContent = "—";
    },
    true
  );

  el.pickLinesBody.addEventListener("click", (e) => {
    const splitBtn = e.target.closest(".pick-split-btn");
    if (splitBtn) {
      splitPickRow(splitBtn.dataset.splitId);
      return;
    }
    const row = e.target.closest("tr.pick-line-row");
    if (!row) return;
    selectPickRow(row.dataset.splitId);
  });
  el.pickLinesBody.addEventListener("input", (e) => {
    const qtyInput = e.target.closest(".pick-qty-input");
    if (qtyInput) {
      const value = Number(qtyInput.value);
      const planned = Number(qtyInput.dataset.defaultQty);
      const short = Number.isFinite(value) && Number.isFinite(planned) && value < planned;
      // Over the row's own required qty (2026-08-10) — the up-arrow can
      // still increment past it (nothing here blocks typing/spinning),
      // but this flags it red and updatePickLineActionButtons() (via
      // isPickQtyValid()) keeps Complete Line/Complete All disabled
      // until it's brought back down.
      const exceeds = Number.isFinite(value) && Number.isFinite(planned) && value > planned;
      qtyInput.classList.toggle("overridden", short);
      qtyInput.classList.toggle("exceeds", exceeds);
      qtyInput.title = exceeds ? "Exceeds required qty of " + planned : "";
      toggleReasonSelect(qtyInput, short);
      updatePickLineActionButtons();
      return;
    }
    const toteInput = e.target.closest(".tote-id-input");
    if (toteInput) {
      getToteGroupState(toteInput.dataset.groupKey).value = toteInput.value;
      scheduleToteValidation(toteInput.dataset.groupKey);
    }
  });
  el.pickLinesBody.addEventListener("change", (e) => {
    const select = e.target.closest(".reason-code-select");
    if (!select) return;
    select.classList.toggle("invalid", !select.value);
    updatePickLineActionButtons();
  });

  /**
   * Drag-and-drop between tote groups (2026-08-10, per explicit
   * instruction — "drag whatever lines down into the new tote
   * section"). Native HTML5 DnD, no library: only rows marked
   * `draggable="true"` in pickLineRowHtml() (not-yet-done,
   * tote-destined rows — an oLPN row's destination is fixed by the
   * task data, nothing to drag it to) can be dragged; only a
   * `tr.pick-tote-header` (including an empty "+ Add Tote" group's own
   * header, since it renders even with zero rows — see
   * renderPickGroups()) accepts a drop. Moving a row is just
   * `state.pickGroupOverride[splitId] = <new group>` followed by a
   * re-render — the same mechanism splitPickRow() already uses to keep
   * a freshly split pair in their shared starting group.
   */
  el.pickLinesBody.addEventListener("dragstart", (e) => {
    const row = e.target.closest('tr.pick-line-row[draggable="true"]');
    if (!row) return;
    e.dataTransfer.setData("text/plain", row.dataset.splitId);
    e.dataTransfer.effectAllowed = "move";
    row.classList.add("dragging");
  });
  el.pickLinesBody.addEventListener("dragend", (e) => {
    const row = e.target.closest("tr.pick-line-row");
    if (row) row.classList.remove("dragging");
    el.pickLinesBody
      .querySelectorAll(".drop-target-hover")
      .forEach((node) => node.classList.remove("drop-target-hover"));
  });
  el.pickLinesBody.addEventListener("dragover", (e) => {
    const header = e.target.closest("tr.pick-tote-header");
    if (!header) return;
    e.preventDefault();
    header.classList.add("drop-target-hover");
  });
  el.pickLinesBody.addEventListener("dragleave", (e) => {
    const header = e.target.closest("tr.pick-tote-header");
    if (!header) return;
    header.classList.remove("drop-target-hover");
  });
  el.pickLinesBody.addEventListener("drop", (e) => {
    const header = e.target.closest("tr.pick-tote-header");
    if (!header) return;
    e.preventDefault();
    header.classList.remove("drop-target-hover");
    const splitId = e.dataTransfer.getData("text/plain");
    const groupKey = header.dataset.groupKey;
    if (!splitId || !groupKey) return;
    if (isPickRowDone(splitId)) return;
    state.pickGroupOverride[splitId] = groupKey;
    renderPickGroups();
  });

  el.addToteBtn.addEventListener("click", () => {
    state.extraToteGroups.push("extra:" + Date.now() + ":" + state.extraToteGroups.length);
    renderPickGroups();
  });

  const allLinesModal = window.bootstrap
    ? new window.bootstrap.Modal(document.getElementById("allLinesModal"))
    : null;
  const warningModalEl = document.getElementById("warningModal");
  const warningModal = window.bootstrap ? new window.bootstrap.Modal(warningModalEl) : null;
  const reasonCodeModalEl = document.getElementById("reasonCodeModal");
  const reasonCodeModal = window.bootstrap ? new window.bootstrap.Modal(reasonCodeModalEl) : null;

  el.fullLineBtn.addEventListener("click", () => {
    if (state.lastSearchMode === "cycle_count") {
      completeCycleCountLine();
    } else if (state.lastSearchMode === "pick") {
      if (state.selectedPickSplitIds.size >= 2) openSelectedPickLinesModal();
      else completePickRow();
    } else {
      if (state.selectedTaskDetailIds.size >= 2) openSelectedLinesModal();
      else completeLine();
    }
  });
  el.allLinesBtn.addEventListener("click", () => {
    if (state.lastSearchMode === "cycle_count") openAllCycleCountLinesModal();
    else if (state.lastSearchMode === "pick") openAllPickLinesModal();
    else openAllLinesModal();
  });
  el.allLinesConfirmBtn.addEventListener("click", () => {
    if (state.lastSearchMode === "cycle_count") confirmAllCycleCountLines();
    else if (state.lastSearchMode === "pick") confirmAllPickLines();
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

  // Item image hover preview (2026-08-10) — bound once, on the shared
  // container all three tables (Putaway/Cycle Count/Pick) live inside,
  // rather than once per table; bindItemImagePreview() itself no-ops
  // on a second call against the same container (see its own
  // dataset.itemImagePreviewBound guard), so this is safe even if
  // called again later.
  if (window.bindItemImagePreview) window.bindItemImagePreview(el.resultsScreen);

  // URL boot: Organization/org auto-authenticates (Task deep-link is applied
  // inside authenticate() once auth completes, see applyUrlTaskBoot()).
  if (urlParams.org) {
    el.org.value = urlParams.org.toUpperCase();
    authenticate(urlParams.org);
  } else {
    el.org.focus();
  }
})();
