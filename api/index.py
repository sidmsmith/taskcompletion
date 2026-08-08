# api/index.py
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, request
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mawm_client import TASK_TYPES, get_manhattan_token, normalize_token, validate_org  # noqa: E402
from task_service import (  # noqa: E402
    complete_container_putaway,
    complete_line,
    complete_putaway_line,
    preload_adjustment_reason_codes,
    preload_putaway_locations,
    preload_putaway_reason_codes,
    preload_task_transactions,
    resolve_search_multi,
    validate_putaway_location,
)

app = Flask(__name__)

PASSWORD = os.getenv("MANHATTAN_PASSWORD")
CLIENT_SECRET = os.getenv("MANHATTAN_SECRET")
APP_NAME = "taskcompletion-app"
APP_VERSION = "0.6.2"
DEFAULT_ORG = os.getenv("MANHATTAN_DEFAULT_ORG", "SS-DEMO").strip().upper() or "SS-DEMO"
TOKEN_FILE = ROOT / ".token"
USAGE_INGEST_URL = os.getenv("MANHATTAN_USAGE_INGEST_URL", "").strip()
USAGE_INGEST_SECRET = os.getenv("MANHATTAN_USAGE_INGEST_SECRET", "").strip()


def forward_usage_event(payload):
    if not USAGE_INGEST_URL:
        return
    headers = {"Content-Type": "application/json"}
    if USAGE_INGEST_SECRET:
        headers["Authorization"] = f"Bearer {USAGE_INGEST_SECRET}"
    try:
        requests.post(USAGE_INGEST_URL, json=payload, headers=headers, timeout=8, verify=False)
    except Exception as e:
        print(f"[usage] Forward failed: {e}")


