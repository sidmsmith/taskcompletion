#!/usr/bin/env python3
"""Task Check In — shared service for the web API.

Mirrors receivingworkbench's rw_service.py layering: this module owns
normalization and business rules (remaining-quantity math, full/partial/
all-line orchestration); mawm_client.py owns raw HTTP calls.

Field mapping in _normalize_task_lines()/load_task() is now CONFIRMED
against a real live SS-DEMO response (TaskId IBPWIBPT0929, a Putaway
task) — see mawm_client.py's module docstring for the confirmed
search_task()/search_task_transactions() calls this relies on.
_first() still tries a couple of fallback key names per field (kept
defensive in case a different task type shapes a field differently —
not yet confirmed one way or the other for Picking/Cycle Count/
Replenishment), but the first-listed key in each call is the one
observed live, not a guess.

complete_putaway_line() implements full-line Putaway completion via the
confirmed-by-user-capture Path C sequence documented in
mawm_putaway_api_call_set_with_warning_handling.md (fetch next move,
then commit — see mawm_client.fetch_putaway_move()/commit_putaway_move()).
When the user edits the destination in the grid, the same Path C
sequence is still used ("directed putaway is still used") but the
fetched move's ToLocationId/ReasonCodeId are overridden before
committing — see mawm_substitute_location_to_user_directed_putaway.md
and _complete_putaway_line_system_directed()'s docstring. The earlier,
task-independent "user directed putaway" approach
(_complete_putaway_line_user_directed(), calling
mawm_client.move_container_user_directed()) is commented out below,
kept for reference in case it's needed again.
complete_line()'s underlying mawm_client.complete_task() call remains a
separate, still-unconfirmed placeholder for non-Putaway task types.

resolve_search() is the single entry point behind the "Task Id or iLPN"
search box (2026-08-08): try the value as a Task Id, then as a
container — resolving to its open (not Completed/Canceled) Putaway
task if one exists — and finally, if no task exists at all, a
synthetic single-line "no open task" response built from the
container's real current location + on-hand inventory. Completing that
synthetic line goes through complete_container_putaway(), which — as of
a second capture the same day — uses the CONFIRMED-live DMM Mobile
Facade "User Directed Putaway" flow (workflow_init()/workflow_execute()/
apply_warning_overrides()), not the earlier task-independent core API
call (mawm_client.move_container_user_directed(), now commented out a
second time after its own warning override was confirmed not to work).
"""

from __future__ import annotations

import re
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

from mawm_client import (
    ADJUSTMENT_REASON_CODES,
    CYCLE_COUNT_QUANTITY_MISMATCH_TEXT,
    CYCLE_COUNT_TRANSACTION_ID,
    DEFAULT_ADJUSTMENT_REASON_CODE,
    DEFAULT_TRANSACTION_BY_TASK_TYPE,
    ILPN_CONSUMED_STATUS,
    LOCATION_ADJUSTMENT_STALE_RECORD_CODE,
    PUTAWAY_WORKFLOW_SCRIPT_NAME,
    TASK_TYPE_LABELS,
    USER_DIRECTED_TRANSACTION_ID,
    accept_quantity,
    adjust_ilpn_inventory,
    adjust_location_inventory,
    apply_warning_overrides,
    commit_putaway_move,
    complete_task,
    end_count,
    extract_message,
    extract_warning,
    fetch_putaway_move,
    ilpn_status_description,
    initiate_count,
    persist_count_details,
    refresh_ilpn_read_timestamp,
    resolve_location,
    search_all_storage_locations,
    search_container_inventory,
    search_ilpn_current_location,
    search_ilpn_statuses,
    search_inventory_count_results,
    search_items,
    search_location_count_info,
    search_location_inventory,
    search_putaway_reason_codes,
    search_task,
    search_task_id_for_container,
    search_task_transactions,
    task_status_description,
    validate_item_and_get_item_details,
    validate_storage_location,
    workflow_execute,
    workflow_init,
)


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0))
    except Exception:
        return Decimal("0")


def _num(value):
    d = _dec(value)
    if d == d.to_integral_value():
        return int(d)
    return float(d)


def _first(row: dict, *keys):
    for key in keys:
        val = row.get(key)
        if val is not None and str(val).strip():
            return val
    return None


def _package_conversion_factor(item: dict, code: str) -> tuple[Decimal, str]:
    """Ported from receivingworkbench's rw_service.py
    `_package_conversion_factor()` (2026-08-08, per explicit
    instruction to reference that app's already-confirmed UOM logic —
    same shape of problem: a bare quantity + raw UOM *code* (e.g.
    "LPN") is not directly displayable, since MAWM quantities are
    always in the item's base unit, not the code's own unit).

    `code` is the code a quantity is expressed against — TaskDetail's
    `UomTypeId` for a task line, or the item's own `DisplayUomId` for a
    no-task container line (there's no per-line "shipped as X" concept
    there). Looks up the item's `ItemPackage[]` for the *Standard*
    entry whose `StandardQuantityUomId` matches that code; `Quantity`
    on that entry is the real conversion factor (base units per one of
    that code), `UomIdDisplay`/`UomId` its human label. Confirmed live,
    2026-08-08: item `3000223`'s "LPN" entry has `Quantity: 50,
    UomId: "Case"` — its 50-base-unit Substitute Location test line
    earlier this session was in fact exactly 1 Case, not "50 LPN" (which
    would misread as 50 containers, since this app already uses "LPN"
    to mean a container id elsewhere in the UI).

    Falls back to (1, code) when the item has no matching standard
    package entry — same as receivingworkbench, so an unresolvable UOM
    degrades to showing the raw base quantity with its raw code instead
    of silently guessing.
    """
    code = str(code or "").strip()
    for pkg in (item or {}).get("ItemPackage") or []:
        if pkg.get("Standard") is not True:
            continue
        if str(pkg.get("StandardQuantityUomId") or "").strip() != code:
            continue
        factor = _dec(pkg.get("Quantity"))
        if factor > 0:
            label = str(pkg.get("UomIdDisplay") or pkg.get("UomId") or code)
            return factor, label
    return Decimal("1"), code


def _task_type_label(task_type: str) -> str:
    key = str(task_type or "").strip().upper()
    return TASK_TYPE_LABELS.get(key, key or "")


def default_transaction_id(task_type: str) -> str:
    key = str(task_type or "").strip().upper()
    return DEFAULT_TRANSACTION_BY_TASK_TYPE.get(key, "")


def _ilpn_display_fields(ilpn: Optional[dict]) -> Dict[str, Any]:
    """Shared by both the task-mode and no-task line builders (2026-08-08,
    seventh session, per explicit instruction — "always display LPN
    status" — this used to be no-task-only). `ilpn` is one row from
    search_ilpn_current_location()/search_ilpn_statuses(), or None if
    the LPN wasn't found/looked up at all.

    Returns `ilpnStatus`/`ilpnStatusLabel` (blank if `ilpn` is None —
    e.g. a task-mode line whose own container id didn't resolve to a
    real iLPN) and `displayLocationId`: `CurrentLocationId` normally,
    or — confirmed live against two real consumed LPNs — a
    `PreviousLocationId`-based fallback marked with a trailing `*` when
    `Status == ILPN_CONSUMED_STATUS` and `CurrentLocationId` is blank
    (a consumed LPN's own CurrentLocationId comes back null, so without
    this there'd be nothing to show for "where did it go").
    `displayLocationId` is only meaningful for the no-task/container
    case, where "Current Location" is genuinely the LPN's own tracked
    location — task-mode's own "Current Location" column is a
    different concept (TaskDetail's SourceLocationId, where the task
    expects to pick from) and doesn't use this.
    """
    if not ilpn:
        return {"ilpnStatus": "", "ilpnStatusLabel": "", "displayLocationId": ""}
    status = str(ilpn.get("Status") or "")
    current_location = str(ilpn.get("CurrentLocationId") or "")
    previous_location = str(ilpn.get("PreviousLocationId") or "")
    display_location = current_location
    if not display_location and status == ILPN_CONSUMED_STATUS and previous_location:
        display_location = f"{previous_location}*"
    return {
        "ilpnStatus": status,
        "ilpnStatusLabel": ilpn_status_description(status),
        "displayLocationId": display_location,
    }


