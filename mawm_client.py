#!/usr/bin/env python3
"""Shared MAWM API client for Task Check In.

Auth/session plumbing (normalize_token, resolve_location,
build_task_headers, get_manhattan_token, validate_org) is carried over
verbatim from receivingworkbench's mawm_client.py — same host, same
.token-file-first / OAuth resolution order, same org convention. See
that app (and supplierenablement, which it was originally copied from)
for the pattern this follows.

The Task Management endpoints below (search / transaction search /
complete) are NOT confirmed against a live MAWM environment. Nothing in
mawm_api_library documents the Task Management domain yet (only
receiving's own narrower `receiving/api/task/transaction/search`,
copied into search_task_transactions_generic() as a fallback, is
confirmed real — see receivingworkbench). The URLs and payload/response
shapes here are informed guesses based on MAWM's general
search/{Query,Template,Page,Size} convention used by every other
confirmed object in this ecosystem. Treat every function below marked
UNCONFIRMED as a first draft to be corrected against a real RF-session
capture, not as verified fact.
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

# --- UNCONFIRMED: Task Management endpoints ---
# Best-guess paths following MAWM's task-management component naming.
# Verify (and correct) each of these against a real RF task-completion
# session capture before trusting them in production use.
TASK_SEARCH_URL = f"{HOST}/task-management/api/task-management/task/search"
TASK_TRANSACTION_SEARCH_URL = f"{HOST}/task-management/api/task-management/task/transaction/search"
TASK_COMPLETE_URL = f"{HOST}/task-management/api/task-management/task/completeTask"

# Confirmed real (copied from receivingworkbench) — receiving's own
# transaction list, kept here only as a fallback/reference for how a
# transaction-search response is typically shaped. Not necessarily the
# same list as Task Management's own transactions.
RECEIVING_TRANSACTION_SEARCH_URL = f"{HOST}/receiving/api/task/transaction/search"

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

# UNCONFIRMED: placeholder default TransactionId per task type, mirroring
# receivingworkbench's DEFAULT_TRANSACTION_ID="Receiving" convention (a
# default *selection* when the org's real transaction list contains a
# match — never sent blind). Confirm the real default with the user once
# search_task_transactions() is verified.
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
    """UNCONFIRMED — look up one Task by TaskId.

    Guessed payload shape follows every other confirmed MAWM search
    endpoint in mawm_api_library (Query/Page/Size). The response's line
    array is assumed to live under a `TaskDetail`/`Lines`-shaped key;
    task_service._normalize_task() tries several plausible key names for
    exactly this reason — correct both once a real response is captured.
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
    """UNCONFIRMED — TransactionIds available for a given TaskType.

    Mirrors receivingworkbench's search_receiving_transactions() shape
    (each row expected to carry TransactionId + a paired StrategyId), but
    against the guessed Task Management endpoint instead of receiving's.
    Correct against a real capture before trusting the response shape.
    """
    token = normalize_token(token)
    payload = {
        "Query": f"TaskType ='{task_type}'" if task_type else "",
        "Size": 1000,
        "Sort": {"attribute": "TransactionId", "direction": "asc"},
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

    Payload shape is a placeholder — the real MAWM completion call for
    RF-driven task types (Putaway/Picking/Cycle Count/Replenishment) has
    not been captured yet. This exists so the frontend/service layers
    have a real seam to call once the user supplies an RF-session capture
    for the first task type (Putaway); expect this function's payload,
    and possibly its URL, to change once that happens.
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
