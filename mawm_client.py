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
from urllib.parse import quote

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

# SUPERSEDED AGAIN 2026-08-08 — this task-independent core API call's own
# warning-override mechanism was confirmed live NOT to work (resubmitting
# with userInputs against a real DCI::120 warning returned the identical
# warning again). Kept defined (not commented out — still called from the
# commented-out orchestration below) since the URL/payload shape itself
# is still correct, just superseded by the DMM Mobile Facade flow for
# the no-open-task iLPN case — see PUTAWAY_WORKFLOW_INIT_URL below and
# task_service.complete_container_putaway().
PUTAWAY_USER_DIRECTED_MOVE_URL = f"{HOST}/putaway/api/putaway/execution/container/move"
USER_DIRECTED_TRANSACTION_ID = "User Directed"

# CONFIRMED live (2026-08-08 HAR capture of a real mobile RF session,
# start to finish) — the DMM Mobile Facade "User Directed Putaway" flow.
# Both source documents said the bootstrap/init call "is not present in
# the captured responses, do not invent one" — it's this, a genuinely
# different URL shape from workflow/execute (under .../api/dmmobile-
# facade/, not .../services/rest/), body is a literal `{}`, everything
# driven by query params. The full confirmed sequence:
#   1. workflow_init("User Directed", "Putaway") -> fresh workflowVO,
#      currentState="AcceptContainerForUserDirectedPutaway".
#   2. Set that workflowVO's header.state.scannedContainerBarcode =
#      <container>, then workflow_execute(..., "AcceptContainerFor
#      UserDirectedPutaway", that workflowVO).
#   3. Set the PRIOR call's returned workflowVO's
#      header.state.scannedLocationBarcode = <location>, then
#      workflow_execute(..., "AcceptLocationForUserDirectedPutaway",
#      that workflowVO).
# CONFIRMED via HAR body inspection (2026-08-08): the scanned value is
# NOT a separate top-level sibling field in the request body (an
# earlier guess assumed `{"workflowVO": ..., "scannedContainerBarcode":
# ...}` and it silently produced a generic serverError) — it lives
# inside `workflowVO.header.state`, mutated in place exactly like
# warningOverrideList below.
# A warning at either step 2 or 3 comes back as `status: "FAILURE"` with
# `workflowVO.header.state.errorVOList` containing an entry with
# `errorCategory: "WARNING"` (extract_warning() already handles this
# shape). CONFIRMED live exactly how to clear it: take the SAME
# workflowVO the warning response returned, add the warning's errorCode
# into `header.state.warningOverrideList`, and resubmit that whole
# object back to the SAME action — nothing else changes. No separate
# override field, no session store needed on our side (see
# apply_warning_overrides() and task_service.complete_container_putaway()
# for how this app replays the whole sequence per attempt instead of
# round-tripping the intermediate workflowVO through the frontend).
PUTAWAY_WORKFLOW_INIT_URL = f"{HOST}/dmmobile-facade/api/dmmobile-facade/workflow/init"
PUTAWAY_WORKFLOW_EXECUTE_URL_TEMPLATE = (
    f"{HOST}/dmmobile-facade/services/rest/workflow/execute/"
    "workflowScriptName/{script}/stateName/{state}/actionName/{action}"
)
PUTAWAY_WORKFLOW_SCRIPT_NAME = "Putaway"

# CONFIRMED live — resolves the open (not Completed/Canceled) Putaway
# task for a container, and an iLPN's current location + on-hand
# contents when no such task exists. See search_task_id_for_container()/
# search_ilpn_current_location()/search_container_inventory() below.
ILPN_SEARCH_URL = f"{HOST}/dcinventory/api/dcinventory/ilpn/search"
INVENTORY_SEARCH_URL = f"{HOST}/dcinventory/api/dcinventory/inventory/search"

# CONFIRMED-by-user-capture (mawm_lpn_history_api_and_selection_logic.md,
# 2026-08-09) — DMUI Search's Activity Tracking view, the only place
# that still has an item/quantity/target-location record for a
# *consumed* LPN (its own CurrentLocationId/on-hand come back empty
# once consumed — see ILPN_CONSUMED_STATUS's comment). Filtering by
# ContainerId alone returns the LPN's complete event history,
# unfiltered by item/transaction/date — see
# task_service.select_terminal_ilpn_activity() for the confirmed
# selection logic (group duplicate rows, then pick the terminal
# movement) used to turn this into "what item, how much, and where did
# it end up."
ILPN_ACTIVITY_HISTORY_URL = f"{HOST}/dmui-search/api/dmui-search/entity/search"
ILPN_ACTIVITY_HISTORY_VIEW_NAME = "dmui_activitytracking"
ILPN_ACTIVITY_HISTORY_COMPONENT_NAME = "com-manh-cp-dmui-search"

# CONFIRMED-by-user-capture (mawm_modify_ilpn_query_and_adjustment.md,
# 2026-08-08) — Modify iLPN: correct an LPN's actual on-hand quantity
# directly (not via a task move), used ahead of a putaway completion
# when the operator's Completed Qty differs from what was planned. See
# task_service.adjust_ilpn_quantities() for the full sequence/decision
# table this backs. Verified against a *non-allocated* LPN in Postman
# by the document's author; behavior against an LPN already on an open
# task is UNCONFIRMED — see that function's docstring.
INVENTORY_READ_TIMESTAMP_REFRESH_URL = (
    f"{HOST}/dcinventory/api/dcinventory/inventory/updateReadTimeStamp"
)
MODIFY_ILPN_ADJUST_URL = f"{HOST}/inventory-management/api/inventory-management/adjust/endIlpn"
MODIFY_ILPN_TRANSACTION_ID = "Modify iLPN"

# Static list, not a live search (2026-08-08) — extracted from a real
# captured lookup response (modifyLPN-single.har's
# adjustmentReasonCodes/list call), which only ever appeared nested
# inside an active DMM ModifyIlpn workflow session in the capture.
# Building that whole workflow just to fetch six stable codes was
# explicitly out of scope; revisit if a core-API equivalent search
# endpoint is ever found, same as how Putaway's own reason codes moved
# from a static guess to a confirmed live search earlier in this app.
ADJUSTMENT_REASON_CODES = [
    {"key": "Charity", "value": "CH"},
    {"key": "Inventory Adjustment", "value": "IA"},
    {"key": "Inventory Damaged", "value": "DM"},
    {"key": "Inventory Delete", "value": "ID"},
    {"key": "Lost in cycle count", "value": "LC"},
    {"key": "Mass Inventory Movement", "value": "MM"},
]
DEFAULT_ADJUSTMENT_REASON_CODE = "IA"