def _normalize_task_lines(
    raw_task: dict,
    items: Optional[Dict[str, dict]] = None,
    ilpn_statuses: Optional[Dict[str, dict]] = None,
) -> List[dict]:
    """Field mapping confirmed live (SS-DEMO, TaskId IBPWIBPT0929) — see
    module docstring. `items` is an optional {ItemId: item-master row}
    map (from mawm_client.search_items) used to fill in Description,
    which TaskDetail itself doesn't carry. `ilpn_statuses` (2026-08-08,
    seventh session) is an optional {IlpnId: iLPN row} map (from
    mawm_client.search_ilpn_statuses()) for each line's own LPN status
    badge — see _ilpn_display_fields().
    """
    items = items or {}
    ilpn_statuses = ilpn_statuses or {}
    raw_lines = raw_task.get("TaskDetail") or []
    if not isinstance(raw_lines, list):
        raw_lines = []

    lines: List[dict] = []
    for idx, line in enumerate(raw_lines, start=1):
        planned_base = _dec(_first(line, "Quantity", "PlannedQuantity") or 0)
        completed_base = _dec(_first(line, "CompletedQuantity") or 0)
        remaining_base = planned_base - completed_base
        item_id = str(_first(line, "ItemId") or "")
        item = items.get(item_id) or {}
        uom_code = str(_first(line, "UomTypeId", "UomId", "QuantityUomId") or "")
        # 2026-08-08 (see _package_conversion_factor()'s docstring):
        # Quantity/CompletedQuantity are base-unit; converted to the
        # item's real pack quantity for display. uomFactor rides along
        # so the frontend can convert an edited Completed Qty (entered
        # in this same display unit) back to base units before it's
        # ever sent anywhere — adjust_ilpn_quantities()/MAWM itself
        # only ever deal in base units.
        factor, uom_label = _package_conversion_factor(item, uom_code)
        lpn_id = str(
            _first(line, "TargetContainerId", "SourceContainerId", "WorkingContainerId") or ""
        )
        line_dict = {
            "lineNumber": idx,
            "taskDetailId": str(
                _first(line, "TaskDetailId", "PK", "Unique_Identifier") or idx
            ),
            "itemId": item_id,
            "description": str(item.get("Description") or ""),
            "fromLocationId": str(_first(line, "SourceLocationId", "FromLocationId") or ""),
            "toLocationId": str(_first(line, "TargetLocationId", "ToLocationId") or ""),
            "lpnId": lpn_id,
            "uomId": uom_label,
            "uomFactor": _num(factor),
            "plannedQuantity": _num(planned_base / factor),
            "completedQuantity": _num(completed_base / factor),
            "remainingQuantity": _num(
                (remaining_base if remaining_base > 0 else Decimal("0")) / factor
            ),
            "mixedItems": None,  # only ever set on a no_task MIXED container line, see resolve_search()
        }
        ilpn_fields = _ilpn_display_fields(ilpn_statuses.get(lpn_id))
        line_dict["ilpnStatus"] = ilpn_fields["ilpnStatus"]
        line_dict["ilpnStatusLabel"] = ilpn_fields["ilpnStatusLabel"]
        # Task-mode's own "Current Location" stays TaskDetail-driven —
        # see _ilpn_display_fields()'s docstring — displayLocationId is
        # deliberately not applied to fromLocationId here.
        lines.append(line_dict)
    return lines


def _build_task_response(raw_task: dict, task_id: str, token: str, org: str, dest: str) -> Dict[str, Any]:
    """Shared normalization for a real Task — used by resolve_search()'s
    direct-TaskId and container->TaskId branches alike.
    """
    # The Task object has no dedicated `TaskType` field — confirmed live;
    # TransactionTypeId (e.g. "Putaway") is the closest real field, with
    # LaborActivityId as a fallback for any task type where it differs.
    task_type = str(
        _first(raw_task, "TransactionTypeId", "LaborActivityId") or ""
    ).strip().upper()

    raw_lines = raw_task.get("TaskDetail") or []
    item_ids = [str(l.get("ItemId") or "") for l in raw_lines if l.get("ItemId")]
    items = search_items(item_ids, token, org, location=dest) if item_ids else {}
    # 2026-08-08, seventh session, per explicit instruction ("always
    # display LPN status"): batched so a multi-line task costs one
    # extra call total, not one per line — see search_ilpn_statuses().
    lpn_ids = [
        str(_first(l, "TargetContainerId", "SourceContainerId", "WorkingContainerId") or "")
        for l in raw_lines
    ]
    lpn_ids = [i for i in lpn_ids if i]
    ilpn_statuses = search_ilpn_statuses(lpn_ids, token, org, location=dest) if lpn_ids else {}
    lines = _normalize_task_lines(raw_task, items, ilpn_statuses)

    return {
        "success": True,
        "mode": "task",
        "taskId": task_id,
        "facility": dest,
        "taskType": task_type,
        "taskTypeLabel": _task_type_label(task_type),
        "taskStatus": _first(raw_task, "Status"),
        "taskStatusLabel": task_status_description(_first(raw_task, "Status")),
        # Confirmed live: the Task record carries its own TransactionId
        # (e.g. "Putaway") directly — preferred over the static
        # DEFAULT_TRANSACTION_BY_TASK_TYPE guess when it's actually
        # present in the org's transaction list (see
        # preload_task_transactions()'s defaultTransactionId).
        "taskTransactionId": str(_first(raw_task, "TransactionId") or ""),
        "lineCount": len(lines),
        "lines": lines,
    }


def resolve_search(
    token: str,
    org: str,
    search_value: str,
    location: str = None,
) -> Dict[str, Any]:
    """Single entry point behind the "Task Id or iLPN" search box.

    1. Try `search_value` as a Task Id directly.
    2. Else try it as a container — resolve its open (not Completed/
       Canceled) Putaway task, if one exists, and load that.
    3. Else, if `search_value` is at least a real iLPN, return a
       synthetic single "no open task" line: Current Location = the
       iLPN's real CurrentLocationId (may be blank), To Location blank
       (the operator must enter one — see complete_container_putaway()),
       Item/Description/Qty pulled from the container's actual on-hand
       inventory. `mode: "no_task"` tells the frontend to render/complete
       this differently than a real task.
    4. Else, not found.
    """
    search_value = (search_value or "").strip()
    if not search_value:
        return {"success": False, "error": "Task Id or iLPN required"}

    dest = resolve_location(org, location)

    raw_task = search_task(search_value, token, org, location=dest)
    if raw_task:
        return _build_task_response(raw_task, search_value, token, org, dest)

    found_task_id = search_task_id_for_container(search_value, token, org, location=dest)
    if found_task_id:
        raw_task = search_task(found_task_id, token, org, location=dest)
        if raw_task:
            return _build_task_response(raw_task, found_task_id, token, org, dest)

    ilpn = search_ilpn_current_location(search_value, token, org, location=dest)
    if ilpn is None:
        return {"success": False, "error": f"No task or iLPN found for '{search_value}'"}

    ilpn_fields = _ilpn_display_fields(ilpn)
    inv_rows = search_container_inventory(search_value, token, org, location=dest)
    item_ids = [str(r.get("ItemId") or "") for r in inv_rows if r.get("ItemId")]
    items = search_items(item_ids, token, org, location=dest) if item_ids else {}

    mixed = len(inv_rows) > 1
    mixed_items = None
    uom_label = ""
    factor = Decimal("1")
    if mixed:
        # 2026-08-08, per explicit instruction: "MIXED" now shows in the
        # Item column (blank Description), not the other way around.
        # mixedItems carries each real line's own detail so the frontend
        # can expand this summary row into per-item editable rows (see
        # adjust_ilpn_quantities()) instead of just displaying a total.
        # No single per-line UOM code exists for a no-task container
        # (unlike a task line's UomTypeId) — each item's own
        # DisplayUomId backs its own conversion instead (see
        # _package_conversion_factor()'s docstring); the summary row's
        # aggregate stays a bare base-unit total (uomId left blank —
        # mixed items may use different units, nothing coherent to
        # label it with).
        item_id = "MIXED"
        description = ""
        qty = sum((_dec(r.get("OnHand")) for r in inv_rows), Decimal("0"))
        mixed_items = []
        for r in inv_rows:
            row_item_id = str(r.get("ItemId") or "")
            row_item = items.get(row_item_id) or {}
            item_factor, item_uom_label = _package_conversion_factor(
                row_item, str(row_item.get("DisplayUomId") or "")
            )
            mixed_items.append(
                {
                    "itemId": row_item_id,
                    "description": str(row_item.get("Description") or ""),
                    "quantity": _num(_dec(r.get("OnHand")) / item_factor),
                    "uomId": item_uom_label,
                    "uomFactor": _num(item_factor),
                }
            )
    elif inv_rows:
        item_id = str(inv_rows[0].get("ItemId") or "")
        item = items.get(item_id) or {}
        description = str(item.get("Description") or "")
        factor, uom_label = _package_conversion_factor(item, str(item.get("DisplayUomId") or ""))
        qty = _dec(inv_rows[0].get("OnHand")) / factor
    else:
        item_id = ""
        description = ""
        qty = Decimal("0")

    line = {
        "lineNumber": 1,
        "taskDetailId": f"container:{search_value}",
        "itemId": item_id,
        "description": description,
        "fromLocationId": ilpn_fields["displayLocationId"],
        "toLocationId": "",
        "lpnId": search_value,
        "uomId": uom_label,
        "uomFactor": _num(factor),
        "plannedQuantity": _num(qty),
        "completedQuantity": 0,
        "remainingQuantity": _num(qty),
        "mixedItems": mixed_items,
        "ilpnStatus": ilpn_fields["ilpnStatus"],
        "ilpnStatusLabel": ilpn_fields["ilpnStatusLabel"],
    }
    return {
        "success": True,
        "mode": "no_task",
        "taskId": "",
        "facility": dest,
        "taskType": "PUTAWAY",
        "taskTypeLabel": "Putaway",
        "taskStatus": "",
        "taskStatusLabel": "No Open Task",
        "taskTransactionId": "",
        "lineCount": 1,
        "lines": [line],
        "containerId": search_value,
    }


_MULTI_SEARCH_SPLIT_RE = re.compile(r"[;,\s]+")


