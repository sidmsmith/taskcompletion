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
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from mawm_client import (
    DEFAULT_TRANSACTION_BY_TASK_TYPE,
    TASK_TYPE_LABELS,
    commit_putaway_move,
    complete_task,
    extract_warning,
    fetch_putaway_move,
    resolve_location,
    search_items,
    search_putaway_reason_codes,
    search_task,
    search_task_transactions,
    task_status_description,
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
        planned = _dec(_first(line, "Quantity", "PlannedQuantity") or 0)
        completed = _dec(_first(line, "CompletedQuantity") or 0)
        remaining = planned - completed
        item_id = str(_first(line, "ItemId") or "")
        item = items.get(item_id) or {}
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
                "uomId": str(_first(line, "UomTypeId", "UomId", "QuantityUomId") or ""),
                "plannedQuantity": _num(planned),
                "completedQuantity": _num(completed),
                "remainingQuantity": _num(remaining if remaining > 0 else 0),
            }
        )
    return lines


def load_task(
    token: str,
    org: str,
    task_id: str,
    location: str = None,
) -> Dict[str, Any]:
    if not task_id:
        return {"success": False, "error": "Task Id required"}
    dest = resolve_location(org, location)
    raw_task = search_task(task_id, token, org, location=dest)
    if not raw_task:
        return {"success": False, "error": f"Task {task_id} not found"}

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
) -> Dict[str, Any]:
    """Complete one Putaway task line in full.

    `to_location_id` is only sent by the frontend when the user actually
    edited the line's destination away from what was loaded (see
    public/app.js's `.to-location-input`) — its mere presence here is
    trusted as "the user wants to substitute the destination," the same
    way `mode` ("full"/"partial") is trusted from the caller elsewhere
    in this module. When present, a `reason_code_id` is required — the
    frontend enforces this via a required reason-code modal (see
    preload_putaway_reason_codes()) before ever sending toLocationId,
    so a substitution without one here means the frontend didn't
    enforce that; fail rather than silently proceeding.

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
    move for the task, then commit it.

    fetchNextPutawayMoveAndStartLaborActivity returns whatever move is
    "next" for the TaskId, not one scoped to a specific TaskDetailId — so
    this checks the fetched move's CurrentTaskDetailId against the line
    the user actually selected and refuses to commit on a mismatch,
    rather than silently completing the wrong line.

    Full completion (vs. partial, not yet wired) is expressed by setting
    the fetched InventoryMove's CompletedQuantity equal to its Quantity
    before committing — the document's own worked example does exactly
    this (CompletedQuantity: 240 alongside Quantity: 240), and the
    fetched move itself doesn't set CompletedQuantity that way.

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
        return {"success": False, "error": "No putaway move returned for this task"}

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
        "error": None if ok else (commit_resp.get("message") or "Complete failed"),
    }


# SAVED FOR LATER — "user directed putaway" (task-independent core API
# alternative), superseded by the substitute-location + reason-code flow
# implemented above. Kept for reference in case it's needed again — see
# mawm_client.py's commented-out move_container_user_directed() and the
# PUTAWAY_USER_DIRECTED_MOVE_URL/USER_DIRECTED_TRANSACTION_ID constants.
#
# def _complete_putaway_line_user_directed(
#     token: str,
#     org: str,
#     task_id: str,
#     task_detail_id: str,
#     to_location_id: str,
#     location: str = None,
#     warning_overrides: Optional[Dict[str, str]] = None,
# ) -> Dict[str, Any]:
#     """User-directed putaway to an operator-chosen destination, per
#     mawm_user_directed_putaway_with_warnings.md's core API alternative
#     (see mawm_client.move_container_user_directed()'s docstring for why
#     this path, not that document's proven DMM Mobile Facade flow, was
#     used here).
#
#     Re-fetches the task fresh to get the line's real current ItemId/
#     SourceContainerId/remaining quantity (same defensive re-verification
#     receivingworkbench's own receive_line() does) — `to_location_id`
#     itself is the only thing trusted from the caller, since it's a pure
#     UI-state fact (what's currently typed in the grid), not fetched data.
#     """
#     state = _line_state(token, org, task_id, task_detail_id, location=location)
#     if not state.get("success"):
#         return state
#
#     line = state["line"]
#     remaining = _dec(line["remainingQuantity"])
#     if remaining <= 0:
#         return {"success": False, "error": "No remaining quantity on this line"}
#
#     move_resp = move_container_user_directed(
#         line["lpnId"],
#         to_location_id,
#         line["itemId"],
#         _num(remaining),
#         token,
#         org,
#         location=state["dest"],
#         warning_overrides=warning_overrides,
#     )
#     warning = extract_warning(move_resp)
#     if warning and warning["code"] not in (warning_overrides or {}):
#         return {
#             "success": False,
#             "warning": True,
#             "messageId": warning["code"],
#             "messageText": warning["text"],
#             "stage": "move",
#         }
#
#     ok = move_resp.get("success", True) if isinstance(move_resp, dict) else True
#     return {
#         "success": bool(ok),
#         "taskDetailId": task_detail_id,
#         "quantity": _num(remaining),
#         "toLocationId": to_location_id,
#         "mawmResponse": move_resp,
#         "error": None if ok else (move_resp.get("message") or "Complete failed"),
#     }
