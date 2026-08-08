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
    DEFAULT_ADJUSTMENT_REASON_CODE,
    DEFAULT_TRANSACTION_BY_TASK_TYPE,
    PUTAWAY_WORKFLOW_SCRIPT_NAME,
    TASK_TYPE_LABELS,
    USER_DIRECTED_TRANSACTION_ID,
    adjust_ilpn_inventory,
    apply_warning_overrides,
    commit_putaway_move,
    complete_task,
    extract_message,
    extract_warning,
    fetch_putaway_move,
    refresh_ilpn_read_timestamp,
    resolve_location,
    search_all_storage_locations,
    search_container_inventory,
    search_ilpn_current_location,
    search_items,
    search_putaway_reason_codes,
    search_task,
    search_task_id_for_container,
    search_task_transactions,
    task_status_description,
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


def _normalize_task_lines(raw_task: dict, items: Optional[Dict[str, dict]] = None) -> List[dict]:
    """Field mapping confirmed live (SS-DEMO, TaskId IBPWIBPT0929) — see
    module docstring. `items` is an optional {ItemId: item-master row}
    map (from mawm_client.search_items) used to fill in Description,
    which TaskDetail itself doesn't carry.
    """
    items = items or {}
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
        lines.append(
            {
                "lineNumber": idx,
                "taskDetailId": str(
                    _first(line, "TaskDetailId", "PK", "Unique_Identifier") or idx
                ),
                "itemId": item_id,
                "description": str(item.get("Description") or ""),
                "fromLocationId": str(_first(line, "SourceLocationId", "FromLocationId") or ""),
                "toLocationId": str(_first(line, "TargetLocationId", "ToLocationId") or ""),
                "lpnId": str(
                    _first(line, "TargetContainerId", "SourceContainerId", "WorkingContainerId")
                    or ""
                ),
                "uomId": uom_label,
                "uomFactor": _num(factor),
                "plannedQuantity": _num(planned_base / factor),
                "completedQuantity": _num(completed_base / factor),
                "remainingQuantity": _num(
                    (remaining_base if remaining_base > 0 else Decimal("0")) / factor
                ),
                "mixedItems": None,  # only ever set on a no_task MIXED container line, see resolve_search()
            }
        )
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
    lines = _normalize_task_lines(raw_task, items)

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

    current_location = str(ilpn.get("CurrentLocationId") or "")
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
        "fromLocationId": current_location,
        "toLocationId": "",
        "lpnId": search_value,
        "uomId": uom_label,
        "uomFactor": _num(factor),
        "plannedQuantity": _num(qty),
        "completedQuantity": 0,
        "remainingQuantity": _num(qty),
        "mixedItems": mixed_items,
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

    **UNCONFIRMED against an allocated LPN**: the document's own
    testing only ever exercised this against a *non-allocated* LPN via
    Postman. Whether adjusting on-hand this way plays correctly with a
    LPN that's already the target of an open task's planned quantity —
    e.g., whether `fetchNextPutawayMoveAndStartLaborActivity` re-syncs
    its `Quantity` to match, or still expects the original planned
    amount — is untested. This is exactly the scenario the caller
    (`complete_putaway_line()`) is being verified against live.
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

    **2026-08-08, superseding the same day's earlier (confirmed-broken)
    attempt**: a `desired_qty` different from what's planned no longer
    tries to book a partial completion directly (MAWM's core
    `commitAndFetchNextMove` rejects that outright — see
    `mawm_client.commit_putaway_move()`'s docstring). Instead, per
    `mawm_modify_ilpn_query_and_adjustment.md`, it corrects the LPN's
    actual on-hand quantity FIRST via `adjust_ilpn_quantities()`
    (requires `item_id`/`lpn_id` — the frontend already has both off
    the loaded line), then falls through to the normal, unmodified full
    completion below, which re-fetches the move fresh and so picks up
    whatever quantity MAWM now considers correct post-adjustment.
    **UNCONFIRMED**: see `adjust_ilpn_quantities()`'s docstring — this
    whole sequence has only ever been verified against a non-allocated
    LPN; behavior against a line already on an open task (this case) is
    the live test this was built for.

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
    if desired_qty is not None:
        if not item_id or not lpn_id:
            return {
                "success": False,
                "error": "itemId and lpnId are required to adjust the completed quantity",
            }
        adjust_result = adjust_ilpn_quantities(
            lpn_id,
            [
                {
                    "itemId": item_id,
                    "desiredQty": desired_qty,
                    "reasonCode": adjustment_reason_code,
                }
            ],
            token,
            org,
            location=location,
        )
        if not adjust_result.get("success"):
            return {
                "success": False,
                "error": adjust_result.get("error") or "Inventory adjustment failed",
                "stage": "adjust",
            }

    if to_location_id and to_location_id.strip():
        if not reason_code_id:
            return {
                "success": False,
                "error": "A reason code is required when changing the destination location",
            }
    else:
        to_location_id = None

    return _complete_putaway_line_system_directed(
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