def resolve_search_multi(
    token: str, org: str, raw_input: str, location: str = None
) -> Dict[str, Any]:
    """Wraps resolve_search() to accept more than one Task Id/iLPN at
    once, delimited by ";", "," or whitespace (2026-08-08, per explicit
    instruction — cycle count in particular may need to work across
    several LPNs/tasks at once, unlike picking which typically stays
    scoped to a single task).

    Each token is resolved independently via resolve_search() — one may
    have an open task, another may not, another may not exist at all;
    none of that stops the others from resolving (failures collect into
    `notFound` instead of aborting the whole search). Every line in
    every resolved group is denormalized with that group's own
    mode/taskId/containerId/status/etc, so the frontend can treat the
    combined line list as flat and self-describing instead of having to
    cross-reference back into a `groups` array for every row action
    (selection, completion, location validation). `taskDetailId` was
    already confirmed globally unique across groups (real GUIDs for
    tasks, `container:{id}` for no-task containers), so no new keying
    scheme was needed for that.
    """
    tokens: List[str] = []
    seen = set()
    for raw_token in _MULTI_SEARCH_SPLIT_RE.split(raw_input or ""):
        token_value = raw_token.strip().upper()
        if token_value and token_value not in seen:
            seen.add(token_value)
            tokens.append(token_value)

    if not tokens:
        return {"success": False, "error": "Task Id or iLPN required"}

    dest = resolve_location(org, location)
    groups: List[Dict[str, Any]] = []
    not_found: List[str] = []
    for token_value in tokens:
        result = resolve_search(token, org, token_value, location=dest)
        if not result.get("success"):
            not_found.append(token_value)
            continue
        group_key = f"{result['mode']}:{result.get('taskId') or result.get('containerId')}"
        for line in result.get("lines") or []:
            line["groupKey"] = group_key
            line["groupMode"] = result["mode"]
            line["groupTaskId"] = result.get("taskId", "")
            line["groupContainerId"] = result.get("containerId", "")
            line["groupTaskType"] = result.get("taskType", "")
            line["groupTaskTypeLabel"] = result.get("taskTypeLabel", "")
            line["groupTaskStatus"] = result.get("taskStatus", "")
            line["groupTaskStatusLabel"] = result.get("taskStatusLabel", "")
            line["groupTaskTransactionId"] = result.get("taskTransactionId", "")
        result["groupKey"] = group_key
        groups.append(result)

    if not groups:
        return {
            "success": False,
            "error": f"No task or iLPN found for: {', '.join(not_found)}",
        }

    line_count = sum(len(g.get("lines") or []) for g in groups)
    return {
        "success": True,
        "facility": dest,
        "groups": groups,
        "lineCount": line_count,
        "notFound": not_found,
    }


def preload_task_transactions(
    token: str,
    org: str,
    task_type: str,
    location: str = None,
    task_transaction_id: str = None,
) -> Dict[str, Any]:
    dest = resolve_location(org, location)
    rows = search_task_transactions(task_type, token, org, location=dest)
    entries = []
    for row in rows:
        transaction_id = str(_first(row, "TransactionId") or "").strip()
        if not transaction_id:
            continue
        entries.append(
            {
                "transactionId": transaction_id,
                "strategyId": str(_first(row, "StrategyId") or "").strip(),
                "description": str(_first(row, "Description") or "").strip(),
            }
        )
    known = {e["transactionId"] for e in entries}
    # Prefer the loaded task's own TransactionId (confirmed live on the
    # Task record itself) when the org's transaction list actually
    # contains it; fall back to the static per-task-type guess otherwise.
    default = ""
    if task_transaction_id and task_transaction_id in known:
        default = task_transaction_id
    elif default_transaction_id(task_type) in known:
        default = default_transaction_id(task_type)
    return {
        "success": True,
        "count": len(entries),
        "entries": entries,
        "defaultTransactionId": default,
    }


def preload_putaway_reason_codes(
    token: str, org: str, location: str = None
) -> Dict[str, Any]:
    """Reason codes for the Substitute Location flow (see
    complete_putaway_line()'s docstring) — the frontend shows these in a
    required modal before completing a line whose destination was edited.
    `value` (ReasonCodeId) is what's sent back to MAWM; `key`
    (Description) is the human label shown in the picker.
    """
    dest = resolve_location(org, location)
    rows = search_putaway_reason_codes(token, org, location=dest)
    entries = []
    for row in rows:
        value = str(_first(row, "ReasonCodeId") or "").strip()
        key = str(_first(row, "Description") or "").strip() or value
        if not value:
            continue
        entries.append({"key": key, "value": value})
    entries.sort(key=lambda e: e["key"].lower())
    return {"success": True, "count": len(entries), "entries": entries}


def _line_state(
    token: str, org: str, task_id: str, task_detail_id: str, location: str = None
) -> Dict[str, Any]:
    """Re-fetch the Task fresh and resolve one line's current state.

    Re-fetching (rather than trusting quantities the frontend already has)
    avoids acting on stale data, matching receivingworkbench's
    _line_state_for_receipt() convention.
    """
    dest = resolve_location(org, location)
    raw_task = search_task(task_id, token, org, location=dest)
    if not raw_task:
        return {"success": False, "error": f"Task {task_id} not found"}

    lines = _normalize_task_lines(raw_task)
    line = next((l for l in lines if l["taskDetailId"] == str(task_detail_id)), None)
    if not line:
        return {"success": False, "error": f"Task line {task_detail_id} not found on {task_id}"}

    return {"success": True, "dest": dest, "line": line}


def complete_line(
    token: str,
    org: str,
    task_id: str,
    task_detail_id: str,
    mode: str,
    transaction_id: str,
    strategy_id: str = None,
    quantity: Optional[float] = None,
    location: str = None,
) -> Dict[str, Any]:
    """Complete one Task line — mode "full" or "partial".

    "full" books the entire remaining quantity. "partial" books `quantity`
    after validating it does not exceed what's remaining. `transaction_id`
    is required — the frontend's Transaction ID picker disables Full/
    Partial/All Lines until one's chosen, so a blank here means the
    frontend didn't enforce that; fail rather than silently defaulting.
    """
    if not transaction_id:
        return {"success": False, "error": "Transaction ID is required"}

    state = _line_state(token, org, task_id, task_detail_id, location=location)
    if not state.get("success"):
        return state

    line = state["line"]
    remaining = _dec(line["remainingQuantity"])
    if remaining <= 0:
        return {"success": False, "error": "No remaining quantity on this line"}

    if mode == "full":
        qty = remaining
    elif mode == "partial":
        if quantity is None:
            return {"success": False, "error": "quantity is required for a partial completion"}
        qty = _dec(quantity)
        if qty <= 0:
            return {"success": False, "error": "quantity must be greater than 0"}
        if qty > remaining:
            return {
                "success": False,
                "error": f"quantity exceeds remaining ({_num(remaining)} {line['uomId']})",
            }
    else:
        return {"success": False, "error": f"Unknown mode: {mode}"}

    result = complete_task(
        task_id,
        task_detail_id,
        _num(qty),
        transaction_id,
        token,
        org,
        location=state["dest"],
        strategy_id=strategy_id or None,
    )
    ok = result.get("success", True) if isinstance(result, dict) else True
    return {
        "success": bool(ok),
        "taskDetailId": task_detail_id,
        "quantity": _num(qty),
        "uomId": line["uomId"],
        "mawmResponse": result,
        "error": None if ok else (result.get("message") or result.get("error") or "Complete failed"),
    }


def preload_adjustment_reason_codes() -> Dict[str, Any]:
    """Static list (see mawm_client.ADJUSTMENT_REASON_CODES's docstring
    for why this isn't a live search) for the frontend's reason-code
    dropdown next to an edited Completed Qty box.
    """
    entries = [{"key": c["key"], "value": c["value"]} for c in ADJUSTMENT_REASON_CODES]
    return {
        "success": True,
        "count": len(entries),
        "entries": entries,
        "defaultReasonCode": DEFAULT_ADJUSTMENT_REASON_CODE,
    }