# CONFIRMED live — Tier 1 (mawm_api_library/location/api.md). Real
# putaway destinations observed in SS-DEMO (R1R20701, C1CS0110,
# C1CS0111) all carry LocationTypeId="STORAGE" — used to validate a
# user-typed "To Location" is a real storage location, per explicit
# instruction that button-gating depend on this, not just non-blank text.
# NOTE (2026-08-08, sixth session): LocationTypeId="STORAGE" does NOT
# imply LPN-level inventory tracking — see InventoryReservationTypeId
# below, a genuinely independent field. C1CS0110 is LocationTypeId=
# STORAGE but InventoryReservationTypeId=LOCATION (putaway there
# consumes the LPN); an earlier note here claiming it "tracks LPNs" was
# wrong.
LOCATION_SEARCH_URL = f"{HOST}/dcinventory/api/dcinventory/location/search"
STORAGE_LOCATION_TYPE_ID = "STORAGE"

# CONFIRMED live, 2026-08-08 (sixth session) — the real signal for
# task_service.complete_putaway_line()'s decision between adjusting the
# LPN (adjust_ilpn_quantities()) vs. the destination location
# (adjust_location_quantities()) after completing a putaway. A
# location's own InventoryReservationTypeId ("LPN" vs "LOCATION",
# dcinventory/location/search) predicts it, but the ground-truth check
# used here is simpler and doesn't require a separate location lookup:
# after putaway, re-query the LPN itself (search_ilpn_current_location(),
# Template extended to include Status) — Status == "9000" means MAWM
# already consumed it into the destination's own location-level
# inventory (CurrentLocationId comes back null at that point), matching
# mawm_api_library's ilpn_dc_inventory_status domain ("9000" =
# "Consumed"; contrast "3000" = "Not Allocated", seen on every LPN that
# stayed a live container after putaway in this app's testing so far).
ILPN_CONSUMED_STATUS = "9000"

# CONFIRMED-by-user-capture (mawm_adjust_location_api.md, 2026-08-08,
# sixth session) — the fallback for exactly the case above: once an LPN
# is consumed, there's no more discrete LPN-level inventory record to
# run Modify iLPN against, so the destination *location's own*
# inventory record for the item has to be adjusted directly instead.
# Confirmed test sequence: (1) POST dcinventory/inventory/search with a
# LocationId-only Query to read the location's current inventory
# detail (OnHand, Allocated, ConsumptionPriorityDate, distinguishing
# attributes, all needed to build step 2's payload) — see
# search_location_inventory() below, same endpoint/shape
# search_container_inventory() already uses, just queried by LocationId
# instead of InventoryContainerId. (2) POST adjustInventory (a genuinely
# different endpoint from Modify iLPN's endIlpn) with a **raw JSON
# array**, not an object wrapper — the document notes an object wrapper
# causes a NullPointerException. Delta-based, not absolute:
# `New OnHand = Current OnHand + Quantity` — the caller must convert an
# absolute desired quantity into a signed delta before building the
# payload (contrast Modify iLPN's ScannedQuantity, which collapses to
# an absolute value in this app's usage since Original==Expected==
# current there; this endpoint has no such shortcut).
# `AddItemRemainder`/`RemoveItemRemainder` must agree with the delta's
# sign. `InventoryReadOnHand` is this endpoint's optimistic-concurrency
# check (parallel to Modify iLPN's InventoryReadTimestamp) — a stale
# value returns `DCI::313`, requiring a fresh location-inventory query
# and a resubmit with the current OnHand/attributes.
# **UNCONFIRMED**: the document only ever describes DCI::313 in prose
# ("MAWM reports that the inventory record changed") — no captured JSON
# error response body for it exists to confirm the exact shape
# extract_message()/extract_warning() would need to recognize it
# reliably; see adjust_location_inventory()'s docstring for how this is
# handled defensively pending a real capture of that error case.
ADJUST_LOCATION_INVENTORY_URL = f"{HOST}/dcinventory/api/dcinventory/inventory/adjustInventory"
LOCATION_ADJUSTMENT_STALE_RECORD_CODE = "DCI::313"

# Cycle Count (ad hoc, "USER_DIRECTED" mode) — ported from the sibling
# `cyclecount` app (C:\Users\ssmith\Personal\Development\work\
# cyclecount, v1.3.1), which live-tests this exact six-call chain
# against SS-DEMO via its own file-upload workflow. Same host/auth as
# everything else in this app, so no new credentials — just a
# different MAWM component (`inventory-management`, not `putaway`/
# `task`). Per-location sequence: initiateCount() -> (item-from-
# location lookup, reuses search_location_inventory() rather than
# duplicating the old app's own dcinventory query) ->
# validate_item_and_get_item_details() -> accept_quantity() ->
# persist_count_details() -> end_count(). accept_quantity() is the one
# call that can come back with a `messages.Message[]` WARNING (a
# quantity-mismatch is auto-overridden, per explicit instruction
# matching the old app's own silent-continue behavior) rather than a
# hard failure.
CYCLE_COUNT_INITIATE_URL = f"{HOST}/inventory-management/api/inventory-management/count/initiateCount"
CYCLE_COUNT_VALIDATE_ITEM_URL = f"{HOST}/inventory-management/api/inventory-management/count/validateItemAndGetItemDetails"
CYCLE_COUNT_ACCEPT_QUANTITY_URL = f"{HOST}/inventory-management/api/inventory-management/count/acceptQuantity"
CYCLE_COUNT_PERSIST_URL = f"{HOST}/inventory-management/api/inventory-management/count/quantity/persistCountDetails"
CYCLE_COUNT_END_URL = f"{HOST}/inventory-management/api/inventory-management/count/end"
CYCLE_COUNT_TRIGGER_END_URL = f"{HOST}/inventory-management/api/inventory-management/count/endCount/trigger"
CYCLE_COUNT_CRITERIA_ID = "Cycle Count Active-API Mode"
CYCLE_COUNT_TRANSACTION_ID = "Cycle Count Active-API"
CYCLE_COUNT_TRANSACTION_TYPE_ID = "Cycle Count"
CYCLE_COUNT_QUANTITY_MISMATCH_TEXT = "quantity mismatch"

# CONFIRMED-by-user-capture (mawm_cycle_count_location_investigation.md,
# 2026-08-08, ninth session) — the booking outcome of a submitted count
# is NOT knowable from acceptQuantity/persistCountDetails/endCount's own
# responses; MAWM books it (or fails to) asynchronously afterward. These
# three read-only endpoints are how to observe the real outcome:
# locationCountInfo (the location-level "is a count pending" lock flag),
# inventoryCountRun (one row per count attempt for a location, Status/
# TaskId/BookedDateTime), inventoryCountResult (the flat item-level
# detail for one run: OriginalQuantity/CountQuantity/VarianceQuantity/
# Status/BookingFailureReason). See task_service.complete_cycle_count_line()
# for how these get polled after persist/end to determine the real
# result instead of trusting the chain's own HTTP statuses.
LOCATION_COUNT_INFO_SEARCH_URL = f"{HOST}/dcinventory/api/dcinventory/locationCountInfo/search"
INVENTORY_COUNT_RUN_SEARCH_URL = f"{HOST}/inventory-management/api/inventory-management/inventoryCountRun/search"
INVENTORY_COUNT_RESULT_SEARCH_URL = f"{HOST}/inventory-management/api/inventory-management/inventoryCountResult/search"