def read_local_token_file() -> str:
    """Local-dev Bearer token from .token (gitignored). Empty on Vercel / missing file."""
    try:
        if not TOKEN_FILE.is_file():
            return ""
        return normalize_token(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[auth] Could not read .token: {e}")
        return ""


def resolve_bearer_token(org: str) -> tuple:
    """
    Resolve access token.
    Priority: project .token file > OAuth env vars.
    Returns (token, source) where source is 'token-file' | 'oauth' | None.
    """
    file_token = read_local_token_file()
    if file_token:
        return file_token, "token-file"
    oauth = get_manhattan_token(org)
    if oauth:
        return normalize_token(oauth), "oauth"
    return None, None


def _json():
    return request.get_json(silent=True) or {}


def _require_auth_fields(data):
    org = (data.get("org") or "").strip().upper()
    token = (data.get("token") or "").strip()
    if not org or not token:
        return None, None, jsonify({"success": False, "error": "ORG and token required"})
    return org, token, None


@app.route("/api/app_opened", methods=["POST"])
def app_opened():
    forward_usage_event(
        {
            **(_json() or {}),
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "event_name": "app_opened",
        }
    )
    return jsonify({"success": True})


@app.route("/api/auth", methods=["POST"])
def auth():
    data = _json()
    org = (data.get("org") or DEFAULT_ORG).strip().upper()
    if not org:
        return jsonify({"success": False, "error": "ORG required"})
    if not validate_org(org):
        return jsonify(
            {"success": False, "error": "Invalid ORG. Must end with -DEMO (e.g. SS-DEMO)."}
        )
    token, source = resolve_bearer_token(org)
    if token:
        forward_usage_event(
            {
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
                "event_name": "auth_success",
                "org": org,
                "source": source,
            }
        )
        return jsonify(
            {
                "success": True,
                "token": token,
                "org": org,
                "source": source,
                "fromTokenFile": source == "token-file",
                "taskTypes": list(TASK_TYPES),
            }
        )
    forward_usage_event(
        {"app_name": APP_NAME, "app_version": APP_VERSION, "event_name": "auth_failed", "org": org}
    )
    has_oauth = bool(PASSWORD and CLIENT_SECRET)
    has_file = TOKEN_FILE.is_file()
    hint = (
        "Auth failed. Place a Bearer token in .token (local), "
        "or set MANHATTAN_PASSWORD / MANHATTAN_SECRET."
    )
    if has_file and not has_oauth:
        hint = "Auth failed reading .token (empty or invalid)."
    elif not has_file and not has_oauth:
        hint = "No .token file and MANHATTAN_PASSWORD / MANHATTAN_SECRET are not set."
    return jsonify({"success": False, "error": hint})


@app.route("/api/load_task", methods=["POST"])
def load_task_route():
    data = _json()
    org, token, err = _require_auth_fields(data)
    if err:
        return err
    location = (data.get("location") or data.get("facility") or "").strip() or None
    # Field name kept as taskId for minimal churn — resolve_search_multi()
    # accepts one or more Task Ids/iLPNs in the same value, delimited by
    # ";", "," or whitespace (see its docstring); the frontend's search
    # box now accepts all of that too. Always returns the `groups`
    # shape (2026-08-08), even for a single match, so the frontend has
    # one response shape to handle instead of two.
    search_value = (data.get("taskId") or data.get("task_id") or "").strip()
    try:
        result = resolve_search_multi(token, org, search_value, location=location)
        forward_usage_event(
            {
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
                "event_name": "load_task_completed" if result.get("success") else "load_task_failed",
                "org": org,
                "searchValue": search_value,
                "groupCount": len(result.get("groups") or []),
            }
        )
        return jsonify(result)
    except Exception as e:
        print(f"[LOAD_TASK] {e}")
        forward_usage_event(
            {
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
                "event_name": "load_task_failed",
                "org": org,
                "searchValue": search_value,
                "error": str(e),
            }
        )
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/validate_putaway_location", methods=["POST"])
def validate_putaway_location_route():
    data = _json()
    org, token, err = _require_auth_fields(data)
    if err:
        return err
    location = (data.get("location") or data.get("facility") or "").strip() or None
    location_text = (data.get("locationText") or data.get("location_text") or "").strip()
    try:
        result = validate_putaway_location(token, org, location_text, location=location)
        return jsonify(result)
    except Exception as e:
        print(f"[VALIDATE_PUTAWAY_LOCATION] {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/preload_task_transactions", methods=["POST"])
def preload_task_transactions_route():
    data = _json()
    org, token, err = _require_auth_fields(data)
    if err:
        return err
    location = (data.get("location") or data.get("facility") or "").strip() or None
    task_type = (data.get("taskType") or data.get("task_type") or "").strip().upper()
    task_transaction_id = (
        data.get("taskTransactionId") or data.get("task_transaction_id") or ""
    ).strip()
    try:
        result = preload_task_transactions(
            token, org, task_type, location=location, task_transaction_id=task_transaction_id
        )
        return jsonify(result)
    except Exception as e:
        print(f"[PRELOAD_TASK_TRANSACTIONS] {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/preload_putaway_locations", methods=["POST"])
def preload_putaway_locations_route():
    data = _json()
    org, token, err = _require_auth_fields(data)
    if err:
        return err
    location = (data.get("location") or data.get("facility") or "").strip() or None
    try:
        result = preload_putaway_locations(token, org, location=location)
        return jsonify(result)
    except Exception as e:
        print(f"[PRELOAD_PUTAWAY_LOCATIONS] {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/preload_putaway_reason_codes", methods=["POST"])
def preload_putaway_reason_codes_route():
    data = _json()
    org, token, err = _require_auth_fields(data)
    if err:
        return err
    location = (data.get("location") or data.get("facility") or "").strip() or None
    try:
        result = preload_putaway_reason_codes(token, org, location=location)
        return jsonify(result)
    except Exception as e:
        print(f"[PRELOAD_PUTAWAY_REASON_CODES] {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/preload_adjustment_reason_codes", methods=["POST"])
def preload_adjustment_reason_codes_route():
    try:
        return jsonify(preload_adjustment_reason_codes())
    except Exception as e:
        print(f"[PRELOAD_ADJUSTMENT_REASON_CODES] {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/complete_line", methods=["POST"])
def complete_line_route():
    data = _json()
    org, token, err = _require_auth_fields(data)
    if err:
        return err
    location = (data.get("location") or data.get("facility") or "").strip() or None
    task_id = (data.get("taskId") or data.get("task_id") or "").strip()
    task_detail_id = (data.get("taskDetailId") or data.get("task_detail_id") or "").strip()
    container_id = (data.get("containerId") or data.get("container_id") or "").strip()
    lpn_id = (data.get("lpnId") or data.get("lpn_id") or "").strip()
    mode = (data.get("mode") or "").strip().lower()
    quantity = data.get("quantity")
    transaction_id = (data.get("transactionId") or data.get("transaction_id") or "").strip()
    strategy_id = (data.get("strategyId") or data.get("strategy_id") or "").strip()
    warning_overrides = data.get("warningOverrides") or data.get("warning_overrides") or None
    to_location_id = (data.get("toLocationId") or data.get("to_location_id") or "").strip()
    reason_code_id = (data.get("reasonCodeId") or data.get("reason_code_id") or "").strip()
    # [{"itemId", "desiredQty", "reasonCode"}, ...] (2026-08-08) — one
    # entry for an ordinary Completed Qty edit, several for a MIXED
    # container's per-item accordion. See
    # task_service.adjust_ilpn_quantities()/complete_putaway_line()/
    # complete_container_putaway()'s docstrings.
    item_adjustments = data.get("itemAdjustments") or data.get("item_adjustments") or None

    try:
        if container_id:
            # No open task at all (resolve_search()'s mode: "no_task") —
            # the task-independent flow, keyed by container instead.
            if not to_location_id:
                return jsonify({"success": False, "error": "A destination location is required"})
            result = complete_container_putaway(
                token,
                org,
                container_id,
                to_location_id,
                location=location,
                warning_overrides=warning_overrides,
                item_adjustments=item_adjustments,
            )
        else:
            if not task_id or not task_detail_id:
                return jsonify({"success": False, "error": "taskId and taskDetailId required"})
            if not transaction_id:
                return jsonify({"success": False, "error": "Transaction ID is required"})
            if mode == "full":
                # Putaway's confirmed-by-capture completion path (see
                # task_service.complete_putaway_line). Other task types
                # aren't wired to this yet — this app currently only
                # builds/tests Putaway, so "full" always routes here for
                # now. A task-mode line is never MIXED, so at most the
                # first itemAdjustments entry applies here — see
                # complete_putaway_line()'s docstring for why a quantity
                # change now goes through a Modify iLPN adjustment first
                # (2026-08-08) rather than the old, confirmed-broken
                # direct-partial attempt. A non-blank toLocationId means
                # the user edited the grid's destination away from what
                # was loaded — see complete_putaway_line()'s docstring
                # for the required reasonCodeId and Substitute Location
                # handling.
                adjustment = (item_adjustments or [None])[0]
                result = complete_putaway_line(
                    token,
                    org,
                    task_id,
                    task_detail_id,
                    transaction_id,
                    location=location,
                    warning_overrides=warning_overrides,
                    to_location_id=to_location_id or None,
                    reason_code_id=reason_code_id or None,
                    item_id=(adjustment or {}).get("itemId"),
                    lpn_id=lpn_id or None,
                    desired_qty=(adjustment or {}).get("desiredQty"),
                    adjustment_reason_code=(adjustment or {}).get("reasonCode"),
                )
            else:
                result = complete_line(
                    token,
                    org,
                    task_id,
                    task_detail_id,
                    mode,
                    transaction_id,
                    strategy_id=strategy_id or None,
                    quantity=quantity,
                    location=location,
                )
        forward_usage_event(
            {
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
                "event_name": "complete_line_completed" if result.get("success") else "complete_line_failed",
                "org": org,
                "taskId": task_id,
                "taskDetailId": task_detail_id or container_id,
                "mode": mode,
            }
        )
        return jsonify(result)
    except Exception as e:
        print(f"[COMPLETE_LINE] {e}")
        forward_usage_event(
            {
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
                "event_name": "complete_line_failed",
                "org": org,
                "taskId": task_id,
                "taskDetailId": task_detail_id or container_id,
                "error": str(e),
            }
        )
        return jsonify({"success": False, "error": str(e)}), 500


# Local Flask entry (vercel wraps the module)
if __name__ == "__main__":
    app.run(port=5000, debug=True)