def adjust_ilpn_quantities(
    container_id: str,
    adjustments: List[Dict[str, Any]],
    token: str,
    org: str,
    location: str = None,
) -> Dict[str, Any]:
    """Modify iLPN: correct `container_id`'s actual on-hand quantity for
    each item in `adjustments` (`[{"itemId", "desiredQty", "reasonCode"}]`)
    to the given value, per
    mawm_modify_ilpn_query_and_adjustment.md (2026-08-08). Always
    re-queries live inventory first — never trusts a caller's belief
    about current on-hand — so `ScannedQuantity` (a *relative*
    adjustment on the wire, `New OnHand = Current + (ScannedQuantity -
    ExpectedOnHandQuantity)`) collapses to simply the desired new total,
    since `OriginalOnHandQuantity == ExpectedOnHandQuantity == the
    freshly-read current OnHand` here.

    Items already at their desired quantity are silently skipped (not
    sent at all — the document says not to include unrelated lines).
    Returns `{"success": True, "adjusted": False}` if nothing needed to
    change.

    A quantity of exactly 0 is sent as a normal line with
    `ScannedQuantity: 0` (not through the document's `DeletedInventory`
    path) — per explicit instruction, based on live testing (by the
    document's author, outside this app) that found this removes the
    line the same way.

    **Null `InventoryReadTimestamp` is NOT only an add-line thing** —
    confirmed live, 2026-08-08: a real, pre-existing (not newly added)
    container's inventory line came back with a null timestamp on a
    fresh query. So this always checks for it and calls
    `refresh_ilpn_read_timestamp()` + re-queries once when any targeted
    line needs it, rather than skipping that step.

    The `endIlpn` adjustment is asynchronous — this waits briefly, then
    re-queries once to verify the new on-hand actually matches what was
    requested, returning `success: False` with the mismatched item ids
    if it doesn't (the caller should treat that as "try again in a
    moment," not necessarily a hard failure).

    **An LPN still allocated to an open task cannot be adjusted this
    way at all** (2026-08-08, sixth session — per explicit domain-
    expertise instruction, not yet independently captured via HAR/API
    test the way most findings in this app are). The document's own
    testing only ever exercised this against a *non-allocated* LPN via
    Postman; that turns out to matter. `complete_putaway_line()` now
    calls this function *after* completing putaway rather than before,
    specifically because of this — by the time it runs, the task has
    released the LPN (assuming the destination keeps LPN-level
    inventory at all; see that function's docstring for the Reserve/
    Storage vs. Pick location distinction). Don't call this against an
    LPN you know is still on an open task; it's expected to fail.
    """
    dest = resolve_location(org, location)
    rows = search_container_inventory(container_id, token, org, location=dest)
    by_item = {str(r.get("ItemId") or ""): r for r in rows}

    to_send = []
    needs_refresh = False
    for adj in adjustments:
        item_id = str(adj.get("itemId") or "").strip()
        if not item_id:
            continue
        desired = _dec(adj.get("desiredQty"))
        row = by_item.get(item_id)
        current = _dec(row.get("OnHand")) if row else Decimal("0")
        if desired == current:
            continue
        if not row or row.get("InventoryReadTimestamp") is None:
            needs_refresh = True
        to_send.append(
            {
                "itemId": item_id,
                "desired": desired,
                "current": current,
                "allocated": _dec((row or {}).get("Allocated") or 0),
                "reasonCode": str(adj.get("reasonCode") or "").strip()
                or DEFAULT_ADJUSTMENT_REASON_CODE,
            }
        )

    if not to_send:
        return {"success": True, "adjusted": False}

    if needs_refresh:
        refresh_ilpn_read_timestamp(container_id, token, org, location=dest)
        rows = search_container_inventory(container_id, token, org, location=dest)
        by_item = {str(r.get("ItemId") or ""): r for r in rows}

    inventory_lines = []
    for item in to_send:
        row = by_item.get(item["itemId"])
        timestamp = row.get("InventoryReadTimestamp") if row else None
        if timestamp is None:
            return {
                "success": False,
                "adjusted": False,
                "error": (
                    f"No InventoryReadTimestamp available for {item['itemId']} "
                    f"on {container_id} even after refresh"
                ),
            }
        inventory_lines.append(
            {
                "OriginalOnHandQuantity": _num(item["current"]),
                "ExpectedOnHandQuantity": _num(item["current"]),
                "AllocatedQuantity": _num(item["allocated"]),
                "ScannedQuantity": _num(item["desired"]),
                "ReasonCode": item["reasonCode"],
                "InventoryReadTimestamp": timestamp,
                "ItemAttributeDTO": {"ItemBarcode": item["itemId"], "Item": item["itemId"]},
            }
        )

    adjust_ilpn_inventory(container_id, inventory_lines, token, org, location=dest)

    time.sleep(1.5)
    verify_rows = search_container_inventory(container_id, token, org, location=dest)
    verify_by_item = {str(r.get("ItemId") or ""): r for r in verify_rows}
    mismatches = []
    for item in to_send:
        new_row = verify_by_item.get(item["itemId"])
        new_onhand = _dec(new_row.get("OnHand")) if new_row else Decimal("0")
        if new_onhand != item["desired"]:
            mismatches.append(item["itemId"])

    return {
        "success": not mismatches,
        "adjusted": True,
        "mismatches": mismatches,
        "error": (
            "Adjustment not yet verified for: " + ", ".join(mismatches) + " — endIlpn processes "
            "asynchronously, this may just need a moment longer."
        )
        if mismatches
        else None,
    }


def adjust_location_quantities(
    location_id: str,
    adjustments: List[Dict[str, Any]],
    token: str,
    org: str,
    location: str = None,
) -> Dict[str, Any]:
    """Adjust Location: correct `location_id`'s own on-hand quantity for
    each item in `adjustments` (`[{"itemId", "desiredQty", "reasonCode"}]`)
    to the given value, per `mawm_adjust_location_api.md` (2026-08-08,
    sixth session). This is the fallback for a putaway destination that
    consumes the LPN into the location's own inventory record rather
    than keeping LPN-level inventory — see `ILPN_CONSUMED_STATUS`'s
    comment in mawm_client.py for the confirmed live signal
    `complete_putaway_line()` uses to choose this over
    `adjust_ilpn_quantities()`.

    Unlike Modify iLPN's `ScannedQuantity`, this endpoint's `Quantity`
    is a genuine signed delta with no absolute-value shortcut — always
    re-queries live location inventory first (never trusts a caller's
    belief about current on-hand) and computes
    `delta = desired - current` per item.

    Items already at their desired quantity are silently skipped (not
    sent at all, same convention as adjust_ilpn_quantities()). Returns
    `{"success": True, "adjusted": False}` if nothing needed to change.

    **Multiple inventory records for the same item at one location are
    NOT disambiguated** — the document says to use distinguishing
    attributes (batch, product status, ConsumptionPriorityDate, etc.)
    from "the selected record" when this happens, but this app has no
    concept of the operator picking a specific record, only an item +
    desired quantity. This takes the first matching row and carries its
    attributes; if a location genuinely holds more than one inventory
    detail for the same item (different batches/attributes), the
    adjustment could land against the wrong one. Not yet hit in
    testing; revisit if it comes up.

    **`DCI::313` (stale record) handling is best-effort, UNCONFIRMED**
    — see `mawm_client.adjust_location_inventory()`'s docstring: no
    captured error response body exists for this endpoint's stale-record
    case, so this checks the response for
    `LOCATION_ADJUSTMENT_STALE_RECORD_CODE` (`extract_message()`'s text,
    or the raw body as a last resort) and, if found, re-queries location
    inventory once and retries the whole payload once with fresh
    `InventoryReadOnHand`/attributes — same one-retry shape as Modify
    iLPN's null-timestamp refresh, not an open-ended loop.

    No async note in the document the way Modify iLPN's endIlpn has one,
    but "allow for the adjustment to complete before verifying" reads
    the same way — waits briefly, then re-queries once to verify,
    returning `success: False` with the mismatched item ids if it
    doesn't match yet (treat as "try again shortly," not a hard
    failure, same as adjust_ilpn_quantities()).
    """
    dest = resolve_location(org, location)

    def _by_item(rows: List[dict]) -> Dict[str, dict]:
        by_item: Dict[str, dict] = {}
        for r in rows:
            item_id = str(r.get("ItemId") or "")
            if item_id and item_id not in by_item:
                by_item[item_id] = r
        return by_item

    rows = search_location_inventory(location_id, token, org, location=dest)
    by_item = _by_item(rows)

    to_send = []
    for adj in adjustments:
        item_id = str(adj.get("itemId") or "").strip()
        if not item_id:
            continue
        desired = _dec(adj.get("desiredQty"))
        row = by_item.get(item_id)
        current = _dec(row.get("OnHand")) if row else Decimal("0")
        if desired == current:
            continue
        if not row:
            return {
                "success": False,
                "adjusted": False,
                "error": f"{item_id} not found at {location_id}",
            }
        to_send.append(
            {
                "itemId": item_id,
                "desired": desired,
                "current": current,
                "row": row,
                "reasonCode": str(adj.get("reasonCode") or "").strip()
                or DEFAULT_ADJUSTMENT_REASON_CODE,
            }
        )

    if not to_send:
        return {"success": True, "adjusted": False}

    def _build_payload(items: List[dict]) -> List[dict]:
        payload = []
        for item in items:
            row = item["row"]
            delta = item["desired"] - item["current"]
            payload.append(
                {
                    "SourceContainerId": location_id,
                    "SourceContainerType": "LOCATION",
                    "SourceLocationId": location_id,
                    "EventSource": "INVENTORY_MANAGEMENT",
                    "TransactionType": "INVENTORY_ADJUSTMENT",
                    "InventoryTypeId": row.get("InventoryTypeId"),
                    "CountryOfOrigin": row.get("CountryOfOrigin"),
                    "ProductStatusId": row.get("ProductStatusId"),
                    "BatchNumber": row.get("BatchNumber"),
                    "ReasonCode": item["reasonCode"],
                    "ReferenceText": None,
                    "SecondaryReferenceText": None,
                    "InventoryAttribute1": row.get("InventoryAttribute1"),
                    "InventoryAttribute2": row.get("InventoryAttribute2"),
                    "InventoryAttribute3": row.get("InventoryAttribute3"),
                    "InventoryAttribute4": row.get("InventoryAttribute4"),
                    "InventoryAttribute5": row.get("InventoryAttribute5"),
                    "ItemId": item["itemId"],
                    "AddItemUOM": None,
                    "RemoveItemUOM": None,
                    "AddItemRemainder": _num(delta) if delta > 0 else None,
                    "RemoveItemRemainder": _num(-delta) if delta < 0 else None,
                    "caseTrackingLpn": "",
                    "ConsumptionPriorityDate": row.get("ConsumptionPriorityDate"),
                    "PixEventName": "INVENTORY_ADJUSTMENT",
                    "PixTransactionType": "ADJUST_UI",
                    "ExpirationDate": row.get("ExpirationDate"),
                    "ManufacturedDate": row.get("ManufacturedDate"),
                    "PackUomQuantity": row.get("PackUomQuantity"),
                    "PackUomTypeId": row.get("PackUomTypeId"),
                    "Quantity": _num(delta),
                    "InventoryReadOnHand": _num(item["current"]),
                }
            )
        return payload

    adjust_resp = adjust_location_inventory(_build_payload(to_send), token, org, location=dest)

    stale = LOCATION_ADJUSTMENT_STALE_RECORD_CODE in (
        extract_message(adjust_resp) + " " + str(adjust_resp)
    )
    if stale:
        rows = search_location_inventory(location_id, token, org, location=dest)
        by_item = _by_item(rows)
        for item in to_send:
            fresh_row = by_item.get(item["itemId"])
            if fresh_row:
                item["row"] = fresh_row
                item["current"] = _dec(fresh_row.get("OnHand"))
        adjust_resp = adjust_location_inventory(_build_payload(to_send), token, org, location=dest)

    time.sleep(1.5)
    verify_rows = search_location_inventory(location_id, token, org, location=dest)
    verify_by_item = _by_item(verify_rows)
    mismatches = []
    for item in to_send:
        new_row = verify_by_item.get(item["itemId"])
        new_onhand = _dec(new_row.get("OnHand")) if new_row else Decimal("0")
        if new_onhand != item["desired"]:
            mismatches.append(item["itemId"])

    return {
        "success": not mismatches,
        "adjusted": True,
        "mismatches": mismatches,
        "error": (
            "Adjustment not yet verified for: " + ", ".join(mismatches) + " — this may just need "
            "a moment longer, or the request itself may have failed: "
            + extract_message(adjust_resp)
        )
        if mismatches
        else None,
    }


