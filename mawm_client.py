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
from datetime import datetime, timezone
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

# Confirmed-by-user-provided-capture (Path C, "core task API alternative")
# from mawm_putaway_api_call_set_with_warning_handling.md — a two-call
# fetch-then-commit sequence keyed by TaskId, distinct from the DMM mobile
# facade's stateful scan workflow (Path A in that same document, not used
# here — it requires preserving a workflowVO context between calls the
# way this app's stateless Flask backend doesn't). This is the real
# completion mechanism for Putaway; see complete_putaway_line() in
# task_service.py for the orchestration and warning handling built on
# top of these two calls.
PUTAWAY_FETCH_MOVE_URL_TEMPLATE = f"{HOST}/putaway/api/putaway/execution/task/{{task_id}}/fetchNextPutawayMoveAndStartLaborActivity"
PUTAWAY_COMMIT_MOVE_URL = f"{HOST}/putaway/api/putaway/execution/transfer/commitAndFetchNextMove"
PUTAWAY_EXECUTION_CRITERIA_ID = "Putaway Execution Criteria"

# Confirmed-by-user-provided-capture (mawm_user_directed_putaway_with_
# warnings.md's "core API alternative") — task-independent putaway,
# revived 2026-08-08 for the no-open-task iLPN case (see
# task_service.complete_container_putaway()). Not used for the
# Substitute-Location-on-an-existing-task case — that still goes through
# the system-directed fetch+commit sequence with an overridden
# destination (see _complete_putaway_line_system_directed()).
PUTAWAY_USER_DIRECTED_MOVE_URL = f"{HOST}/putaway/api/putaway/execution/container/move"
USER_DIRECTED_TRANSACTION_ID = "User Directed"

# CONFIRMED live — resolves the open (not Completed/Canceled) Putaway
# task for a container, and an iLPN's current location + on-hand
# contents when no such task exists. See search_task_id_for_container()/
# search_ilpn_current_location()/search_container_inventory() below.
ILPN_SEARCH_URL = f"{HOST}/dcinventory/api/dcinventory/ilpn/search"
INVENTORY_SEARCH_URL = f"{HOST}/dcinventory/api/dcinventory/inventory/search"

# CONFIRMED live — Tier 1 (mawm_api_library/location/api.md). Real
# putaway destinations observed in SS-DEMO (R1R20701, C1CS0110,
# C1CS0111) all carry LocationTypeId="STORAGE" — used to validate a
# user-typed "To Location" is a real storage location, per explicit
# instruction that button-gating depend on this, not just non-blank text.
LOCATION_SEARCH_URL = f"{HOST}/dcinventory/api/dcinventory/location/search"
STORAGE_LOCATION_TYPE_ID = "STORAGE"

# CONFIRMED live against SS-DEMO — the document's own
# `/api/putaway/config/services/reasonCodes/list` guess (hedged there as
# "typically GET", no method captured) 404'd; the real endpoint is the
# standard `{component}/api/{component}/{object}/search` shape every
# other confirmed object in this app uses. Verified: returns the same
# four reason codes the document's DMM-facade lookup captured (Damaged
# Location, Location Full, Aisle Congested, Default Reason code for
# putaway/RC1) — the value to send back to MAWM is `ReasonCodeId`
# (human label is `Description`), not the document's `key`/`value`
# pair (that shape belongs to the DMM facade's own lookup response,
# not this core search endpoint).
PUTAWAY_REASON_CODE_SEARCH_URL = f"{HOST}/putaway/api/putaway/reasonCode/search"

# Tier 1 (mawm_api_library/_conventions/statuses.json, domain
# "task_status") — CONFIRMED, and matches what was observed live:
# IBPWIBPT0929 read back as "3000" before completion and "8000" after.
TASK_STATUS_LABELS = {
    "1000": "Created",
    "2000": "Held",
    "3000": "Ready For Assignment",
    "5000": "Assigned",
    "7000": "In Progress",
    "7500": "Pending Complete",
    "8000": "Completed",
    "9000": "Canceled",
}

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


def task_status_description(status_id) -> str:
    """Human Task status, e.g. 'Completed'. See TASK_STATUS_LABELS."""
    if status_id in (None, ""):
        return ""
    key = str(status_id).strip()
    return TASK_STATUS_LABELS.get(key) or key


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