# Tier 1 (CONFIRMED live, mawm_cycle_count_location_investigation.md +
# live capture this session) — inventoryCountRun.Status / .StatusKey.
# 35/70/75/80 confirmed by the user's captured document; 65 ("Booking
# Failed") confirmed live this session (a real concurrency conflict:
# "Cycle count already in progress for different inventory read", from
# running two count attempts against the same location within seconds
# of each other — not expected in normal single-count-per-visit usage).
INVENTORY_COUNT_RUN_STATUS_LABELS = {
    "35": "Pending Booking",
    "65": "Booking Failed",
    "70": "Booking Rejected",
    "75": "Booking Accepted",
    "80": "Booked",
}

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

# mawm_api_library's `ilpn_dc_inventory_status` domain (confirmed live,
# 2026-08-08, sixth session — see ILPN_CONSUMED_STATUS's comment above
# for how "9000" first got confirmed). A different status ladder from
# TASK_STATUS_LABELS above and from receivingworkbench's own
# LPN_STATUS_LABELS (that one's the *receiving-component* ASN-nested
# LpnStatus domain, not this one — mawm_api_library is explicit that
# the two must not be merged; same numeric codes mean different things).
ILPN_STATUS_LABELS = {
    "1000": "In Transit",
    "2000": "Pre-Receipt Allocated",
    "3000": "Not Allocated",
    "4000": "Partially Allocated",
    "5000": "Allocated",
    "9000": "Consumed",
    "10000": "Lost",
    "11000": "Canceled",
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


def build_cycle_count_headers(token: str, org: str, location: str = None) -> dict:
    """Cycle Count endpoints additionally require an explicit `FacilityId`
    header (confirmed live in the sibling `cyclecount` app) — every
    other header matches build_task_headers() exactly, so this just
    layers that one extra header on top instead of duplicating the rest.
    """
    headers = build_task_headers(token, org, location=location)
    headers["FacilityId"] = headers["selectedLocation"]
    return headers


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


def ilpn_status_description(status_id) -> str:
    """Human iLPN status, e.g. 'Consumed'. See ILPN_STATUS_LABELS."""
    if status_id in (None, ""):
        return ""
    key = str(status_id).strip()
    return ILPN_STATUS_LABELS.get(key) or key


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

    Template extended 2026-08-08 to also fetch `Status` — see
    `ILPN_CONSUMED_STATUS`'s comment above for why: this is the signal
    task_service.complete_putaway_line() uses to tell whether an LPN
    is still a live container after putaway, or was consumed into the
    destination location's own inventory. Also fetches
    `PreviousLocationId` (2026-08-08, seventh session) — confirmed
    live on two real consumed LPNs that this holds the location it was
    consumed *into* (`CurrentLocationId` comes back null once
    consumed, so there'd otherwise be nothing to show as "where is
    it") — see task_service.resolve_search()'s no_task branch for how
    the frontend displays this, marked, since it's not technically
    where the LPN "is" anymore.
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
            "Template": {
                "IlpnId": "",
                "CurrentLocationId": "",
                "PreviousLocationId": "",
                "Status": "",
            },
        },
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"ilpn search failed: {response.status_code} {response.text[:500]}"
        )
    data = _response_data_list(response.json())
    return data[0] if data else None


def search_ilpn_activity_history(container_id: str, token: str, org: str, location: str = None) -> List[dict]:
    """CONFIRMED-by-user-capture — every Activity Tracking record for
    one LPN, filtered only by ContainerId (no item/transaction/date
    filter, per the source document — deliberately returns the
    complete history so the caller can apply its own selection logic,
    see task_service.select_terminal_ilpn_activity()). A different
    shape from every other search in this app: `Filters`/`ViewName`
    instead of `Query`/`Template`, and the real rows live under
    `data.Results`, not the usual `data` list directly — DMUI Search is
    a different MAWM component (search/reporting over Elasticsearch-
    backed activity data) from dcinventory/task/inventory-management.
    `TimeZone` is hardcoded to America/New_York, matching the source
    document's own capture — unconfirmed whether/how this affects the
    result content versus just display formatting, but it isn't a
    filter dimension so it's assumed inert.
    """
    token = normalize_token(token)
    response = _post(
        ILPN_ACTIVITY_HISTORY_URL,
        headers=build_task_headers(token, org, location=location),
        json={
            "ViewName": ILPN_ACTIVITY_HISTORY_VIEW_NAME,
            "Filters": [
                {
                    "ViewName": ILPN_ACTIVITY_HISTORY_VIEW_NAME,
                    "AttributeId": "ContainerId",
                    "FilterValues": [container_id],
                }
            ],
            "SortIndicator": "chevron-up",
            "TimeZone": "America/New_York",
            "ComponentName": ILPN_ACTIVITY_HISTORY_COMPONENT_NAME,
        },
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"ilpn activity history search failed: {response.status_code} {response.text[:500]}"
        )
    body = response.json()
    data = body.get("data") if isinstance(body, dict) else None
    results = (data or {}).get("Results") if isinstance(data, dict) else None
    return results if isinstance(results, list) else []


def search_ilpn_statuses(
    ilpn_ids: List[str], token: str, org: str, location: str = None
) -> Dict[str, dict]:
    """Batched sibling of search_ilpn_current_location() (2026-08-08,
    seventh session) — one call for several LPNs' Status/
    CurrentLocationId/PreviousLocationId at once (same `ItemId in (...)`
    batching convention search_items() already uses), instead of one
    call per line. Used to show every task-mode line's own LPN status
    badge too (per explicit instruction — "always display LPN status"),
    not just the no-task/container search path, which already did a
    single-LPN lookup. Returns `{IlpnId: row}`; an id with no match is
    simply absent from the result.
    """
    clean = [str(i).strip() for i in ilpn_ids if str(i).strip()]
    if not clean:
        return {}
    token = normalize_token(token)
    quoted = ", ".join(f"'{i.replace(chr(39), chr(39) + chr(39))}'" for i in clean)
    response = _post(
        ILPN_SEARCH_URL,
        headers=build_task_headers(token, org, location=location),
        json={
            "Query": f"IlpnId in ({quoted})",
            "Size": max(len(clean), 50),
            "Page": 0,
            "Template": {
                "IlpnId": "",
                "CurrentLocationId": "",
                "PreviousLocationId": "",
                "Status": "",
            },
        },
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"ilpn search failed: {response.status_code} {response.text[:500]}"
        )
    data = _response_data_list(response.json())
    return {str(row.get("IlpnId")): row for row in data if row.get("IlpnId")}


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