def complete_putaway_line(
    token: str,
    org: str,
    task_id: str,
    task_detail_id: str,
    transaction_id: str,
    location: str = None,
    warning_overrides: Optional[Dict[str, str]] = None,
    to_location_id: Optional[str] = None,
    reason_code_id: Optional[str] = None,
    item_id: Optional[str] = None,
    lpn_id: Optional[str] = None,
    desired_qty: Optional[float] = None,
    adjustment_reason_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Complete one Putaway task line in full.

    **2026-08-08, sequence REVERSED (sixth session), per explicit
    domain-expertise instruction, not yet independently captured**: an
    LPN still allocated to an open task cannot be adjusted via Modify
    iLPN at all — that only works once the task releases it, which for
    Putaway means *after* it's been put away. So a `desired_qty`
    different from what's planned no longer runs the adjustment first
    (that was this same day's *second* attempt at this problem, after
    the first — a direct partial `CompletedQuantity` — was confirmed
    broken; see `mawm_client.commit_putaway_move()`'s docstring for that
    one). The sequence is now: complete the full, unmodified putaway
    below FIRST (`desired_qty` plays no part in that call at all), and
    only *after* it succeeds, correct the quantity via
    `adjust_ilpn_quantities()` — see that function's docstring for why
    this needed no other changes: it already re-queries live inventory
    itself, so the same call works whether the LPN is fresh or was just
    put away.

    **Which of two APIs to use is now decided automatically, per
    explicit domain-expertise instruction, confirmed live 2026-08-08**:
    `LocationTypeId='STORAGE'` (the only destination type this app's
    own `validate_storage_location()` allows) does NOT by itself mean
    the destination keeps LPN-level inventory — that's a genuinely
    independent property (`Location.InventoryReservationTypeId`, "LPN"
    vs "LOCATION"). Some Storage locations consume the LPN into their
    own location-level inventory record just like a Pick location
    would (confirmed live: `A1AC0114`/`C1CS0110` are both
    `LocationTypeId='STORAGE'` but `InventoryReservationTypeId=
    'LOCATION'`). Rather than pre-checking the destination location,
    this checks the *LPN itself* after putaway — the real, observed
    outcome rather than an inferred one: `search_ilpn_current_location()`
    now also fetches `Status`; `Status == ILPN_CONSUMED_STATUS` ("9000",
    MAWM's own `ilpn_dc_inventory_status` domain, confirmed live against
    both a consumed and a still-live LPN) means the LPN was consumed
    into the destination's own inventory, so
    `adjust_location_quantities()` runs against the destination
    location instead of `adjust_ilpn_quantities()` against the LPN.

    If the putaway itself fails or hits a warning, this returns exactly
    that (same as before) — the adjustment step never runs, and never
    runs partially. If putaway succeeds but the *adjustment* afterward
    fails, the response still reports `"success": True` (the putaway,
    the primary action, really did happen and can't be silently hidden)
    with a separate `"adjustmentSuccess": False` /
    `"adjustmentError"` pair so the frontend can surface that distinctly
    — "completed, but the quantity correction failed" is a materially
    different situation from "nothing happened," and conflating them
    would be actively misleading.

    `to_location_id` is only sent by the frontend when the user actually
    edited the line's destination away from what was loaded (see
    public/app.js's `.to-location-input`) — its mere presence here is
    trusted as "the user wants to substitute the destination." When
    present, a `reason_code_id` is required — the frontend enforces
    this via a required reason-code modal (see
    preload_putaway_reason_codes()) before ever sending toLocationId,
    so a substitution without one here means the frontend didn't
    enforce that; fail rather than silently proceeding. (This is the
    Putaway-domain Substitute Location reason code, a different concept
    and a different code list from `adjustment_reason_code`'s
    inventory-adjustment domain — see mawm_client.ADJUSTMENT_REASON_CODES.)

    Per mawm_substitute_location_to_user_directed_putaway.md, "directed
    putaway is still used" for a substituted destination — this always
    calls `_complete_putaway_line_system_directed()` (the same fetch+
    commit Path C sequence as the default case), just with the fetched
    move's ToLocationId/ReasonCodeId overridden before committing when
    a substitution was requested.
    """
    if to_location_id and to_location_id.strip():
        if not reason_code_id:
            return {
                "success": False,
                "error": "A reason code is required when changing the destination location",
            }
    else:
        to_location_id = None

    putaway_result = _complete_putaway_line_system_directed(
        token,
        org,
        task_id,
        task_detail_id,
        transaction_id,
        location=location,
        warning_overrides=warning_overrides,
        to_location_id=to_location_id,
        reason_code_id=reason_code_id,
    )
    if not putaway_result.get("success"):
        return putaway_result

    if desired_qty is not None:
        if not item_id or not lpn_id:
            putaway_result["adjustmentSuccess"] = False
            putaway_result["adjustmentError"] = (
                "itemId and lpnId are required to adjust the completed quantity"
            )
            return putaway_result

        dest = resolve_location(org, location)
        ilpn = search_ilpn_current_location(lpn_id, token, org, location=dest)
        # No ILPN record at all means `lpn_id` was never a real container
        # (e.g. a loose/location-direct putaway where the task's "LPN" is
        # just the destination location id) — same as a consumed LPN,
        # there is no LPN-level inventory to adjust, so this must route
        # to the location. Confirmed live, 2026-08-08 (IBPWIBPT0052):
        # treating "not found" as "not consumed" here sent this down the
        # LPN branch, which crashed trying to refresh a read-timestamp on
        # a container that doesn't exist.
        route_to_location = ilpn is None or str(ilpn.get("Status") or "") == ILPN_CONSUMED_STATUS

        adjustment = [
            {
                "itemId": item_id,
                "desiredQty": desired_qty,
                "reasonCode": adjustment_reason_code,
            }
        ]
        if route_to_location:
            final_location_id = str(putaway_result.get("toLocationId") or "").strip()
            if not final_location_id:
                putaway_result["adjustmentSuccess"] = False
                putaway_result["adjustmentError"] = (
                    "Could not determine the putaway destination to adjust"
                )
                return putaway_result
            putaway_result["adjustmentTarget"] = "location"
            try:
                adjust_result = adjust_location_quantities(
                    final_location_id, adjustment, token, org, location=dest
                )
            except Exception as exc:  # noqa: BLE001 - putaway already committed; must not crash the response
                adjust_result = {"success": False, "error": str(exc)}
        else:
            putaway_result["adjustmentTarget"] = "lpn"
            try:
                adjust_result = adjust_ilpn_quantities(
                    lpn_id, adjustment, token, org, location=dest
                )
            except Exception as exc:  # noqa: BLE001 - putaway already committed; must not crash the response
                adjust_result = {"success": False, "error": str(exc)}

        putaway_result["adjustmentSuccess"] = bool(adjust_result.get("success"))
        if not adjust_result.get("success"):
            putaway_result["adjustmentError"] = (
                adjust_result.get("error") or "Inventory adjustment failed"
            )

    return putaway_result


def _complete_putaway_line_system_directed(
    token: str,
    org: str,
    task_id: str,
    task_detail_id: str,
    transaction_id: str,
    location: str = None,
    warning_overrides: Optional[Dict[str, str]] = None,
    to_location_id: Optional[str] = None,
    reason_code_id: Optional[str] = None,
) -> Dict[str, Any]:
    """The confirmed-by-capture Path C sequence
    (mawm_putaway_api_call_set_with_warning_handling.md): fetch the next
    move for the task, then commit it in full.

    fetchNextPutawayMoveAndStartLaborActivity returns whatever move is
    "next" for the TaskId, not one scoped to a specific TaskDetailId — so
    this checks the fetched move's CurrentTaskDetailId against the line
    the user actually selected and refuses to commit on a mismatch,
    rather than silently completing the wrong line.

    Always books the fetched move's full Quantity (CompletedQuantity ==
    Quantity, per the document's own worked commit payload example) —
    **2026-08-08, reverted back to this**: a same-day earlier attempt at
    a caller-supplied partial `quantity` here was confirmed live NOT to
    work (MAWM's core `commitAndFetchNextMove` rejects
    `CompletedQuantity < Quantity` outright — see
    mawm_client.commit_putaway_move()'s docstring). A different
    quantity is now expressed by correcting the LPN's actual on-hand
    *before* ever calling this, via `adjust_ilpn_quantities()` (see
    `complete_putaway_line()`'s docstring) — so by the time this
    function fetches the move, whatever Quantity comes back should
    already reflect the correction, and this function itself needed no
    quantity-related parameter at all anymore.

    Substitute Location (`to_location_id` + `reason_code_id` given):
    per mawm_substitute_location_to_user_directed_putaway.md, "directed
    putaway is still used" for this case, not a separate endpoint — the
    document's own captured move payload (Call 1's "current move") is
    field-identical to this same InventoryMove object, including
    ToLocationId. UNCONFIRMED extrapolation: the document only captures
    the DMM Mobile Facade's stateful equivalent of this substitution
    (SubstituteLocation -> EnterReasonCodeForSubstituteAction ->
    AcceptLocationForUserDirectedPutaway, each carrying a workflowVO
    across calls); it never captures the core commitAndFetchNextMove
    payload with an overridden ToLocationId/ReasonCodeId directly. This
    overrides InventoryMove.ToLocationId with `to_location_id` and adds
    InventoryMove.ReasonCodeId = `reason_code_id` (PascalCase to match
    every other InventoryMove field, vs. the DMM flow's camelCase
    `reasonCodeId` input name) before committing. If MAWM rejects this
    shape, the DMM stateful flow is the fallback to implement instead.

    If either call surfaces an overrideable WARNING (see
    mawm_client.extract_warning()) and the matching code isn't already
    present in `warning_overrides`, this returns a `warning: True`
    response instead of proceeding — the frontend shows a Cancel/Confirm
    modal and, on Confirm, retries this same function with that code
    added to `warning_overrides`.
    """
    if not transaction_id:
        return {"success": False, "error": "Transaction ID is required"}

    dest = resolve_location(org, location)
    fetch_resp = fetch_putaway_move(task_id, transaction_id, token, org, location=dest)

    warning = extract_warning(fetch_resp)
    if warning and warning["code"] not in (warning_overrides or {}):
        return {
            "success": False,
            "warning": True,
            "messageId": warning["code"],
            "messageText": warning["text"],
            "stage": "fetch",
        }

    data = fetch_resp.get("data") if isinstance(fetch_resp, dict) else None
    inventory_move = dict((data or {}).get("InventoryMove") or {})
    sub_type = (data or {}).get("SubType") if isinstance(data, dict) else None
    if not inventory_move or not sub_type:
        # Bug fixed 2026-08-08: this used to always show the same generic
        # text, even when the fetch actually came back with a real hard
        # ERROR (not a WARNING — extract_warning() above correctly lets
        # those through) carrying a real business message — confirmed
        # live via FWTSK::019, "Task <id> cannot be assigned to user
        # <user>." (a task-assignment conflict, not a code bug), which
        # was being silently replaced by this generic fallback. Now
        # prefers the real message when the response actually has one.
        real_message = extract_message(fetch_resp)
        fallback = "No putaway move returned for this task"
        return {
            "success": False,
            "error": real_message if real_message and real_message != "Complete failed" else fallback,
        }

    fetched_detail_id = str(inventory_move.get("CurrentTaskDetailId") or "")
    if fetched_detail_id and fetched_detail_id != str(task_detail_id):
        return {
            "success": False,
            "error": (
                f"Task Management returned a different line ({fetched_detail_id}) "
                f"than the one selected ({task_detail_id}) — not completing it."
            ),
        }

    if to_location_id:
        inventory_move["ToLocationId"] = to_location_id
        inventory_move["ReasonCodeId"] = reason_code_id

    # Full completion: CompletedQuantity == Quantity, per the document's
    # own worked commit payload example.
    inventory_move["CompletedQuantity"] = inventory_move.get("Quantity")

    commit_resp = commit_putaway_move(
        sub_type, inventory_move, token, org, location=dest, warning_overrides=warning_overrides
    )
    commit_warning = extract_warning(commit_resp)
    if commit_warning and commit_warning["code"] not in (warning_overrides or {}):
        return {
            "success": False,
            "warning": True,
            "messageId": commit_warning["code"],
            "messageText": commit_warning["text"],
            "stage": "commit",
        }

    ok = commit_resp.get("success", True) if isinstance(commit_resp, dict) else True
    return {
        "success": bool(ok),
        "taskDetailId": task_detail_id,
        "quantity": _num(inventory_move.get("CompletedQuantity") or 0),
        "toLocationId": inventory_move.get("ToLocationId"),
        "mawmResponse": commit_resp,
        "error": None if ok else extract_message(commit_resp),
    }


# SUPERSEDED 2026-08-08 — move_container_user_directed()'s own warning
# override was confirmed live not to work (see mawm_client.py). Replaced
# by the DMM Mobile Facade implementation below. Kept, commented out,
# alongside its transport function.
#
# def complete_container_putaway(
#     token: str,
#     org: str,
#     container_id: str,
#     to_location_id: str,
#     location: str = None,
#     warning_overrides: Optional[Dict[str, str]] = None,
# ) -> Dict[str, Any]:
#     dest = resolve_location(org, location)
#     rows = search_container_inventory(container_id, token, org, location=dest)
#     if not rows:
#         return {"success": False, "error": f"No on-hand inventory found for {container_id}"}
#     if len(rows) > 1:
#         return {
#             "success": False,
#             "error": f"{container_id} holds more than one item — not supported here.",
#         }
#     item_id = str(rows[0].get("ItemId") or "")
#     quantity = _dec(rows[0].get("OnHand"))
#     if not item_id or quantity <= 0:
#         return {"success": False, "error": f"No on-hand inventory found for {container_id}"}
#     move_resp = move_container_user_directed(
#         container_id, to_location_id, item_id, _num(quantity), token, org,
#         location=dest, warning_overrides=warning_overrides,
#     )
#     warning = extract_warning(move_resp)
#     if warning and warning["code"] not in (warning_overrides or {}):
#         return {
#             "success": False, "warning": True,
#             "messageId": warning["code"], "messageText": warning["text"], "stage": "move",
#         }
#     ok = move_resp.get("success", True) if isinstance(move_resp, dict) else True
#     return {
#         "success": bool(ok),
#         "taskDetailId": f"container:{container_id}",
#         "quantity": _num(quantity),
#         "toLocationId": to_location_id,
#         "mawmResponse": move_resp,
#         "error": None if ok else extract_message(move_resp),
#     }


def complete_container_putaway(
    token: str,
    org: str,
    container_id: str,
    to_location_id: str,
    location: str = None,
    warning_overrides: Optional[Dict[str, str]] = None,
    item_adjustments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Complete putaway for a container with no open Task at all (see
    resolve_search()'s `mode: "no_task"` branch), via the CONFIRMED-live
    DMM Mobile Facade "User Directed Putaway" flow — captured 2026-08-08
    end to end, including a real warning and its override (see
    mawm_client.py's `PUTAWAY_WORKFLOW_INIT_URL` comment for the full
    story). No reason code is required here (unlike Substitute Location
    on an existing task): there's no system-directed default being
    overridden, just a fresh destination for a loose container.

    `item_adjustments` (2026-08-08,
    `[{"itemId", "desiredQty", "reasonCode"}, ...]`), if given, runs
    `adjust_ilpn_quantities()` against this container first — the same
    Modify iLPN mechanism `complete_putaway_line()` now uses for a
    task-mode line's Completed Qty box, see that function's docstring.
    Covers both the ordinary single-item case (one entry) and the
    MIXED-container accordion case (multiple entries, one per real
    line) uniformly.

    **A genuinely multi-item (MIXED) container is CONFIRMED supported,
    2026-08-08 (fifth session), via a real HAR capture of the mobile RF
    client** (`userdirectedmultiple.har`) — an earlier version of this
    function refused any container with more than one on-hand line,
    reasoning that "the DMM flow's AcceptContainer step doesn't take an
    ItemId or Quantity input, so there's no way to tell it which item to
    move." That reasoning was backwards: neither `AcceptContainer` nor
    `AcceptLocation` need to know which item at all — MAWM's own
    workflow state carries a `multiItemContainer: true` flag and moves
    every item on the container to the *same* destination in one
    AcceptLocation call, exactly like the single-item case. The capture
    showed the identical `DCI::120` warning/override mechanism already
    implemented here, at the same states, no shape difference — so this
    function needed no structural change at all, only the removal of
    its own artificial refusal. `item_adjustments` is still useful for
    correcting a MIXED container's quantities beforehand (or reducing
    it to one item, e.g. zeroing out a mis-scanned item), but is no
    longer *required* to complete a multi-item container — the whole
    thing can be moved as-is.

    Stateless by design, consistent with every other completion path in
    this app: every call — the first attempt or a Confirm retry —
    bootstraps a brand-new workflow session via workflow_init() and
    replays the whole AcceptContainer -> AcceptLocation sequence, with
    any already-known `warning_overrides` pre-applied to each step's
    workflowVO before submitting it. This trades a little redundant work
    on retries for not having to round-trip the ~10KB intermediate
    workflowVO blob through the frontend between the warning and the
    Confirm click — the frontend's existing warning-loop
    (completeLineWithWarningHandling()) already resubmits with an
    accumulated override map and keeps looping until no warning comes
    back, so this needed no frontend changes to wire up.
    """
    dest = resolve_location(org, location)
    warning_overrides = warning_overrides or {}

    # 2026-08-08, seventh session, per explicit instruction: a consumed
    # LPN's inventory has already moved into a location record — there
    # is nothing left on the LPN itself to adjust or putaway, so this
    # refuses outright rather than attempting a call that would have
    # nothing real to act on. Mirrors the frontend's own
    # isConsumedLine() gating (public/app.js), which already keeps the
    # UI from getting here in the normal click-through flow — this is
    # the backend-side guarantee for a direct API call.
    ilpn = search_ilpn_current_location(container_id, token, org, location=dest)
    if ilpn and str(ilpn.get("Status") or "") == ILPN_CONSUMED_STATUS:
        return {
            "success": False,
            "error": (
                "This LPN has already been consumed — its inventory moved to "
                "a location and it can no longer be updated."
            ),
        }

    if item_adjustments:
        adjust_result = adjust_ilpn_quantities(
            container_id, item_adjustments, token, org, location=dest
        )
        if not adjust_result.get("success"):
            return {
                "success": False,
                "error": adjust_result.get("error") or "Inventory adjustment failed",
                "stage": "adjust",
            }

    rows = search_container_inventory(container_id, token, org, location=dest)
    if not rows:
        return {"success": False, "error": f"No on-hand inventory found for {container_id}"}

    init_resp = workflow_init(
        USER_DIRECTED_TRANSACTION_ID, PUTAWAY_WORKFLOW_SCRIPT_NAME, token, org, location=dest
    )
    workflow_vo = init_resp.get("workflowVO") if isinstance(init_resp, dict) else None
    if not isinstance(workflow_vo, dict):
        return {"success": False, "error": "Could not start the User Directed Putaway workflow"}

    apply_warning_overrides(workflow_vo, warning_overrides)
    workflow_vo["header"]["state"]["scannedContainerBarcode"] = container_id
    container_resp = workflow_execute(
        "AcceptContainerForUserDirectedPutaway",
        "AcceptContainerForUserDirectedPutaway",
        workflow_vo,
        token,
        org,
        location=dest,
    )
    warning = extract_warning(container_resp)
    if warning and warning["code"] not in warning_overrides:
        return {
            "success": False,
            "warning": True,
            "messageId": warning["code"],
            "messageText": warning["text"],
            "stage": "container",
        }
    next_vo = container_resp.get("workflowVO") if isinstance(container_resp, dict) else None
    if not isinstance(next_vo, dict):
        return {"success": False, "error": extract_message(container_resp)}

    apply_warning_overrides(next_vo, warning_overrides)
    next_vo["header"]["state"]["scannedLocationBarcode"] = to_location_id
    location_resp = workflow_execute(
        "AcceptLocationForUserDirectedPutaway",
        "AcceptLocationForUserDirectedPutaway",
        next_vo,
        token,
        org,
        location=dest,
    )
    warning = extract_warning(location_resp)
    if warning and warning["code"] not in warning_overrides:
        return {
            "success": False,
            "warning": True,
            "messageId": warning["code"],
            "messageText": warning["text"],
            "stage": "location",
        }

    final_header = (
        (location_resp.get("workflowVO") or {}).get("header")
        if isinstance(location_resp, dict)
        else None
    )
    ok = isinstance(final_header, dict) and final_header.get("status") == "SUCCESS"
    return {
        "success": bool(ok),
        "taskDetailId": f"container:{container_id}",
        "toLocationId": to_location_id,
        # Bug fixed 2026-08-08: this never included a `quantity` at all
        # (unlike the task-mode path), so a successful no-task
        # completion showed "Completed undefined  on line N" in the
        # frontend's status message. Summed across every row (not just
        # rows[0]) since a genuinely multi-item container moves all of
        # them together — whatever was actually on hand right before
        # this move (post-adjustment if item_adjustments were applied
        # above).
        "quantity": _num(sum((_dec(r.get("OnHand")) for r in rows), Decimal("0"))),
        "mawmResponse": location_resp,
        "error": None if ok else extract_message(location_resp),
    }


def preload_putaway_locations(token: str, org: str, location: str = None) -> Dict[str, Any]:
    """Every active Storage location for one facility, preloaded once per
    session (2026-08-08, replacing per-keystroke debounced calls to
    validate_putaway_location() below, per explicit instruction — see
    mawm_client.search_all_storage_locations()'s docstring for why this
    is safe: real facilities scope far smaller than the org-wide
    "thousands" the old per-keystroke approach was written to avoid).
    The frontend builds a plain Set from `entries` and checks it
    synchronously on every keystroke instead of calling this per line.
    """
    dest = resolve_location(org, location)
    rows = search_all_storage_locations(token, org, location=dest)
    entries = []
    for row in rows:
        location_id = str(_first(row, "LocationId") or "").strip()
        if not location_id:
            continue
        entries.append(
            {
                "locationId": location_id,
                "displayLocation": str(_first(row, "DisplayLocation") or "").strip()
                or location_id,
            }
        )
    return {"success": True, "count": len(entries), "entries": entries}


def validate_putaway_location(
    token: str, org: str, location_text: str, location: str = None
) -> Dict[str, Any]:
    """Backs the frontend's To Location validity check — per explicit
    instruction, button-gating depends on a real, active Storage
    location, not just non-blank text (see
    mawm_client.validate_storage_location()).
    """
    text = (location_text or "").strip()
    if not text:
        return {"success": True, "valid": False}
    dest = resolve_location(org, location)
    row = validate_storage_location(text, token, org, location=dest)
    if not row:
        return {"success": True, "valid": False}
    return {
        "success": True,
        "valid": True,
        "locationId": str(row.get("LocationId") or ""),
        "displayLocation": str(row.get("DisplayLocation") or row.get("LocationId") or ""),
    }


# ---------------------------------------------------------------------------
# Ad hoc Cycle Count (2026-08-08, ninth session) — ported from the sibling
# `cyclecount` app's own six-call chain (see mawm_client.py's Cycle Count
# section for per-call docstrings and the exact source it was ported
# from). Mirrors resolve_search()/resolve_search_multi()'s architecture:
# one group per Storage location, one line per item found there (more
# than one item -> the frontend renders the same "MIXED" accordion
# pattern already built for multi-item no-task containers, per explicit
# instruction). Quantity always starts blank/None regardless of current
# on-hand -- the count must be entered fresh, per explicit instruction.
# ---------------------------------------------------------------------------


def _extract_count_run_id(body: dict) -> str:
    """initiateCount's CountRunId lands in one of several response
    shapes across MAWM orgs -- the old cyclecount app defensively
    checks all of them; ported verbatim rather than guessing which one
    this org actually uses.
    """
    if not isinstance(body, dict):
        return ""
    for key in ("CountRunId", "countRunId"):
        val = body.get(key)
        if val:
            return str(val)
    for container_key in ("data", "Data"):
        inner = body.get(container_key)
        if isinstance(inner, dict):
            for key in ("CountRunId", "countRunId"):
                val = inner.get(key)
                if val:
                    return str(val)
    return ""


def _extract_count_task_id(body: dict) -> str:
    """Same defensive multi-shape extraction as _extract_count_run_id(), for TaskId."""
    if not isinstance(body, dict):
        return ""
    for key in ("TaskId", "taskId"):
        val = body.get(key)
        if val:
            return str(val)
    for container_key in ("data", "Data"):
        inner = body.get(container_key)
        if isinstance(inner, dict):
            for key in ("TaskId", "taskId"):
                val = inner.get(key)
                if val:
                    return str(val)
    return ""


def resolve_cycle_count_location(
    token: str, org: str, location_id: str, location: str = None
) -> Dict[str, Any]:
    """Builds one cycle-count group for a single Storage location — the
    ad hoc counterpart to resolve_search()'s task/no_task branches.
    Item-from-location lookup reuses search_location_inventory() (the
    same dcinventory endpoint the old cyclecount app calls directly, so
    no new client function was needed for it).

    **Deduplicates by ItemId** — confirmed live, 2026-08-08: a location
    can carry more than one raw inventory *record* for the very same
    item (the same duplicate-record pattern already documented for
    adjust_location_quantities() — distinguishing attributes like batch/
    lot, not a genuinely different item). `C1CS0110` came back as 4 rows
    all for item `50002236`. One line per distinct ItemId, not one line
    per raw row — a physical cycle count is "how much of this item is
    here," not "how many system records exist for it." The MIXED-style
    accordion (multiple lines under one location) is reserved for
    genuinely different items at the same location, per explicit
    instruction.
    """
    location_id = (location_id or "").strip().upper()
    if not location_id:
        return {"success": False, "error": "Location required"}

    dest = resolve_location(org, location)
    raw_rows = search_location_inventory(location_id, token, org, location=dest)
    seen_item_ids = set()
    rows: List[dict] = []
    for row in raw_rows:
        item_id = str(row.get("ItemId") or "")
        if item_id and item_id not in seen_item_ids:
            seen_item_ids.add(item_id)
            rows.append(row)
    item_ids = [str(r.get("ItemId") or "") for r in rows if r.get("ItemId")]
    items = search_items(item_ids, token, org, location=dest) if item_ids else {}

    lines: List[Dict[str, Any]] = []
    if not rows:
        lines.append(
            {
                "lineNumber": 1,
                "taskDetailId": f"cyclecount:{location_id}:",
                "locationId": location_id,
                "itemId": "",
                "description": "",
                "quantity": None,
            }
        )
    else:
        for idx, row in enumerate(rows, start=1):
            item_id = str(row.get("ItemId") or "")
            item = items.get(item_id) or {}
            lines.append(
                {
                    "lineNumber": idx,
                    "taskDetailId": f"cyclecount:{location_id}:{item_id}",
                    "locationId": location_id,
                    "itemId": item_id,
                    "description": str(item.get("Description") or ""),
                    "quantity": None,
                }
            )

    return {
        "success": True,
        "mode": "cycle_count",
        "locationId": location_id,
        "facility": dest,
        "taskType": "CYCLE_COUNT",
        "taskTypeLabel": "Cycle Count",
        "taskStatus": "",
        "taskStatusLabel": "Not Started",
        "taskTransactionId": CYCLE_COUNT_TRANSACTION_ID,
        "lineCount": len(lines),
        "lines": lines,
    }


_CYCLE_COUNT_SPLIT_RE = _MULTI_SEARCH_SPLIT_RE


def resolve_cycle_count_search_multi(
    token: str, org: str, raw_locations: str, location: str = None
) -> Dict[str, Any]:
    """Multi-location counterpart to resolve_search_multi() — same
    delimiter convention (";", ",", whitespace), one group per location,
    denormalized the same way onto each line so the frontend can treat
    the combined line list as flat.
    """
    tokens: List[str] = []
    seen = set()
    for raw_token in _CYCLE_COUNT_SPLIT_RE.split(raw_locations or ""):
        token_value = raw_token.strip().upper()
        if token_value and token_value not in seen:
            seen.add(token_value)
            tokens.append(token_value)

    if not tokens:
        return {"success": False, "error": "Storage location required"}

    dest = resolve_location(org, location)
    groups: List[Dict[str, Any]] = []
    not_found: List[str] = []
    for token_value in tokens:
        result = resolve_cycle_count_location(token, org, token_value, location=dest)
        if not result.get("success"):
            not_found.append(token_value)
            continue
        group_key = f"cycle_count:{result['locationId']}"
        for line in result.get("lines") or []:
            line["groupKey"] = group_key
            line["groupMode"] = "cycle_count"
            line["groupLocationId"] = result["locationId"]
            line["groupTaskStatus"] = result.get("taskStatus", "")
            line["groupTaskStatusLabel"] = result.get("taskStatusLabel", "")
        result["groupKey"] = group_key
        groups.append(result)

    if not groups:
        return {
            "success": False,
            "error": f"No valid Storage location found for: {', '.join(not_found)}",
        }

    line_count = sum(len(g.get("lines") or []) for g in groups)
    return {
        "success": True,
        "facility": dest,
        "groups": groups,
        "lineCount": line_count,
        "notFound": not_found,
    }


def _cycle_count_result_response(
    result_row: Optional[dict], location_id: str, item_id: str, count_run_id: str
) -> Dict[str, Any]:
    """Shared shape-builder for both complete_cycle_count_line() and
    check_cycle_count_status() — `success` is only True when
    `Status == "Booked"`; every other status (including "Pending
    Booking", which is both a normal in-flight stage and the permanent
    out-of-tolerance parked state — see complete_cycle_count_line()'s
    docstring) comes back False with a real status/reason attached, not
    a bare error.
    """
    if result_row is None:
        return {
            "success": False,
            "error": "No count result available yet",
            "locationId": location_id,
            "itemId": item_id,
            "countRunId": count_run_id,
            "status": "",
        }
    status_label = str(result_row.get("Status") or "")
    booked = status_label == "Booked"
    response = {
        "success": booked,
        "locationId": location_id,
        "itemId": item_id,
        "countRunId": count_run_id,
        "status": status_label,
        "statusKey": str(result_row.get("StatusKey") or ""),
        "previousQty": result_row.get("OriginalQuantity"),
        "countedQty": result_row.get("CountQuantity"),
        "varianceQty": result_row.get("VarianceQuantity"),
        "bookingFailureReason": result_row.get("BookingFailureReason"),
    }
    if not booked:
        if status_label == "Pending Booking":
            response["error"] = (
                "Out of tolerance — pending supervisor booking. "
                "Location is locked; inventory not yet updated."
            )
        elif status_label:
            response["error"] = (
                result_row.get("BookingFailureReason") or f"Count not booked (status: {status_label})"
            )
        else:
            response["error"] = "Count not booked yet"
    return response


def complete_cycle_count_line(
    token: str, org: str, location_id: str, item_id: str, quantity, location: str = None
) -> Dict[str, Any]:
    """Runs the full six-call ad hoc Cycle Count chain for one
    location/item pair (ported from the sibling cyclecount app — see
    mawm_client.py's Cycle Count section for the per-call docstrings):
    initiateCount -> validateItemAndGetItemDetails -> acceptQuantity ->
    persistCountDetails -> endCount.

    **Revised 2026-08-08 after live investigation** (see
    mawm_cycle_count_location_investigation.md, user-captured) — none of
    these six calls' own HTTP status/messages reveal whether the count
    actually got applied. MAWM runs three real outcomes, invisible until
    checked afterward:
      - Perfect match: books synchronously, no warning anywhere.
      - Within tolerance: acceptQuantity returns a WARNING (INM::227
        "Quantity mismatch") but the count still books — ASYNCHRONOUSLY,
        confirmed live to take a few seconds (same posture as Modify
        iLPN's endIlpn elsewhere in this app).
      - Out of tolerance: acceptQuantity returns that same WARNING plus
        an ERROR (INM::411 "Recount required"); persistCountDetails/
        endCount still both report success, but the count run parks in
        "Pending Booking" forever — a real human/supervisor decision
        outside this app, not something that resolves on its own.
    So this runs the chain to completion regardless of what any
    individual step reports (a WARNING or ERROR message at validate/
    accept is not, by itself, treated as failure — confirmed live that
    both still let the chain proceed), then checks
    search_inventory_count_results() **once** for whatever the status
    is right then — it does NOT block waiting for booking to finish
    (2026-08-08, revised per explicit instruction: the first version
    polled synchronously for up to ~3s before returning, which just
    made the request slow; now it returns immediately with the
    in-flight status, e.g. "Count Initiated"/"Pending Booking", and the
    frontend calls check_cycle_count_status() every couple seconds
    afterward to pick up the real resolution once MAWM finishes
    booking it — see completeCycleCountLineAction()'s docstring in
    app.js). Only a call that genuinely raises (network failure,
    unparseable non-2xx body) stops the chain early.

    Each line runs its own independent chain (its own initiateCount/
    endCount) — including each item within a multi-item location's
    accordion. **Not yet confirmed live** whether MAWM tolerates
    re-initiating a count against the same location back to back for a
    second/third item — confirmed live this session that running two
    counts against the *same* location within seconds of each other can
    produce a genuine "Booking Failed" concurrency conflict
    ("Cycle count already in progress for different inventory read"),
    so a multi-item accordion may need to serialize its per-item
    completions rather than fire them concurrently; not yet addressed
    here since it needs a real multi-item location to verify against.
    """
    location_id = (location_id or "").strip().upper()
    item_id = (item_id or "").strip()
    if not location_id or not item_id:
        return {"success": False, "error": "Location and Item are required"}
    if quantity is None:
        return {"success": False, "error": "Quantity is required"}

    dest = resolve_location(org, location)

    try:
        info_before = search_location_count_info(location_id, token, org, location=dest)
    except Exception:  # noqa: BLE001
        info_before = None
    locked_before = bool(info_before and info_before.get("CycleCountPending"))

    try:
        initiate = initiate_count(location_id, token, org, location=dest)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"initiateCount failed: {exc}"}
    if not (200 <= int(initiate.get("_httpStatus") or 0) < 300):
        return {"success": False, "error": extract_message(initiate) or "initiateCount failed"}
    count_run_id = _extract_count_run_id(initiate)
    task_id = _extract_count_task_id(initiate)
    if not count_run_id or not task_id:
        return {
            "success": False,
            "error": "initiateCount did not return a CountRunId/TaskId",
        }

    try:
        validate_item_and_get_item_details(location_id, count_run_id, item_id, token, org, location=dest)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"validateItemAndGetItemDetails failed: {exc}"}

    try:
        accept_quantity(location_id, count_run_id, task_id, item_id, quantity, token, org, location=dest)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"acceptQuantity failed: {exc}"}

    try:
        persist_count_details(location_id, count_run_id, task_id, item_id, quantity, token, org, location=dest)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"persistCountDetails failed: {exc}"}

    try:
        end_count(location_id, count_run_id, token, org, location=dest)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"endCount failed: {exc}"}

    try:
        rows = search_inventory_count_results(count_run_id, location_id, token, org, location=dest)
    except Exception:  # noqa: BLE001
        rows = []
    result_row = next((r for r in rows if str(r.get("ItemId") or "") == item_id), None)
    response = _cycle_count_result_response(result_row, location_id, item_id, count_run_id)

    try:
        info_after = search_location_count_info(location_id, token, org, location=dest)
    except Exception:  # noqa: BLE001
        info_after = None
    response["locationLockedBefore"] = locked_before
    response["locationLockedAfter"] = bool(info_after and info_after.get("CycleCountPending"))
    return response