def search_task_id_for_container(
    container_id: str, token: str, org: str, location: str = None
) -> Optional[str]:
    """CONFIRMED live — the open (not Completed/Canceled) Putaway TaskId
    for a container, or None if there isn't one.

    Query: `(TaskDetail.SourceContainerId ='{id}' or
    TaskDetail.TargetContainerId ='{id}') and TransactionTypeId
    ='Putaway' and Status !='8000' and Status !='9000'` — the nested
    `TaskDetail.<field>` dotted-path filter on the header search is
    confirmed to work (mirrors mawm_api_library's documented
    `AsnLine.PurchaseOrderId` pattern for a different object); `Status
    not in (...)` was tried and rejected with a 400, chained `!=` works.
    Also confirmed live: filtering out TransactionTypeId narrows out
    unrelated task types on the same container (a real LPN in SS-DEMO
    had both an open Putaway task and an open "LPN Disposition" task).
    """
    token = normalize_token(token)
    quoted = container_id.replace("'", "''")
    query = (
        f"(TaskDetail.SourceContainerId ='{quoted}' or "
        f"TaskDetail.TargetContainerId ='{quoted}') and "
        f"TransactionTypeId ='Putaway' and Status !='8000' and Status !='9000'"
    )
    response = _post(
        TASK_SEARCH_URL,
        headers=build_task_headers(token, org, location=location),
        json={"Query": query, "Size": 5, "Page": 0},
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"task-by-container search failed: {response.status_code} {response.text[:500]}"
        )
    data = _response_data_list(response.json())
    return str(data[0].get("TaskId")) if data else None


def search_ilpn_current_location(
    container_id: str, token: str, org: str, location: str = None
) -> Optional[dict]:
    """CONFIRMED live (Tier 1, mawm_api_library/ilpn/api.md) — one iLPN
    row (for its CurrentLocationId), or None if the iLPN doesn't exist.
    """
    token = normalize_token(token)
    quoted = container_id.replace("'", "''")
    response = _post(
        ILPN_SEARCH_URL,
        headers=build_task_headers(token, org, location=location),
        json={
            "Query": f"IlpnId ='{quoted}'",
            "Size": 5,
            "Page": 0,
            "Template": {"IlpnId": "", "CurrentLocationId": ""},
        },
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"ilpn search failed: {response.status_code} {response.text[:500]}"
        )
    data = _response_data_list(response.json())
    return data[0] if data else None


def search_container_inventory(
    container_id: str, token: str, org: str, location: str = None
) -> List[dict]:
    """CONFIRMED live — {ItemId, OnHand} rows for a container's actual
    on-hand contents, same endpoint/shape receivingworkbench already
    uses for received-LPN summaries. More than one row means the LPN is
    mixed (more than one item).
    """
    token = normalize_token(token)
    quoted = container_id.replace("'", "''")
    response = _post(
        INVENTORY_SEARCH_URL,
        headers=build_task_headers(token, org, location=location),
        json={
            "Query": f"InventoryContainerId ='{quoted}' and InventoryContainerTypeId ='ILPN'",
            "Size": 50,
            "Page": 0,
        },
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"inventory search failed: {response.status_code} {response.text[:500]}"
        )
    return _response_data_list(response.json())


def validate_storage_location(
    location_text: str, token: str, org: str, location: str = None
) -> Optional[dict]:
    """CONFIRMED live — resolves a typed value against a real, active
    STORAGE location, matching either LocationId or DisplayLocation
    (case-insensitively handled by the caller — this does one exact-value
    query per candidate field rather than a client-side cached list,
    since Storage locations can run into the thousands org-wide, unlike
    receivingworkbench's much smaller preloaded STAGING list). Returns
    the matched row, or None if nothing matches.
    """
    token = normalize_token(token)
    quoted = location_text.replace("'", "''")
    query = (
        f"(LocationId ='{quoted}' or DisplayLocation ='{quoted}') and "
        f"LocationTypeId ='{STORAGE_LOCATION_TYPE_ID}' and IsActive=true"
    )
    response = _post(
        LOCATION_SEARCH_URL,
        headers=build_task_headers(token, org, location=location),
        json={"Query": query, "Size": 5, "Page": 0},
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"location search failed: {response.status_code} {response.text[:500]}"
        )
    data = _response_data_list(response.json())
    return data[0] if data else None


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