def search_location_inventory(
    location_id: str, token: str, org: str, location: str = None
) -> List[dict]:
    """CONFIRMED-by-user-capture (mawm_adjust_location_api.md,
    2026-08-08, sixth session) — step 1 of the Adjust Location flow:
    every inventory detail row directly at `location_id` (not scoped to
    any container — this is for location-level inventory, the case
    where putaway has consumed the LPN into the location's own record).
    Same endpoint/shape as search_container_inventory(), just queried
    by LocationId instead of InventoryContainerId. More than one row
    for the same ItemId means distinguishing attributes (batch, product
    status, ConsumptionPriorityDate, etc.) matter for picking the right
    one — see adjust_location_quantities()'s docstring for how (or
    rather, whether) this app disambiguates that today.
    """
    token = normalize_token(token)
    quoted = location_id.replace("'", "''")
    response = _post(
        INVENTORY_SEARCH_URL,
        headers=build_task_headers(token, org, location=location),
        json={
            "Query": f"LocationId ='{quoted}'",
            "Size": 20,
            "Page": 0,
        },
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"location inventory search failed: {response.status_code} {response.text[:500]}"
        )
    return _response_data_list(response.json())


def adjust_location_inventory(inventory_lines: List[dict], token: str, org: str, location: str = None) -> dict:
    """CONFIRMED-by-user-capture (mawm_adjust_location_api.md,
    2026-08-08, sixth session) — step 2: apply a delta-based adjustment
    directly to a location's own inventory record for an item, via the
    core DCI endpoint (a different one from Modify iLPN's endIlpn — no
    IlpnId/TransactionId wrapper here at all). `inventory_lines` are
    dicts already shaped like the document's payload (SourceContainerId/
    SourceContainerType="LOCATION"/SourceLocationId, ItemId, ReasonCode,
    the distinguishing attributes, AddItemRemainder/RemoveItemRemainder,
    Quantity as a signed delta, InventoryReadOnHand) — see
    task_service.adjust_location_quantities() for how those are built.

    **The request body is the raw list itself, not `{"...": [...]}`** —
    the document is explicit that wrapping it in an object causes a
    server-side `NullPointerException`.

    **UNCONFIRMED**: the document only describes the `DCI::313`
    stale-record error in prose ("MAWM reports that the inventory
    record changed"), with no captured JSON error response body — so
    the exact shape isn't confirmed the way most error handling in this
    app is. This always hands back the parsed response body (never
    raises except when genuinely unparseable, same non-raise-on-non-2xx
    pattern as every other warning-capable call in this app) and lets
    the caller inspect it — adjust_location_quantities() checks the
    stringified body for `LOCATION_ADJUSTMENT_STALE_RECORD_CODE` as a
    best-effort match pending a real capture of this error case.
    """
    token = normalize_token(token)
    response = _post(
        ADJUST_LOCATION_INVENTORY_URL,
        headers=build_task_headers(token, org, location=location),
        json=inventory_lines,
    )
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:1200]} if response.text else {}
    if not isinstance(body, dict):
        body = {"data": body}
    if response.status_code not in (200, 201) and "raw" in body and len(body) == 1:
        raise RuntimeError(
            f"adjustInventory failed: {response.status_code} {response.text[:800]}"
        )
    body["_httpStatus"] = response.status_code
    return body


def _parse_cycle_count_response(response: requests.Response, label: str) -> dict:
    """Shared response handling for all six Cycle Count calls below.
    Mirrors the old cyclecount app's Flask layer: always try to parse
    JSON regardless of status code (accept_quantity() in particular can
    come back 400 with a genuine `messages.Message[]` warning body, not
    an actual failure), only raising when the body is truly unparseable
    AND the status was non-2xx. Callers inspect `_httpStatus` themselves
    to decide success — a 2xx status here doesn't guarantee MAWM-level
    success any more than it does elsewhere in this app.
    """
    try:
        body = response.json()
    except Exception:
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"{label} failed: {response.status_code} {response.text[:500]}"
            )
        body = {"raw": response.text[:500]} if response.text else {}
    if not isinstance(body, dict):
        body = {"data": body}
    body["_httpStatus"] = response.status_code
    return body


def initiate_count(location_id: str, token: str, org: str, location: str = None) -> dict:
    """Ported from cyclecount/api/index.py's initiate_count() — step 1
    of the chain. Response carries `CountRunId`/`TaskId` (exact casing/
    nesting unconfirmed in this app until live-tested; the old app
    defensively checks several shapes — see
    task_service.complete_cycle_count_line() for that extraction).
    """
    token = normalize_token(token)
    payload = {
        "LocationId": location_id,
        "CountMode": "USER_DIRECTED",
        "CountSequence": 1,
        "LpnTracking": False,
        "CountCriteriaId": CYCLE_COUNT_CRITERIA_ID,
        "TransactionId": CYCLE_COUNT_TRANSACTION_ID,
        "TaskIntegrationDTO": {
            "TransactionId": CYCLE_COUNT_TRANSACTION_ID,
            "TransactionTypeId": CYCLE_COUNT_TRANSACTION_TYPE_ID,
            "LaborActivityId": CYCLE_COUNT_TRANSACTION_TYPE_ID,
        },
    }
    response = _post(
        CYCLE_COUNT_INITIATE_URL,
        headers=build_cycle_count_headers(token, org, location=location),
        json=payload,
    )
    return _parse_cycle_count_response(response, "initiateCount")


def validate_item_and_get_item_details(
    location_id: str, count_run_id: str, item_id: str, token: str, org: str, location: str = None
) -> dict:
    """Ported from cyclecount/api/index.py's validate_item_and_get_item_details() — step 3."""
    token = normalize_token(token)
    payload = {
        "LocationId": location_id,
        "CountRunId": count_run_id,
        "LpnTracking": False,
        "ContainerId": None,
        "TransactionId": CYCLE_COUNT_TRANSACTION_ID,
        "CriteriaId": CYCLE_COUNT_CRITERIA_ID,
        "ItemAttributeDTO": {"Item": item_id},
        "TransactionTypeId": CYCLE_COUNT_TRANSACTION_TYPE_ID,
    }
    response = _post(
        CYCLE_COUNT_VALIDATE_ITEM_URL,
        headers=build_cycle_count_headers(token, org, location=location),
        json=payload,
    )
    return _parse_cycle_count_response(response, "validateItemAndGetItemDetails")


def accept_quantity(
    location_id: str,
    count_run_id: str,
    task_id: str,
    item_id: str,
    quantity,
    token: str,
    org: str,
    location: str = None,
) -> dict:
    """Ported from cyclecount/api/index.py's accept_quantity() — step 4,
    the one call that can legitimately come back non-2xx with a real
    `messages.Message[]` warning (quantity mismatch) rather than a hard
    failure; see task_service.complete_cycle_count_line() for how that's
    distinguished from an actual error.
    """
    token = normalize_token(token)
    payload = {
        "LocationId": location_id,
        "CountRunId": count_run_id,
        "ContainerType": None,
        "ContainerId": None,
        "Quantity": quantity,
        "BlindIlpn": False,
        "TransactionId": CYCLE_COUNT_TRANSACTION_ID,
        "TaskId": task_id,
        "CriteriaId": CYCLE_COUNT_CRITERIA_ID,
        "ItemAttributeDTO": {"Item": item_id, "CompareAttributes": False},
        "TaskIntegrationDTO": {
            "TransactionId": CYCLE_COUNT_TRANSACTION_ID,
            "TransactionTypeId": CYCLE_COUNT_TRANSACTION_TYPE_ID,
            "LaborActivityId": CYCLE_COUNT_TRANSACTION_TYPE_ID,
        },
    }
    response = _post(
        CYCLE_COUNT_ACCEPT_QUANTITY_URL,
        headers=build_cycle_count_headers(token, org, location=location),
        json=payload,
    )
    return _parse_cycle_count_response(response, "acceptQuantity")


