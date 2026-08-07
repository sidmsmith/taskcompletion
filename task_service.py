#!/usr/bin/env python3
"""Task Check In — shared service for the web API.

Mirrors receivingworkbench's rw_service.py layering: this module owns
normalization and business rules (remaining-quantity math, full/partial/
all-line orchestration); mawm_client.py owns raw HTTP calls. Everything
here that touches Task Management's actual field names is UNCONFIRMED —
see mawm_client.py's module docstring. _normalize_task_lines() tries
several plausible key names per field for exactly that reason; once a
real search_task() response is captured, trim it down to the one real
shape instead of guessing across several.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from mawm_client import (
    DEFAULT_TRANSACTION_BY_TASK_TYPE,
    TASK_TYPE_LABELS,
    complete_task,
    resolve_location,
    search_task,
    search_task_transactions,
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


def _normalize_task_lines(raw_task: dict) -> List[dict]:
    """UNCONFIRMED field mapping — see module docstring."""
    raw_lines = (
        raw_task.get("TaskDetail")
        or raw_task.get("TaskLine")
        or raw_task.get("Lines")
        or raw_task.get("Detail")
        or []
    )
    if not isinstance(raw_lines, list):
        raw_lines = []

    lines: List[dict] = []
    for idx, line in enumerate(raw_lines, start=1):
        planned = _dec(_first(line, "PlannedQuantity", "ExpectedQuantity", "Quantity") or 0)
        completed = _dec(
            _first(line, "CompletedQuantity", "ActualQuantity", "CountedQuantity") or 0
        )
        remaining = planned - completed
        lines.append(
            {
                "lineNumber": idx,
                "taskDetailId": str(
                    _first(line, "TaskDetailId", "TaskLineId", "PK", "Unique_Identifier") or idx
                ),
                "itemId": str(_first(line, "ItemId") or ""),
                "description": str(
                    _first(line, "Description", "ItemDescription") or ""
                ),
                "fromLocationId": str(_first(line, "FromLocationId", "SourceLocationId") or ""),
                "toLocationId": str(_first(line, "ToLocationId", "DestinationLocationId") or ""),
                "lpnId": str(_first(line, "LpnId", "ContainerId") or ""),
                "uomId": str(_first(line, "UomId", "QuantityUomId") or ""),
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

    task_type = str(_first(raw_task, "TaskType") or "").strip().upper()
    lines = _normalize_task_lines(raw_task)

    return {
        "success": True,
        "taskId": task_id,
        "facility": dest,
        "taskType": task_type,
        "taskTypeLabel": _task_type_label(task_type),
        "taskStatus": _first(raw_task, "TaskStatus"),
        "lineCount": len(lines),
        "lines": lines,
    }


def preload_task_transactions(
    token: str,
    org: str,
    task_type: str,
    location: str = None,
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
    return {
        "success": True,
        "count": len(entries),
        "entries": entries,
        "defaultTransactionId": default_transaction_id(task_type),
    }


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
