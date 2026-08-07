# Task Check In — Project Instructions

This project follows the global `AGENTS.md` and `SECURITY_BASELINE.md`.
The notes below cover only what's specific to this repository.

## Version identifiers

This project's version appears in three places — bump whichever
actually changed:

- `public/index.html` — the `<title>` ("Task Check In vX.Y.Z")
- `package.json` — the `version` field
- `api/index.py` — the `APP_VERSION` constant

## Running locally

Requires a `-DEMO` org token, either via:
- A local `.token` file (gitignored) containing a raw Bearer token, or
- `MANHATTAN_PASSWORD` / `MANHATTAN_SECRET` env vars (OAuth).

```
npm install
pip install -r requirements.txt
vercel dev
```

or, running the two processes separately:

```
python api/index.py        # Flask API on :5000
node server.js              # static + proxy on :3012
```

## Task Management API endpoints are unconfirmed

Everything in `mawm_client.py` that touches Task Management (`search_task`,
`search_task_transactions`, `complete_task` — and the URLs they call) is a
best-guess placeholder, not a verified integration. `mawm_api_library`
does not document the Task Management domain; nobody has captured a real
MAWM Task search/complete call for this app yet. Do not treat these as
working endpoints, and don't extend them further (new task types, new
fields) without first correcting them against a real RF-session capture.

The intended path to closing this gap: exercise a task in the normal
mobile RF client, capture the underlying API calls, and correct
`mawm_client.py`'s URLs/payloads against that capture — one task type at
a time, Putaway first (the currently prioritized workflow; Picking,
Cycle Counting, and Replenishment follow once Putaway's real endpoints
are confirmed working end to end).

## No known-good test Task Id yet

Unlike `receivingworkbench` (which has `ASN000000000013` as a confirmed
test fixture), there is no confirmed Task Id to test against yet — the
first one should come from whatever RF capture is used to correct the
search/complete endpoints above.