def persist_count_details(
    location_id: str,
    count_run_id: str,
    task_id: str,
    item_id: str,
    quantity,
    token: str,
    org: str,
    location: str = None,
) -> dict:
    """Ported from cyclecount/api/index.py's persist_count_details() — step 5."""
    token = normalize_token(token)
    payload = {
        "LocationId": location_id,
        "CountRunId": count_run_id,
        "Quantity": quantity,
        "TaskId": task_id,
        "ContainerId": None,
        "ContainerType": "LOCATION",
        "TransactionId": CYCLE_COUNT_TRANSACTION_ID,
        "BlindIlpn": False,
        "CriteriaId": CYCLE_COUNT_CRITERIA_ID,
        "ItemAttributeDTO": {"Item": item_id, "CompareAttributes": False},
        "TaskIntegrationDTO": {
            "TransactionId": CYCLE_COUNT_TRANSACTION_ID,
            "TransactionTypeId": CYCLE_COUNT_TRANSACTION_TYPE_ID,
            "LaborActivityId": CYCLE_COUNT_TRANSACTION_TYPE_ID,
        },
    }
    response = _post(
        CYCLE_COUNT_PERSIST_URL,
        headers=build_cycle_count_headers(token, org, location=location),
        json=payload,
    )
    return _parse_cycle_count_response(response, "persistCountDetails")


def end_count(location_id: str, count_run_id: str, token: str, org: str, location: str = None) -> dict:
    """Ported from cyclecount/api/index.py's end_count() — step 6, closes the count run."""
    token = normalize_token(token)
    payload = {
        "LocationId": location_id,
        "CountRunId": count_run_id,
        "CountSequence": 1,
        "LpnTracking": False,
        "TransactionId": CYCLE_COUNT_TRANSACTION_ID,
        "CriteriaId": CYCLE_COUNT_CRITERIA_ID,
        "TaskIntegrationDTO": {
            "TransactionId": CYCLE_COUNT_TRANSACTION_ID,
            "TransactionTypeId": CYCLE_COUNT_TRANSACTION_TYPE_ID,
            "LaborActivityId": CYCLE_COUNT_TRANSACTION_TYPE_ID,
        },
    }
    response = _post(
        CYCLE_COUNT_END_URL,
        headers=build_cycle_count_headers(token, org, location=location),
        json=payload,
    )
    return _parse_cycle_count_response(response, "endCount")


def trigger_end_count(
    location_id: str, count_run_id: str, task_id: str, token: str, org: str, location: str = None
) -> dict:
    """CONFIRMED live 2026-08-09 against a real WM-scheduled Cycle Count
    task (`CCNTINM000023`) — a task-aware replacement for end_count().
    `end_count()` books the inventory count but never closes the
    underlying Task Management record (confirmed stuck at
    `Status: "7000"` for 15s+ of polling); this endpoint does both.
    Verified via independent re-query afterward (never trust the 200
    response alone): `Task.Status` changed `7000` -> `8000` ("Completed"),
    stable across 18s of polling, with the item-carrying `TaskDetail`
    row's `CompletedQuantity` equal to `Quantity`. Note the task has TWO
    `TaskDetail` rows — a placeholder one (no ItemId) that never gets
    populated, and the real one (matches the count's ItemId) that does;
    don't check TaskDetail[0] alone.

    Only tested so far by calling this directly against an
    already-booked run (to isolate task-closure behavior) — not yet
    tested as the actual step-5 replacement in a fresh chain, and not
    yet tested against an ad hoc (system-pool) TaskId. Safe to assume
    task_id is always available here since every caller already
    extracts it from initiateCount's response.
    """
    token = normalize_token(token)
    payload = {
        "LocationId": location_id,
        "CountRunId": count_run_id,
        "TaskId": task_id,
        "CountSequence": 1,
        "CountMode": "USER_DIRECTED",
        "LpnTracking": False,
        "NumberOfLPNs": 0,
        "TransactionId": CYCLE_COUNT_TRANSACTION_ID,
        "CriteriaId": CYCLE_COUNT_CRITERIA_ID,
        "CountCriteriaId": CYCLE_COUNT_CRITERIA_ID,
        "TaskIntegrationDTO": {
            "TransactionId": CYCLE_COUNT_TRANSACTION_ID,
            "TransactionTypeId": CYCLE_COUNT_TRANSACTION_TYPE_ID,
            "LaborActivityId": CYCLE_COUNT_TRANSACTION_TYPE_ID,
            "LocationId": location_id,
            "TaskId": task_id,
        },
    }
    response = _post(
        CYCLE_COUNT_TRIGGER_END_URL,
        headers=build_cycle_count_headers(token, org, location=location),
        json=payload,
    )
    return _parse_cycle_count_response(response, "triggerEndCount")


def search_location_count_info(location_id: str, token: str, org: str, location: str = None) -> Optional[dict]:
    """CONFIRMED-by-user-capture — the location-level cycle-count lock
    flag (`CycleCountPending`). Confirmed live: `true` while a count is
    unresolved (including a genuinely out-of-tolerance, never-resolving
    Pending Booking run), `false` once resolved (booked, rejected, or
    failed all clear it — being "resolved" isn't the same as "the count
    succeeded"). No Template needed — tested with and without one live
    and got the identical full row either way.
    """
    token = normalize_token(token)
    quoted = location_id.replace("'", "''")
    response = _post(
        LOCATION_COUNT_INFO_SEARCH_URL,
        headers=build_cycle_count_headers(token, org, location=location),
        json={"Query": f"LocationId = '{quoted}'", "Size": 20},
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"locationCountInfo search failed: {response.status_code} {response.text[:500]}"
        )
    rows = _response_data_list(response.json())
    return rows[0] if rows else None


