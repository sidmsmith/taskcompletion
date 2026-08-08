#!/usr/bin/env python3
"""Shared MAWM API client for Task Check In.

Auth/session plumbing (normalize_token, resolve_location,
build_task_headers, get_manhattan_token, validate_org) is carried over
verbatim from receivingworkbench's mawm_client.py — same host, same
.token-file-first / OAuth resolution order, same org convention. See
that app (and supplierenablement, which it was originally copied from)
for the pattern this follows.

Task Management lives under its own real MAWM component named plainly
"task" (confirmed via cloudComponent: com-manh-cp-task-1 in live
responses) — not "task-management" as originally guessed. Confirmed
directly against SS-DEMO with a real TaskId (IBPWIBPT0929, a Putaway
task):

- search_task() -> POST task/api/task/task/search — CONFIRMED. Query
  by TaskId works exactly like every other MAWM search endpoint. The
  response nests the full line-level array under TaskDetail[] (same
  field shape as a standalone task/api/task/taskDetail/search call —
  confirmed identical by direct comparison), so only one call is
  needed to load a task with its lines, mirroring how
  receivingworkbench reads AsnLine[] nested inside its own asn/search
  response.
- search_task_transactions() -> POST task/api/task/transaction/search
  — CONFIRMED (a distinct, task-component-owned transaction list, not
  receiving's own `receiving/api/task/transaction/search`). Query by
  TransactionTypeId, not TaskType — the Task record itself doesn't
  carry a `TaskType` field at all; the closest real field is
  `TransactionTypeId` (e.g. "Putaway"), which task_service.py now
  reads directly off the loaded task instead of guessing per task type.
- complete_task() -> STILL UNCONFIRMED. This one actually mutates task/
  inventory state (moves a container, marks a task detail complete),
  so it hasn't been probed against the live SS-DEMO org — unlike the
  two search calls above, guessing wrong here risks corrupting real
  demo data, not just a 404. Correct this against a real RF-session
  capture (or explicit sign-off to probe live) before trusting it.
"""

import os
import re
from typing import Dict, List, Optional

import requests
import urllib3
from requests.auth import HTTPBasicAuth

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HOST = os.getenv("MANHATTAN_API_HOST", "salep.sce.manh.com")
HOST = f"https://{HOST}" if not HOST.startswith("http") else HOST
AUTH_HOST = os.getenv("MANHATTAN_AUTH_HOST", "salep-auth.sce.manh.com")

# CONFIRMED live against SS-DEMO (see module docstring).
TASK_SEARCH_URL = f"{HOST}/task/api/task/task/search"
TASK_TRANSACTION_SEARCH_URL = f"{HOST}/task/api/task/transaction/search"

# UNCONFIRMED — a mutating call; not yet probed live. See module docstring.
TASK_COMPLETE_URL = f"{HOST}/task/api/task/task/completeTask"

# CONFIRMED (Tier 1, mawm_api_library/item/api.md) — used to hydrate each
# line's item Description, which the Task/TaskDetail objects don't carry
# themselves (confirmed: neither object has a Description/ItemDescription
# field for the line's item).
ITEM_SEARCH_URL = f"{HOST}/item-master/api/item-master/item/search"

USERNAME_BASE = os.getenv("MANHATTAN_USERNAME_BASE", "sdtadmin@")
CLIENT_ID = os.getenv("MANHATTAN_CLIENT_ID", "omnicomponent.1.0.0")
REQUEST_TIMEOUT = 60

_session = requests.Session()
_session.trust_env = False
_NO_PROXY = {"http": None, "https": None}

# Task types this app targets, in build order. Putaway is the first
# workflow being wired up end to end; the others are UI-ready but not
# yet verified against a real completion call.
TASK_TYPES = ("PUTAWAY", "PICKING", "CYCLE_COUNT", "REPLENISHMENT")

TASK_TYPE_LABELS = {
    "PUTAWAY": "Putaway",
    "PICKING": "Picking",
    "CYCLE_COUNT": "Cycle Counting",
    "REPLENISHMENT": "Replenishment",
}