def fetch_putaway_move(
    task_id: str, transaction_id: str, token: str, org: str, location: str = None
) -> dict:
    """CONFIRMED-by-user-provided-capture — fetch the next putaway move for
    a task and start labor activity (Path C, Call C1 in
    mawm_putaway_api_call_set_with_warning_handling.md).

    Returns whatever move is "next" for this TaskId, not a move scoped to
    a specific TaskDetailId — task_service.complete_putaway_line() checks
    the returned CurrentTaskDetailId against the line the user actually
    selected before committing anything.
    """
    token = normalize_token(token)
    url = PUTAWAY_FETCH_MOVE_URL_TEMPLATE.format(task_id=task_id)
    payload = {
        "StartTime": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
        "TransactionId": transaction_id,
        "PutawayExecutionCriteriaId": PUTAWAY_EXECUTION_CRITERIA_ID,
        "IgnoreSourceLocationForTravel": False,
        "HandleMultiContainerForChaining": False,
        "GenerateChainingEnabledLaborMessage": False,
    }
    response = _post(url, headers=build_task_headers(token, org, location=location), json=payload)
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:1200]}
    # Bug fixed 2026-08-08: this used to raise immediately on any non-200
    # status, before the caller ever got a chance to inspect the body for
    # a WARNING (see extract_warning()) — MAWM appears to return at least
    # some warnings with a non-2xx status too (confirmed for FWTSK::019,
    # an ERROR, via HTTP 400), so a real warning here was being converted
    # into a hard failure instead of surfacing the Confirm/Cancel modal.
    # Now: always hand back the parsed body when we have one; only raise
    # when there's truly nothing usable to return.
    if not isinstance(body, dict):
        body = {"data": body}
    if response.status_code not in (200, 201) and "raw" in body and len(body) == 1:
        raise RuntimeError(
            f"fetchNextPutawayMoveAndStartLaborActivity failed: {response.status_code} {response.text[:800]}"
        )
    return body


def commit_putaway_move(
    sub_type: str,
    inventory_move: dict,
    token: str,
    org: str,
    location: str = None,
    warning_overrides: Optional[Dict[str, str]] = None,
) -> dict:
    """CONFIRMED-by-user-provided-capture — commit a fetched putaway move
    (Path C, Call C2). `inventory_move` should already have its
    CompletedQuantity set by the caller (full vs. partial completion is
    decided by that value, per the document's worked example — the
    fetched move itself doesn't set it).

    `warning_overrides`, if given, is sent as a top-level `userInputs`
    map ({code: code}) — UNCONFIRMED extension of the document's Path A
    warning-override pattern to this core endpoint. The document is
    explicit that Path C's warning contract hasn't been confirmed to
    match Path A's DMM workflowVO shape; if a real warning response from
    this endpoint doesn't look like what extract_warning() expects, or a
    resubmitted `userInputs` doesn't actually clear the warning, this is
    the one piece to correct next.
    """
    token = normalize_token(token)
    payload = {"SubType": sub_type, "InventoryMove": inventory_move}
    if warning_overrides:
        payload["userInputs"] = warning_overrides
    response = _post(
        PUTAWAY_COMMIT_MOVE_URL,
        headers=build_task_headers(token, org, location=location),
        json=payload,
    )
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:1200]}
    # See fetch_putaway_move()'s comment — same fix, same reason: don't
    # raise before the caller can check the body for a WARNING.
    if not isinstance(body, dict):
        body = {"data": body}
    if response.status_code not in (200, 201) and "raw" in body and len(body) == 1:
        raise RuntimeError(
            f"commitAndFetchNextMove failed: {response.status_code} {response.text[:800]}"
        )
    body["_requestPayload"] = payload
    return body