def search_inventory_count_runs(location_id: str, token: str, org: str, location: str = None) -> List[dict]:
    """CONFIRMED-by-user-capture — every count run ever attempted for a
    location (not scoped to one CountRunId), newest and oldest mixed
    together; the caller picks the relevant one (see
    task_service.complete_cycle_count_line(), which already knows its
    own CountRunId from initiateCount and doesn't need to guess). Each
    row nests its own `ContainerCount[]`/`InventoryCount[]` detail, but
    search_inventory_count_results() below is the flatter, easier-to-use
    equivalent for that — this is mainly useful for the run's own
    Status/TaskId/BookedDateTime.
    """
    token = normalize_token(token)
    quoted = location_id.replace("'", "''")
    response = _post(
        INVENTORY_COUNT_RUN_SEARCH_URL,
        headers=build_cycle_count_headers(token, org, location=location),
        json={"Query": f"LocationId = '{quoted}'", "Size": 100},
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"inventoryCountRun search failed: {response.status_code} {response.text[:500]}"
        )
    return _response_data_list(response.json())


def search_inventory_count_results(
    count_run_id: str, location_id: str, token: str, org: str, location: str = None
) -> List[dict]:
    """CONFIRMED-by-user-capture — the flat, item-level detail for one
    specific count run: OriginalQuantity/CountQuantity/VarianceQuantity/
    Status ("Pending Booking"/"Booked"/etc, a human label already, not
    just a status key)/BookingFailureReason. This is the single source
    of truth task_service.complete_cycle_count_line() polls after
    persist/end to determine what actually happened, since none of the
    six completion calls' own responses reveal the real (asynchronous)
    booking outcome.
    """
    token = normalize_token(token)
    quoted_run = count_run_id.replace("'", "''")
    quoted_loc = location_id.replace("'", "''")
    response = _post(
        INVENTORY_COUNT_RESULT_SEARCH_URL,
        headers=build_cycle_count_headers(token, org, location=location),
        json={
            "Query": f"CountRunId = '{quoted_run}' AND LocationId = '{quoted_loc}'",
            "Size": 999,
        },
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"inventoryCountResult search failed: {response.status_code} {response.text[:500]}"
        )
    return _response_data_list(response.json())


def refresh_ilpn_read_timestamp(container_id: str, token: str, org: str, location: str = None) -> None:
    """CONFIRMED-by-user-capture — Modify iLPN step 4: when a targeted
    line's InventoryReadTimestamp comes back null from
    search_container_inventory(), call this once, then re-run that
    search to get the now-populated timestamp before building an
    endIlpn payload. Per the document, don't use any timestamp from
    this call's own response — only the subsequent search's.
    """
    token = normalize_token(token)
    quoted = container_id.replace("'", "''")
    response = _post(
        INVENTORY_READ_TIMESTAMP_REFRESH_URL,
        headers=build_task_headers(token, org, location=location),
        json={"Query": f"InventoryContainerId = '{quoted}'"},
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"updateReadTimeStamp failed: {response.status_code} {response.text[:500]}"
        )


def adjust_ilpn_inventory(
    ilpn_id: str,
    inventory_lines: List[dict],
    token: str,
    org: str,
    location: str = None,
    deleted_lines: Optional[List[dict]] = None,
) -> dict:
    """CONFIRMED-by-user-capture — Modify iLPN step 2 (endIlpn): correct
    an LPN's on-hand quantity directly. `inventory_lines` are dicts
    already shaped like the document's payload (OriginalOnHandQuantity/
    ExpectedOnHandQuantity/AllocatedQuantity/ScannedQuantity/ReasonCode/
    InventoryReadTimestamp/ItemAttributeDTO) — see
    task_service.adjust_ilpn_quantities() for how those are built.
    `ScannedQuantity` is relative, not absolute: New OnHand = Current
    OnHand + (ScannedQuantity - ExpectedOnHandQuantity). Only include
    lines actually changing — the document says not to include
    unrelated lines.

    A qty-0 delete: per explicit instruction, this app deliberately does
    NOT use the document's `DeletedInventory` path — live testing (by
    the document's author, outside this app) found that just including
    the line in `inventory_lines` with `ScannedQuantity: 0` removes it
    the same way. `deleted_lines` is kept as an unused parameter only so
    the (untested-here) documented path is easy to wire in later if the
    qty-0 approach ever turns out not to hold up.

    The response may be HTTP 200 with an empty body — the adjustment is
    processed asynchronously; the caller is expected to re-query and
    verify (see adjust_ilpn_quantities()).
    """
    token = normalize_token(token)
    payload = {
        "IlpnId": ilpn_id,
        "TransactionId": MODIFY_ILPN_TRANSACTION_ID,
        "Inventory": inventory_lines,
        "DeletedInventory": deleted_lines or [],
    }
    response = _post(
        MODIFY_ILPN_ADJUST_URL,
        headers=build_task_headers(token, org, location=location),
        json=payload,
    )
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:1200]} if response.text else {}
    if not isinstance(body, dict):
        body = {"data": body}
    if response.status_code not in (200, 201) and "raw" in body and len(body) == 1:
        raise RuntimeError(
            f"endIlpn adjustment failed: {response.status_code} {response.text[:800]}"
        )
    return body


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


def search_all_storage_locations(token: str, org: str, location: str = None) -> List[dict]:
    """CONFIRMED live — every active STORAGE location for one facility,
    for the frontend to preload once per task load rather than calling
    validate_storage_location() per keystroke (2026-08-08: switched from
    debounced live validation to this, per explicit instruction — real
    paper-warehouse facilities run far smaller counts than the
    org-wide "thousands" validate_storage_location()'s docstring warns
    about, so one bulk fetch scoped to the facility header is cheap).
    Same endpoint/`{Query,Page,Size}` convention as
    validate_storage_location(), just unscoped by LocationId/
    DisplayLocation and with a large Size instead of a Page loop —
    mirrors receivingworkbench's search_staging_locations() convention
    for its (much smaller) STAGING list.
    """
    token = normalize_token(token)
    response = _post(
        LOCATION_SEARCH_URL,
        headers=build_task_headers(token, org, location=location),
        json={
            "Query": f"LocationTypeId ='{STORAGE_LOCATION_TYPE_ID}' and IsActive=true",
            "Template": {"LocationId": "", "DisplayLocation": ""},
            "Size": 5000,
            "Page": 0,
        },
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"location search failed: {response.status_code} {response.text[:500]}"
        )
    return _response_data_list(response.json())