# Fallback default TransactionId per task type, mirroring
# receivingworkbench's DEFAULT_TRANSACTION_ID="Receiving" convention (a
# default *selection* when the org's real transaction list contains a
# match — never sent blind). Only used if the loaded task's own
# TransactionTypeId (confirmed present directly on every Task record,
# e.g. "Putaway") isn't itself present in the org's transaction list.
DEFAULT_TRANSACTION_BY_TASK_TYPE = {
    "PUTAWAY": "Putaway",
    "PICKING": "Picking",
    "CYCLE_COUNT": "Cycle Count",
    "REPLENISHMENT": "Replenishment",
}


def _get(url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    kwargs.setdefault("verify", False)
    kwargs.setdefault("proxies", _NO_PROXY)
    return _session.get(url, **kwargs)


def _post(url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    kwargs.setdefault("verify", False)
    kwargs.setdefault("proxies", _NO_PROXY)
    return _session.post(url, **kwargs)


def normalize_token(token: str) -> str:
    """Clean pasted tokens: strip whitespace, quotes, and redundant Bearer prefix."""
    token = (token or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'"):
        token = token[1:-1].strip()
    return token


def resolve_location(org: str, location: str = None, default_suffix: str = "DM1") -> str:
    """Resolve full facility id for selectedLocation."""
    org = org.upper()
    if location and str(location).strip():
        loc = str(location).strip().upper()
        if loc.startswith(org):
            return loc
        if "-" in loc:
            return loc
        return f"{org}-{loc}"
    return f"{org}-{default_suffix}"


def build_task_headers(
    token: str, org: str, facility_suffix: str = "DM1", location: str = None
) -> dict:
    org = org.upper()
    loc = resolve_location(org, location, facility_suffix)
    token = normalize_token(token)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "selectedOrganization": org,
        "selectedLocation": loc,
    }


def get_manhattan_token(org: str) -> Optional[str]:
    """Obtain OAuth token using MANHATTAN_PASSWORD and MANHATTAN_SECRET env vars."""
    password = os.getenv("MANHATTAN_PASSWORD", "").strip()
    secret = os.getenv("MANHATTAN_SECRET", "").strip()
    if not password or not secret:
        return None

    url = f"https://{AUTH_HOST}/oauth/token"
    username = f"{USERNAME_BASE}{org.lower()}"
    data = {
        "grant_type": "password",
        "username": username,
        "password": password,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    auth = HTTPBasicAuth(CLIENT_ID, secret)
    try:
        response = _post(url, data=data, headers=headers, auth=auth)
        if response.status_code == 200:
            return response.json().get("access_token")
        print(f"OAuth failed ({response.status_code}): {response.text[:300]}")
    except requests.RequestException as exc:
        print(f"OAuth error: {exc}")
    return None


def validate_org(org: str) -> bool:
    return bool(re.match(r"^[A-Z0-9]+-DEMO$", org or ""))


def _response_data_list(body) -> List[dict]:
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if not isinstance(body, dict):
        return []
    data = body.get("data") or body.get("Data") or []
    return data if isinstance(data, list) else []


def search_task(task_id: str, token: str, org: str, location: str = None) -> Optional[dict]:
    """CONFIRMED — look up one Task by TaskId (task/api/task/task/search).

    Verified live against SS-DEMO with TaskId IBPWIBPT0929 (a Putaway
    task). The response nests the full line array under TaskDetail[]
    (each row field-identical to a standalone taskDetail/search call).
    """
    token = normalize_token(token)
    payload = {
        "Query": f"TaskId ='{task_id}'",
        "Size": 5,
        "Page": 0,
    }
    response = _post(
        TASK_SEARCH_URL,
        headers=build_task_headers(token, org, location=location),
        json=payload,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"task search failed: {response.status_code} {response.text[:500]}"
        )
    data = _response_data_list(response.json())
    return data[0] if data else None


def search_task_transactions(
    task_type: str, token: str, org: str, location: str = None
) -> List[dict]:
    """CONFIRMED — TransactionIds for a TransactionTypeId, via
    task/api/task/transaction/search.

    Verified live: an empty Query returns the org's full transaction
    list (SS-DEMO currently seeds only one row, "SelectTask", with
    TransactionTypeId="Task"); filtering `TransactionTypeId ='Putaway'`
    correctly returned zero rows in that same org (none seeded yet for
    Putaway specifically) rather than erroring — confirms the field name
    and query syntax, not that every org has per-task-type rows seeded.
    Each row carries TransactionId + StrategyId, same shape
    receivingworkbench's own transaction picker expects.

    Confirmed wrong in the original guess: the object has no `TaskType`
    field — `task_type` here is filtered against `TransactionTypeId`.
    """
    token = normalize_token(token)
    payload = {
        "Query": f"TransactionTypeId ='{task_type}'" if task_type else "",
        "Size": 1000,
        "Page": 0,
    }
    response = _post(
        TASK_TRANSACTION_SEARCH_URL,
        headers=build_task_headers(token, org, location=location),
        json=payload,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"task transaction search failed: {response.status_code} {response.text[:500]}"
        )
    return _response_data_list(response.json())


def search_items(
    item_ids: List[str], token: str, org: str, location: str = None
) -> Dict[str, dict]:
    """CONFIRMED (Tier 1) — item Description lookup, to hydrate task lines.

    Neither Task nor TaskDetail carries an item description field, so
    this is called after loading a task to fill that column in — same
    endpoint/shape receivingworkbench already uses for the same reason.
    """
    clean = [str(i).strip() for i in item_ids if str(i).strip()]
    if not clean:
        return {}
    quoted = ", ".join(
        f"'{item_id.replace(chr(39), chr(39) + chr(39))}'" for item_id in clean
    )
    payload = {
        "Query": f"ItemId in ({quoted})",
        "Page": 0,
        "Size": max(len(clean), 50),
        "Template": {"ItemId": "", "Description": ""},
    }
    headers = build_task_headers(token, org, location=location)
    headers["FacilityId"] = resolve_location(org, location)
    try:
        response = _post(ITEM_SEARCH_URL, headers=headers, json=payload)
    except requests.RequestException as exc:
        print(f"Warning: item search failed: {exc}")
        return {}
    if response.status_code != 200:
        print(f"Warning: item search failed: {response.status_code}")
        return {}
    data = _response_data_list(response.json())
    return {str(item.get("ItemId")): item for item in data if item.get("ItemId")}


def complete_task(
    task_id: str,
    task_detail_id: str,
    quantity,
    transaction_id: str,
    token: str,
    org: str,
    location: str = None,
    strategy_id: str = None,
) -> dict:
    """UNCONFIRMED — complete (fully or partially) one Task line.

    Unlike search_task()/search_task_transactions() (both confirmed live
    against SS-DEMO this session), this call was deliberately NOT probed
    against the live org — it mutates real state (moves a container,
    marks a task detail complete), so guessing wrong here risks leaving
    SS-DEMO's test data in a bad state rather than just a 404. The URL
    follows the same task/api/task/task/{verb} shape the two confirmed
    calls use, but is not itself confirmed. Correct this (URL and
    payload both) against a real RF-session capture, or explicit
    sign-off to probe it live, before trusting it.
    """
    token = normalize_token(token)
    payload = {
        "TaskId": task_id,
        "TaskDetailId": task_detail_id,
        "Quantity": quantity,
        "TransactionId": transaction_id,
    }
    if strategy_id:
        payload["StrategyId"] = strategy_id
    response = _post(
        TASK_COMPLETE_URL,
        headers=build_task_headers(token, org, location=location),
        json=payload,
    )
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:1200]}
    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"task complete failed: {response.status_code} {response.text[:800]}"
        )
    if isinstance(body, dict):
        body["_requestPayload"] = payload
    return body if isinstance(body, dict) else {"data": body, "_requestPayload": payload}