def check_cycle_count_status(
    token: str, org: str, location_id: str, item_id: str, count_run_id: str, location: str = None
) -> Dict[str, Any]:
    """Lightweight poll target for the frontend (2026-08-08) — re-checks
    search_inventory_count_results() for one already-started count run,
    without re-running the six-call chain. Booking is asynchronous (see
    complete_cycle_count_line()'s docstring), so the frontend calls this
    every couple seconds after a non-"Booked" result to update the row
    live once MAWM finishes booking it, rather than the original
    request blocking for several seconds.
    """
    location_id = (location_id or "").strip().upper()
    item_id = (item_id or "").strip()
    count_run_id = (count_run_id or "").strip()
    if not location_id or not item_id or not count_run_id:
        return {"success": False, "error": "locationId, itemId, and countRunId are required"}

    dest = resolve_location(org, location)
    try:
        rows = search_inventory_count_results(count_run_id, location_id, token, org, location=dest)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"inventoryCountResult search failed: {exc}"}
    result_row = next((r for r in rows if str(r.get("ItemId") or "") == item_id), None)
    response = _cycle_count_result_response(result_row, location_id, item_id, count_run_id)

    try:
        info = search_location_count_info(location_id, token, org, location=dest)
    except Exception:  # noqa: BLE001
        info = None
    response["locationLocked"] = bool(info and info.get("CycleCountPending"))
    return response