def search_items(
    item_ids: List[str], token: str, org: str, location: str = None
) -> Dict[str, dict]:
    """CONFIRMED (Tier 1) — item Description lookup, to hydrate task lines.

    Neither Task nor TaskDetail carries an item description field, so
    this is called after loading a task to fill that column in — same
    endpoint/shape receivingworkbench already uses for the same reason.

    Template extended 2026-08-08 to also fetch `DisplayUomId` and
    `ItemPackage[]` (exact same shape receivingworkbench's own
    search_items() confirmed live) — see task_service's UOM-resolution
    helpers, ported from that app, for how these back the UOM column.
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
        "Template": {
            "ItemId": "",
            "Description": "",
            "DisplayUomId": "",
            "ItemPackage": [
                {
                    "UomId": None,
                    "StandardQuantityUomId": None,
                    "Quantity": None,
                    "Standard": None,
                }
            ],
        },
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
    CompletedQuantity set by the caller (the fetched move itself doesn't
    set it). **CONFIRMED NOT to work, 2026-08-08**: setting
    `CompletedQuantity < Quantity` to express a genuine partial putaway
    (an extrapolation from the field names — the document's only worked
    example is a full completion, 240/240) was tested live against
    `IBPWIBPT0929` (`CompletedQuantity: 100` of `Quantity: 240`) and
    MAWM rejected it outright with a real business message: "Quantity
    entered is less than the system quantity." This endpoint requires
    the full system quantity; there is currently no known way to book a
    genuine partial Putaway completion through Path C. The task was
    left unaffected by the failed attempt (still 240 remaining, 0
    completed) — only its status flipped to "In Progress", an expected
    side effect of Call C1 (`fetchNextPutawayMoveAndStartLaborActivity`)
    itself, unrelated to the rejected commit.

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


# SUPERSEDED 2026-08-08 — this call's own warning-override mechanism
# (userInputs, an extrapolation from the DMM flow's documented pattern)
# was confirmed live NOT to work: resubmitting against a real DCI::120
# warning returned the identical warning again. Replaced by the
# confirmed-live DMM Mobile Facade flow (workflow_init()/
# workflow_execute()/apply_warning_overrides() below). Kept, commented
# out, in case this task-independent single-call approach is ever useful
# again — e.g. if a future capture confirms its real override contract.
#
# def move_container_user_directed(
#     container_id: str,
#     to_location_id: str,
#     item_id: str,
#     quantity,
#     token: str,
#     org: str,
#     location: str = None,
#     warning_overrides: Optional[Dict[str, str]] = None,
# ) -> dict:
#     """UNCONFIRMED — user-directed putaway to an operator-chosen
#     destination, per mawm_user_directed_putaway_with_warnings.md's core
#     API alternative.
#
#     Task-independent by design (per the document: "no task DTO, no task
#     ID, and no allocation ID in the active move") — takes ContainerId/
#     ItemId/Quantity/ToLocationId directly, unlike the system-directed
#     Path C flow which is keyed by TaskId.
#     """
#     token = normalize_token(token)
#     payload = {
#         "ContainerId": container_id,
#         "ToLocationId": to_location_id,
#         "TransactionId": USER_DIRECTED_TRANSACTION_ID,
#         "ItemId": item_id,
#         "ScannedQty": quantity,
#     }
#     if warning_overrides:
#         payload["userInputs"] = warning_overrides
#     response = _post(
#         PUTAWAY_USER_DIRECTED_MOVE_URL,
#         headers=build_task_headers(token, org, location=location),
#         json=payload,
#     )
#     try:
#         body = response.json()
#     except Exception:
#         body = {"raw": response.text[:1200]}
#     if not isinstance(body, dict):
#         body = {"data": body}
#     if response.status_code not in (200, 201) and "raw" in body and len(body) == 1:
#         raise RuntimeError(
#             f"user-directed putaway move failed: {response.status_code} {response.text[:800]}"
#         )
#     body["_requestPayload"] = payload
#     return body


def workflow_init(
    transaction_id: str, transaction_type: str, token: str, org: str, location: str = None
) -> dict:
    """CONFIRMED live — bootstrap a fresh DMM Mobile Facade workflow
    session (see PUTAWAY_WORKFLOW_INIT_URL's comment for the full
    confirmed sequence). Body is a literal `{}`; everything is driven by
    the query params. Returns the initial `{"workflowVO": {...}}`.
    """
    token = normalize_token(token)
    url = (
        f"{PUTAWAY_WORKFLOW_INIT_URL}"
        f"?transactionId={quote(transaction_id)}&transactionType={quote(transaction_type)}"
    )
    response = _post(url, headers=build_task_headers(token, org, location=location), json={})
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:1200]}
    if not isinstance(body, dict):
        body = {"data": body}
    if response.status_code not in (200, 201) and "raw" in body and len(body) == 1:
        raise RuntimeError(
            f"workflow init failed: {response.status_code} {response.text[:800]}"
        )
    return body


def workflow_execute(
    state_name: str,
    action_name: str,
    workflow_vo: dict,
    token: str,
    org: str,
    location: str = None,
) -> dict:
    """CONFIRMED live — resubmit a DMM Mobile Facade workflow action.
    `workflow_vo` must be the complete object from the immediately
    preceding call (init or execute) — every other field it carries
    (breadCrumbs, idempotencyKey, etc.) is required as-is. The scanned
    input for this action (e.g. `scannedContainerBarcode`,
    `scannedLocationBarcode`) is CONFIRMED (via HAR body inspection,
    2026-08-08) to live *inside* `workflow_vo["header"]["state"]` —
    the caller must set it there before calling this, the same way
    `apply_warning_overrides()` mutates `header.state.warningOverrideList`.
    There is NO separate top-level sibling field in the request body;
    an earlier version of this function guessed one
    (`{"workflowVO": ..., "scannedContainerBarcode": ...}`) and it
    silently produced a generic `serverError` — the server never saw
    the scan because it only reads it from `header.state`.
    See PUTAWAY_WORKFLOW_INIT_URL's comment for the confirmed warning
    shape and how apply_warning_overrides() clears one.
    """
    token = normalize_token(token)
    url = PUTAWAY_WORKFLOW_EXECUTE_URL_TEMPLATE.format(
        script=PUTAWAY_WORKFLOW_SCRIPT_NAME, state=state_name, action=action_name
    )
    payload = {"workflowVO": workflow_vo}
    response = _post(url, headers=build_task_headers(token, org, location=location), json=payload)
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:1200]}
    if not isinstance(body, dict):
        body = {"data": body}
    if response.status_code not in (200, 201) and "raw" in body and len(body) == 1:
        raise RuntimeError(
            f"workflow execute ({action_name}) failed: {response.status_code} {response.text[:800]}"
        )
    return body


def apply_warning_overrides(workflow_vo: dict, warning_overrides: Optional[Dict[str, str]]) -> dict:
    """Mutates (and returns) `workflow_vo` so its
    `header.state.warningOverrideList` includes every code in
    `warning_overrides` — the CONFIRMED live mechanism for clearing a
    DMM Mobile Facade warning (see PUTAWAY_WORKFLOW_INIT_URL's comment).
    No-op if there's nothing to apply.
    """
    if not warning_overrides or not isinstance(workflow_vo, dict):
        return workflow_vo
    state = (workflow_vo.get("header") or {}).get("state")
    if not isinstance(state, dict):
        return workflow_vo
    existing = list(state.get("warningOverrideList") or [])
    for code in warning_overrides:
        if code not in existing:
            existing.append(code)
    state["warningOverrideList"] = existing
    return workflow_vo


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
    (PTW::120). CONFIRMED live 2026-08-08 for the standard envelope on
    the core `container/move` endpoint specifically (`LPN00953` /
    `DCI::120`) — but resubmitting with `userInputs: {code: code}`
    (extract_warning()'s caller's override mechanism) did NOT clear it;
    MAWM returned the identical DCI::120 warning again. Both source
    documents hedged that the core endpoint's override contract might
    differ from the DMM flow's — this is now confirmed, not just
    suspected. The DMM stateful flow remains the fallback to implement
    if this specific warning needs to be overridable.
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