def move_container_user_directed(
    container_id: str,
    to_location_id: str,
    item_id: str,
    quantity,
    token: str,
    org: str,
    location: str = None,
    warning_overrides: Optional[Dict[str, str]] = None,
) -> dict:
    """UNCONFIRMED — user-directed putaway to an operator-chosen
    destination, per mawm_user_directed_putaway_with_warnings.md's core
    API alternative. See PUTAWAY_USER_DIRECTED_MOVE_URL's comment for why
    this (not the document's proven DMM Mobile Facade flow) was chosen.

    Task-independent by design (per the document: "no task DTO, no task
    ID, and no allocation ID in the active move") — takes ContainerId/
    ItemId/Quantity/ToLocationId directly, unlike the system-directed
    Path C flow which is keyed by TaskId. This is exactly why it was
    revived for task_service.complete_container_putaway(): the no-open-
    task iLPN case has no TaskId to key off of either.

    `warning_overrides`, if given, is sent the same way as
    commit_putaway_move()'s — a top-level `userInputs` map — which is
    itself an unconfirmed extrapolation there; doubly so here, since
    this endpoint's own warning contract is explicitly unconfirmed by
    the source document.
    """
    token = normalize_token(token)
    payload = {
        "ContainerId": container_id,
        "ToLocationId": to_location_id,
        "TransactionId": USER_DIRECTED_TRANSACTION_ID,
        "ItemId": item_id,
        "ScannedQty": quantity,
    }
    if warning_overrides:
        payload["userInputs"] = warning_overrides
    response = _post(
        PUTAWAY_USER_DIRECTED_MOVE_URL,
        headers=build_task_headers(token, org, location=location),
        json=payload,
    )
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:1200]}
    # See fetch_putaway_move()'s comment — same fix, same reason. This is
    # the exact function LPN00953's Substitute-Location-with-no-open-task
    # test hit: a real WARNING came back over a non-2xx status and was
    # being raised as a hard error before extract_warning() ever saw it.
    if not isinstance(body, dict):
        body = {"data": body}
    if response.status_code not in (200, 201) and "raw" in body and len(body) == 1:
        raise RuntimeError(
            f"user-directed putaway move failed: {response.status_code} {response.text[:800]}"
        )
    body["_requestPayload"] = payload
    return body


def search_putaway_reason_codes(token: str, org: str, location: str = None) -> List[dict]:
    """CONFIRMED live — Substitute Location reason codes, via the
    standard search convention (see PUTAWAY_REASON_CODE_SEARCH_URL's
    comment for why this replaced the document's original guess).
    Each row's `ReasonCodeId` is the value to send back to MAWM;
    `Description` is the human label.
    """
    token = normalize_token(token)
    payload = {"Query": "", "Size": 1000, "Page": 0}
    response = _post(
        PUTAWAY_REASON_CODE_SEARCH_URL,
        headers=build_task_headers(token, org, location=location),
        json=payload,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"reason code search failed: {response.status_code} {response.text[:500]}"
        )
    return _response_data_list(response.json())


def extract_warning(body) -> Optional[Dict[str, str]]:
    """Best-effort scan for an overrideable WARNING in a MAWM response.

    Checks two shapes: the standard MAWM `messages.Message[]` envelope
    (seen on every confirmed search endpoint in this app), and the DMM
    workflowVO `header.state.errorVOList` shape documented in
    mawm_putaway_api_call_set_with_warning_handling.md's Path A example
    (PTW::120). Which shape Path C's core commit endpoint actually uses
    for a warning has not been confirmed either way — this defensively
    checks both rather than assuming one.
    """
    if not isinstance(body, dict):
        return None

    for msg in ((body.get("messages") or {}).get("Message") or []):
        if not isinstance(msg, dict):
            continue
        category = str(msg.get("Type") or msg.get("Category") or "").upper()
        if category == "WARNING":
            return {
                "code": str(msg.get("Code") or msg.get("MessageKey") or ""),
                "text": str(msg.get("Description") or msg.get("Message") or ""),
            }

    workflow_vo = body.get("workflowVO")
    if isinstance(workflow_vo, dict):
        state = ((workflow_vo.get("header") or {}).get("state") or {})
        for err in state.get("errorVOList") or []:
            if isinstance(err, dict) and str(err.get("errorCategory") or "").upper() == "WARNING":
                return {
                    "code": str(err.get("errorCode") or ""),
                    "text": str(err.get("errorMessage") or ""),
                }

    return None


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