def extract_message(body) -> str:
    """Best-effort human-readable message for a failed response, for use
    as the error text shown to the user. Prefers the first
    `messages.Message[].Description` (any Type, not just WARNING —
    unlike extract_warning()) over the generic top-level `message`/
    `messageKey` (often just an opaque code like `"error.400"`).

    Added 2026-08-08 after observing exactly this gap live: resubmitting
    LPN00953's DCI::120 warning with an override that didn't clear it
    fell back to `message: "error.400"` at the top level, even though
    `messages.Message[0].Description` ("Location permanently dedicated
    to a different item") was sitting right there, unused.
    """
    if not isinstance(body, dict):
        return "Complete failed"
    for msg in ((body.get("messages") or {}).get("Message") or []):
        if isinstance(msg, dict):
            text = str(msg.get("Description") or msg.get("Message") or "").strip()
            if text:
                return text
    return str(body.get("message") or body.get("messageKey") or "Complete failed")


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


# ---------------------------------------------------------------------
# Picking (2026-08-10, eleventh session) — real TransactionTypeId is
# `"Pick"` (the app's own earlier TASK_TYPES/"PICKING" constant above
# was never live-verified — this is the confirmed real value). Locked
# down at the task_service layer to `TaskExecutionMode:
# "PICK_INTO_OLPN"` tasks with every line sourced from a plain
# `LOCATION` — confirmed live this session that an iLPN-sourced /
# `FullContainerAllocated: true` line hits a real, unresolved MAWM
# validation bug (`PPK::0513 "Quantity exceeds order line quantity"`)
# no matter what payload shape is sent (four distinct shapes tried,
# including the fully-enriched `fetchNextMove` response verbatim with
# only `CompletedQuantity` changed) — see CLAUDE.md's Picking section
# for the full investigation. `PICK_INTO_CART` (cart picking) is a
# real strategy to support later, not permanently excluded — refused
# for now as "not yet supported."
#
# **Confirmed live: one `commitPickMove` call does everything** for a
# `LOCATION`-sourced line — commits the inventory move, completes the
# task detail, and auto-closes the task when it's the last open line —
# no separate "end"/"trigger" call needed, unlike this app's Cycle
# Count feature. Also confirmed synchronous (not async like Cycle
# Count's booking) — every test showed the real outcome immediately on
# re-query right after the commit call, no polling needed.
# ---------------------------------------------------------------------

PICK_TRANSACTION_TYPE_ID = "Pick"
PICK_COMMIT_MOVE_URL = f"{HOST}/pickpack/api/pickpack/pick/commitPickMove"
PICK_OLPN_SEARCH_URL = f"{HOST}/pickpack/api/pickpack/olpn/search"


def search_task_id_for_olpn(olpn_id: str, token: str, org: str, location: str = None) -> List[str]:
    """Real, open (not Completed/Canceled) Pick TaskIds whose own
    TaskDetail references this OlpnId — same dotted-path
    `TaskDetail.<field>` query pattern already confirmed for Putaway's
    search_task_id_for_container(). Returns a **list**, not a single
    Optional[str] like that function — confirmed live this session
    that a single oLPN's lines can be split across more than one real
    Task record (e.g. two real SS-DEMO examples, each split 1-line/
    1-line across two separate tasks). Callers must handle more than
    one match explicitly rather than silently picking the first.
    """
    token = normalize_token(token)
    quoted = olpn_id.replace("'", "''")
    query = (
        f"TaskDetail.OlpnId ='{quoted}' and "
        f"TransactionTypeId ='{PICK_TRANSACTION_TYPE_ID}' and "
        f"Status !='8000' and Status !='9000'"
    )
    response = _post(
        TASK_SEARCH_URL,
        headers=build_task_headers(token, org, location=location),
        json={"Query": query, "Size": 10, "Page": 0},
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"task-by-olpn search failed: {response.status_code} {response.text[:500]}"
        )
    rows = _response_data_list(response.json())
    task_ids: List[str] = []
    seen = set()
    for row in rows:
        task_id = str(row.get("TaskId") or "")
        if task_id and task_id not in seen:
            seen.add(task_id)
            task_ids.append(task_id)
    return task_ids


def search_olpn(olpn_id: str, token: str, org: str, location: str = None) -> Optional[dict]:
    """The oLPN header + OlpnDetail array — used to confirm real
    PickedQuantity after a commit (this app's own verification, not
    part of the commit chain itself). CONFIRMED live —
    `pickpack/api/pickpack/olpn/search`, `Query: "OlpnId ='{id}'"`,
    same response shape as a Task/iLPN search (`data: [...]`).
    """
    token = normalize_token(token)
    quoted = olpn_id.replace("'", "''")
    response = _post(
        PICK_OLPN_SEARCH_URL,
        headers=build_task_headers(token, org, location=location),
        json={"Query": f"OlpnId ='{quoted}'", "Size": 5},
    )
    if response.status_code != 200:
        raise RuntimeError(f"olpn search failed: {response.status_code} {response.text[:500]}")
    rows = _response_data_list(response.json())
    return rows[0] if rows else None


def commit_pick_move(
    source_container_id: str,
    source_container_type: str,
    task_id: str,
    task_detail_id: str,
    olpn_id: str,
    transaction_id: str,
    quantity,
    token: str,
    org: str,
    location: str = None,
) -> dict:
    """CONFIRMED live — commits one Pick task-detail line. Deliberately
    the plain/minimal payload shape (not the fetch-then-enrich pattern
    Putaway needs) — confirmed live this session that the minimal shape
    below is sufficient and correct for every `LOCATION`-sourced line
    tested (single-line and multi-line tasks, `UNIT` and `PACK` UOM
    both), with no separate fetch call needed first. Only an
    iLPN-sourced/full-container line needed (and still didn't resolve
    with) the richer fetch/enrich/commit shape — see the module
    docstring above; this function is intentionally not used for that
    case at all (locked out one layer up, in task_service.py).
    """
    token = normalize_token(token)
    payload = {
        "InventoryMove": {
            "SourceContainerId": source_container_id,
            "SourceContainerType": source_container_type,
            "TaskId": task_id,
            "CurrentTaskDetailId": task_detail_id,
            "OlpnId": olpn_id,
            "TransactionId": transaction_id,
            "CompletedQuantity": quantity,
        },
        "ExceptionMove": False,
        "EndTargetContainer": False,
    }
    response = _post(
        PICK_COMMIT_MOVE_URL,
        headers=build_task_headers(token, org, location=location),
        json=payload,
    )
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:1200]}
    if not isinstance(body, dict):
        body = {"data": body}
    body["_httpStatus"] = response.status_code
    return body
