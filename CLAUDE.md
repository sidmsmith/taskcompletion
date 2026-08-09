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

## Task Management domain — confirmed read, unconfirmed write

The real MAWM component is named plainly `task` (confirmed via
`cloudComponent: com-manh-cp-task-1` in live responses), not
`task-management` as first guessed — `mawm_api_library` still doesn't
document this domain, so this app is the first place it's captured.

**Confirmed live** against `SS-DEMO` with `IBPWIBPT0929` (a Putaway
task) — safe, read-only, verified working end to end:
- `search_task()` → `POST task/api/task/task/search` — same
  `{Query,Page,Size}` convention as every other MAWM object; the
  response nests the full line array under `TaskDetail[]`, so one call
  loads a task with its lines (no separate detail call needed).
- `search_task_transactions()` → `POST task/api/task/transaction/search`
  — filter by `TransactionTypeId`, not `TaskType` (the Task object has
  no `TaskType` field at all).
- The Task record carries its own `TransactionId` (e.g. `"Putaway"`)
  directly — `load_task()` returns it as `taskTransactionId`, and
  `preload_task_transactions()` prefers it as the dropdown default
  over the static `DEFAULT_TRANSACTION_BY_TASK_TYPE` guess.

**Confirmed-by-user-capture** (not independently probed live, but taken
from a real captured request/response set the user supplied):
- Putaway full completion (default destination) — `fetch_putaway_move()`
  + `commit_putaway_move()`, from
  `mawm_putaway_api_call_set_with_warning_handling.md`. Sets the fetched
  move's `CompletedQuantity` equal to its `Quantity` for a full
  completion.
- Putaway Substitute Location (operator edits the destination in the
  grid) — still the same `fetch_putaway_move()` + `commit_putaway_move()`
  sequence ("directed putaway is still used"), with the fetched move's
  `ToLocationId`/`ReasonCodeId` overridden before committing, per
  `mawm_substitute_location_to_user_directed_putaway.md`. A reason code
  is required — `search_putaway_reason_codes()` backs a
  required-selection modal (`public/app.js`'s `promptReasonCode()`)
  shown before the completion call is made. **CONFIRMED live, but at a
  different URL than the document guessed**: its
  `GET /api/putaway/config/services/reasonCodes/list` (hedged there as
  "typically GET", no method actually captured) 404'd; the real
  endpoint is `POST /putaway/api/putaway/reasonCode/search` — the same
  standard search convention every other confirmed object in this app
  uses. Returns the same four codes the document's DMM-facade lookup
  captured, but under `ReasonCodeId`/`Description`, not the document's
  `key`/`value` (that shape belongs to the DMM facade's own lookup
  response, not this core search endpoint).

  **CONFIRMED live, 2026-08-08**: the substitution itself. The document
  only ever captured the DMM Mobile Facade's stateful equivalent
  (`SubstituteLocation` → `EnterReasonCodeForSubstituteAction` →
  `AcceptLocationForUserDirectedPutaway`), never the core
  `commitAndFetchNextMove` payload with an overridden
  `ToLocationId`/`ReasonCodeId` directly — so putting a `ReasonCodeId`
  field on `InventoryMove` was this app's own extrapolation (see
  `_complete_putaway_line_system_directed()`'s docstring), and it works:
  tested against task `IBPWIBPT0221` (container `LPN000000000315`,
  item `3000223`, qty 50), substituting the planned destination
  `A1AC0114` for `A1AC0119` with reason `Damaged Location`. `success:
  true`, and independently confirmed — not just trusted — by
  re-querying: the task shows `Completed`/`50 of 50`, and a direct
  inventory search for `LocationId ='A1AC0119' and ItemId ='3000223'`
  returns the full 50 units on hand there (the source container itself
  goes empty, `CurrentLocationId: null`, which is expected — its
  contents moved to the destination location, not to a new container).
- Warning handling (`extract_warning()`) checks both documents' shapes:
  the standard `messages.Message[]` envelope and the DMM
  `workflowVO.header.state.errorVOList` shape (the source of the
  `PTW::119`/`DCI::120`/`PTW::120` examples). Confirming a warning by
  resubmitting with a `{code: code}` `userInputs` map is proven for the
  DMM flow specifically — extending that same mechanism to the core
  commit endpoint is an extrapolation, flagged as such in the relevant
  docstrings.
- **Bug fixed 2026-08-08**: `fetch_putaway_move()`/`commit_putaway_move()`/
  `move_container_user_directed()` all raised a hard `RuntimeError` on
  any non-2xx HTTP status *before* the caller ever got to run
  `extract_warning()` on the body — and MAWM returns at least some
  warnings over a non-2xx status (confirmed: `LPN00953`'s no-task
  Substitute-Location test returned `DCI::120`, "Location permanently
  dedicated to a different item," the same code the DMM document
  captured, via the standard `messages.Message[]` envelope — but over a
  non-200 status, so it was surfacing as a raw error instead of the
  Confirm/Cancel modal). All three now always return the parsed body
  and let the caller's existing warning/success check decide, instead
  of raising early. Only a genuinely unparseable response still raises.
  This confirms the core API *does* use the standard envelope for at
  least one of the DMM-documented codes — evidence the extrapolation
  above is on the right track, though only for the read side of that
  flow; the override-and-actually-complete side is still unverified.
- **CONFIRMED WRONG, 2026-08-08**: the `userInputs: {code: code}`
  override, resubmitted against `LPN00953`'s `DCI::120` warning, did
  **not** clear it — MAWM returned the identical warning again. Both
  source documents hedged that the core endpoint's override contract
  might not match the DMM flow's; this settles it for `container/move`
  specifically (untested on `commitAndFetchNextMove`). A dedicated-
  location warning currently has no way to be overridden through this
  app. Real next step: capture what a successful override actually
  looks like from the mobile RF client for this exact scenario — same
  as how every other endpoint in this app got confirmed. Until then,
  `extract_message()` (added the same day) at least surfaces the real
  business message (`"Location permanently dedicated to a different
  item"`) instead of the unhelpful top-level `"error.400"` when this
  happens.

**Superseded a second time, 2026-08-08** — `mawm_client.move_container_user_directed()`
(`POST putaway/api/putaway/execution/container/move`) was briefly
revived for the no-task/iLPN container case, but its own warning
override was confirmed not to work (same as on an existing task), so
it's commented out again. The no-task container case now goes through
the DMM Mobile Facade flow below instead — same mechanism, same
override, already proven live end to end. `task_service
._complete_putaway_line_user_directed()` — the old orchestration that
called it for the on-task case — also remains commented out.

**CONFIRMED live end-to-end, 2026-08-08** — the DMM Mobile Facade
"User Directed Putaway" flow now backs `task_service
.complete_container_putaway()` (the no-task/iLPN container case, see
"iLPN search" below): `mawm_client.workflow_init()` →
`workflow_execute()` (`AcceptContainerForUserDirectedPutaway`, then
`AcceptLocationForUserDirectedPutaway`), captured start-to-finish from
a real mobile RF session's HAR. Two real, non-header findings from
building this:
- **Root cause of an early `serverError`**: the scanned input for each
  step (`scannedContainerBarcode`, `scannedLocationBarcode`) is **not**
  a separate top-level sibling field in the request body — an
  extrapolation from the source document assumed
  `{"workflowVO": ..., "scannedContainerBarcode": "..."}` and the
  server silently ignored it, returning a generic
  `"serverError": "An unexpected system error occurred."` with no
  other diagnostic info. Confirmed via direct HAR body inspection: the
  value lives *inside* `workflowVO.header.state.scannedContainerBarcode`
  (a hidden field on the state, alongside `warningOverrideList`),
  mutated the same way as the warning override, then the whole
  `workflowVO` resubmitted. `workflow_execute()` no longer takes an
  `extra_fields` parameter for this reason — the caller must set the
  field on `workflow_vo["header"]["state"]` before calling it.
- Headers were a dead end for this bug — `workflow_init()`'s headers
  (which already worked) matched `AcceptContainer`'s almost exactly;
  no missing session cookie or auth header was involved. Worth
  remembering for next time a DMM step fails with an opaque
  `serverError`: check the request **body** shape against a real HAR
  capture before suspecting headers/session state.
- Verified with two real round-trip moves of `LPN00953` between
  `R2R40106` and `R2R40105` through `task_service
  .complete_container_putaway()` directly (bypassing the Flask route,
  which is a thin pass-through) — both succeeded and were confirmed by
  re-querying the iLPN's actual current location afterward, not just
  by trusting `success: true`.
- **CONFIRMED live, 2026-08-08 (second session)**: the warning-and-
  override path for this exact flow. `LPN000000000010` (no current
  location — a container that had never been putaway) to `R2R61001`
  returned `DCI::159` ("No item assignment exists for location
  R2R61001") at the `AcceptLocationForUserDirectedPutaway` step on the
  first attempt; resubmitting with `warningOverrideList: ["DCI::159"]`
  returned `success: true`, and the iLPN's current location was
  confirmed (by re-query, not just the response) to have actually
  changed from `null` to `R2R61001`. Same override mechanism already
  proven for Substitute Location, now proven for this flow too.

## iLPN search (2026-08-08)

The search box now accepts a Task Id **or** an iLPN — one field,
auto-detect (`task_service.resolve_search()`):

1. Try the value as a Task Id.
2. Else, try it as a container: resolve its open (not Completed/
   Canceled) Putaway task, if one exists — **CONFIRMED live**:
   `TaskDetail.SourceContainerId ='{id}' or TaskDetail.TargetContainerId
   ='{id}'`, combined with `TransactionTypeId ='Putaway' and Status
   !='8000' and Status !='9000'`, on the same `task/api/task/task/search`
   endpoint (the nested `TaskDetail.<field>` dotted-path filter works on
   the header search, mirroring `mawm_api_library`'s documented
   `AsnLine.PurchaseOrderId` pattern for a different object; `Status not
   in (...)` was tried and rejected with a 400 — chained `!=` works).
   The `TransactionTypeId` filter matters: a real SS-DEMO container had
   both an open Putaway task and an unrelated open "LPN Disposition"
   task on it.
3. Else, if it's at least a real iLPN, return `mode: "no_task"`: a
   synthetic single line with Current Location = the iLPN's real
   `CurrentLocationId` (**CONFIRMED live**, `dcinventory/ilpn/search`)
   and Item/Description/Qty from its actual on-hand inventory
   (**CONFIRMED live**, `dcinventory/inventory/search` — same
   endpoint/shape `receivingworkbench` already uses). To Location starts
   blank; fails cleanly if the LPN holds more than one item (mixed-LPN
   putaway isn't handled).
4. Completing that synthetic line goes through
   `complete_container_putaway()` → the DMM Mobile Facade "User
   Directed Putaway" flow (see the confirmed section above) — **no
   reason code required** here (unlike Substitute Location: there's no
   system-directed default being overridden). **CONFIRMED live,
   2026-08-08**: both the plain-success and the warning-and-override
   paths, with real inventory moves verified by re-query, not just
   `success: true` — see above.

**To Location validation now applies everywhere, always** (per explicit
instruction, not just the no-task case), and is now preloaded rather
than checked live per keystroke (**changed 2026-08-08**, per explicit
instruction): `mawm_client.search_all_storage_locations()` /
`task_service.preload_putaway_locations()` (**CONFIRMED live** —
`dcinventory/location/search`, `LocationTypeId ='STORAGE' and
IsActive=true`, unscoped by LocationId/DisplayLocation, `Size: 5000` —
`SS-DEMO-DM1` returned **3,832** real rows, comfortably under that cap;
if a facility ever needs more than 5000, this will silently truncate
and needs a Page loop added) is called once per session, right after
`authenticate()` succeeds, fire-and-forget
(`public/app.js`'s `preloadStorageLocations()`). It builds a plain
uppercased `Set` of every `LocationId`/`DisplayLocation`, and
`validateLocation()` checks that Set synchronously on every keystroke —
genuinely real-time, no network round trip, no debounce. The original
per-keystroke live call (`validate_storage_location()` /
`task_service.validate_putaway_location()` / `/api
/validate_putaway_location`) is kept as-is, now only as a fallback
inside `validateLocation()` for the brief window before the preload
resolves (or if it fails outright) — still debounced 400ms in that
case, same as before. Gates all 3 completion buttons — Partial/Complete
Line need the *selected* line's destination valid; Complete All needs
every *outstanding* line's destination valid. Partial Complete is also
unconditionally disabled in `no_task` mode — it isn't wired for the
container flow (`complete_container_putaway()` always moves the full
on-hand quantity, no partial-quantity concept).

**Still unconfirmed**: `complete_task()` (`task/api/task/task/completeTask`,
URL and payload both guessed — the original, generic placeholder for
non-Putaway task types). Deliberately *not* probed live — it mutates
real state, so a wrong guess risks corrupting `SS-DEMO` test data rather
than just 404ing. Don't call it against a real environment without
either a real RF-session capture to correct it against first, or
explicit sign-off to probe it live.

**RESOLVED, 2026-08-08 (fifth session)** — see the "UOM display and
conversion" section further down: `TaskDetail.UomTypeId` is a code into
the item's `ItemPackage[]`, not a display label, and `Quantity` really
is base-unit and needed real conversion, not just a label fix.

## Multi-LPN search, editable Completed Qty, LPN column (2026-08-08, third session)

Three related UI/API changes, delivered together per explicit instruction:

- **LPN column** moved between Line and Item in both the table header
  and `renderGroups()`'s row markup — purely cosmetic, no API change.
- **Completed Qty is now an editable inline box** (`public/app.js`'s
  `.completed-qty-input`), defaulted to the line's remaining quantity,
  same "only send if changed" override contract as Current Location
  (`getCompletedQtyOverride()` mirrors `getLocationOverride()` exactly).
  The separate **Partial Complete button/modal was removed** — editing
  this box is now how a different quantity is expressed, on the same
  single "Complete Line" button.

  **Superseded the same day, see the "Modify iLPN" section below**:
  the mechanism this originally shipped with — sending the edited value
  straight through as the Path C commit's `CompletedQuantity` — was
  confirmed live NOT to work (MAWM's core `commitAndFetchNextMove`
  rejects `CompletedQuantity < Quantity` outright: *"Quantity entered is
  less than the system quantity"*) and has been fully reverted, not
  just left disabled. An edited Completed Qty now triggers a real
  inventory correction (Modify iLPN) *before* the normal, unmodified
  completion call, which is a materially different (and — for the
  no-task/container case — confirmed working) mechanism.

- **Multi-LPN search** (`task_service.resolve_search_multi()`): the
  search box now accepts more than one Task Id/iLPN, delimited by `;`,
  `,`, or whitespace. Each token resolves independently via the
  existing `resolve_search()` — one may have an open task, another may
  not, another may not exist at all (collected into `notFound` rather
  than aborting the whole search). Every line is denormalized with its
  owning group's identity (`groupMode`/`groupTaskId`/
  `groupContainerId`/`groupTaskStatusLabel`/etc.) so the frontend can
  treat the combined line list as flat and self-describing — no
  cross-referencing back into a `groups` array anywhere in
  `public/app.js`. `taskDetailId` was already globally unique across
  groups (real GUIDs for tasks, `container:{id}` for no-task
  containers), so row selection/keying just switched from `lineNumber`
  (not unique across groups) to `taskDetailId` — this also simplified
  the single-group case, which is now just `groups.length === 1`
  instead of a separate code path. `/api/load_task` always returns this
  `groups` shape now, even for one match — one response shape for the
  frontend to handle instead of two.

  **Complete All now spans every group on screen**, not just one
  task/container (per explicit instruction). The confirmation modal
  prefixes each line with its owning Task/Container when more than one
  group is loaded.

  **Task/Container column**: shown in the table only when more than one
  group is loaded (`#linesTable`'s `multi-group` CSS class, toggled by
  `renderGroups()`) — single-group results look exactly as before.
  `renderTaskMeta()`'s header does the same: unchanged for one group,
  a plain count summary for multiple. This is an explicitly interim
  default — final design (whether to keep both a header and a column,
  how picking vs. cycle count should differ) is deliberately deferred.

  **CONFIRMED live end-to-end** (2026-08-08, real `SS-DEMO` data, via
  an actual browser session against the local dev server — not just
  direct Python calls): search `LPN00763, LPN000000000010` (a fresh
  task-mode line + a no-task container) loaded both groups correctly,
  the no-task row's Completed Qty was correctly locked, and running
  **Complete All** across both — with a `DCI::120` warning firing
  mid-loop on the no-task line and correctly pausing for Confirm — both
  lines resolved (one success, one real failure, see below), each
  independently verified against live inventory/task state afterward.

  **Two real bugs found and fixed during this browser session** (both
  pre-existing, not introduced today — confirmed via `git log` that the
  first predates this session entirely):
  - `actionStatus` was being cleared immediately after a completion's
    success/error message was set, because `renderLines()`/
    `renderGroups()` unconditionally cleared it on every render,
    including the automatic refresh after a completion
    (`reloadCurrentSearch()`). The message would flash and vanish
    before ever being visible. Fixed by moving the clear into
    `loadTask()` (a genuinely new search) instead of `renderGroups()`
    (which now also runs on post-completion refreshes).
  - A failed `fetch_putaway_move()` call (Path C, Call C1) that comes
    back as a hard MAWM `ERROR` (not a `WARNING` — `extract_warning()`
    correctly lets those through separately) with a real business
    message was being silently replaced by a generic "No putaway move
    returned for this task." Confirmed live: `IBPWIBPT0109` returned
    `FWTSK::019`, *"Task IBPWIBPT0109 cannot be assigned to user
    demoweb@ss-demo"* (a real task-assignment conflict in `SS-DEMO`,
    not a code bug — same class of issue noted for this exact task
    earlier in the project) — the app showed the generic fallback
    instead. Fixed by preferring `extract_message()`'s real text when
    the response actually has one, same pattern already used elsewhere
    in this module.

## Modify iLPN adjustment, MIXED accordion, Task/Container column move (2026-08-08, fourth session)

The real mechanism for a different Completed Qty, replacing the
same-day-earlier (confirmed-broken) direct-`CompletedQuantity` attempt
described above. Source: `mawm_modify_ilpn_query_and_adjustment.md`
(a real captured Postman test set) plus `modifyLPN-single.har`
(reason-code lookup only — the DMM ModifyIlpn mobile workflow itself
was explicitly out of scope, per instruction).

**The mechanism** — `task_service.adjust_ilpn_quantities()`: correct
the LPN's actual on-hand inventory via
`POST inventory-management/api/inventory-management/adjust/endIlpn`,
around the normal, unmodified completion (which re-fetches fresh and so
picks up whatever quantity MAWM now considers correct). **Superseded
for the task-mode path in the sixth session** — see "Task-mode quantity
correction: sequence reversed" further down: adjust-before-complete
only works for a no-task/container LPN; a task-mode line now adjusts
*after* completing, because an LPN still allocated to an open task
can't be adjusted at all. Everything below in this section (the
adjustment mechanism itself, its confirmed live tests, the reason
codes, the MIXED accordion) is unchanged and still accurate — only the
task-mode *ordering* moved.
- `ScannedQuantity` on the wire is a *relative* adjustment
  (`New OnHand = Current + (ScannedQuantity - ExpectedOnHandQuantity)`),
  but since this always re-queries live inventory immediately before
  building the payload, `Original == Expected == current OnHand` here,
  which collapses `ScannedQuantity` to simply the desired new total —
  see the function's docstring for the full reasoning.
- Unchanged items are omitted from the payload entirely (per the
  document — "don't include unrelated lines"), not sent with a
  no-op adjustment.
- A quantity of exactly 0 is sent as a normal line
  (`ScannedQuantity: 0`), **not** through the document's
  `DeletedInventory` path — per explicit instruction, based on the
  document author's own live testing that this removes the line the
  same way. `adjust_ilpn_inventory()` keeps `DeletedInventory` as an
  unused parameter in case that ever needs wiring in instead.
- **CONFIRMED WRONG, live**: the assumption (stated in the original
  request) that a null `InventoryReadTimestamp` only comes up when
  adding a brand-new line. Checked two real, pre-existing (nothing
  being added) containers live: one had a real timestamp, the other —
  `LPN000000000010` — came back null. So `adjust_ilpn_quantities()`
  always checks for this and calls `refresh_ilpn_read_timestamp()` +
  re-queries once whenever a targeted line needs it, rather than
  skipping that step. Adding a genuinely new line (a row that doesn't
  exist in inventory at all yet) is still out of scope and correctly
  refused with a clear error — there's no existing row to read *any*
  timestamp from, refreshed or not; the document's own approach for
  that case is to send a literal null timestamp directly, which this
  function deliberately doesn't attempt.
- `endIlpn` is asynchronous (may return HTTP 200 with an empty body) —
  `adjust_ilpn_quantities()` waits ~1.5s then re-queries once to verify
  the new on-hand actually matches what was requested, returning
  `success: False` with the mismatched item ids if it doesn't yet
  (treat as "try again shortly," not a hard failure).

**CONFIRMED live end-to-end**, all against real `SS-DEMO` data,
independently re-verified by direct re-query (not just trusting a
`success: true`):
- Basic increase: `0000099999000008672` item `6000106`, 9 → 10 units.
- Qty-0 deletion: same container, item `6000105`, 5 → 0 — the line was
  fully removed from inventory search results, confirming the
  qty-0-instead-of-`DeletedInventory` approach works exactly as the
  document's author found.
- Null-timestamp refresh path: `LPN000000000010` item `5000225`, real
  pre-existing line, null timestamp → refresh → retry succeeded, 200 →
  195 units, timestamp now populated.
- **No-task/container completion with an adjustment, full click-driven
  browser test** (not just direct Python calls): `LPN000000000010`
  edited to 190 units + destination `R2R40106` in the actual UI,
  Complete Line clicked, a real `DCI::120` warning appeared and was
  confirmed, and the completion succeeded — independently verified
  both the on-hand quantity (195 → 190) and the container's current
  location actually changed.
- **MIXED container, adjustment down to a single item, then full
  completion**: `0000099999100000772` (real mixed inventory found live,
  not fabricated — items `4000042`/`4000043`) — zeroed out `4000043`,
  left `4000042` unchanged (correctly skipped as a no-op), container
  correctly reduced to one item, then completed putaway to `R2R40105`
  (after confirming the same `DCI::120` warning) — confirmed both the
  adjustment and the final location live.

**UNCONFIRMED, explicitly deferred to the user to test live**: this
whole sequence against an LPN **already allocated to an open task**
(the `complete_putaway_line()` task-mode path). Everything above was
tested against no-task/container LPNs only, matching the source
document's own Postman testing scope. Whether
`fetchNextPutawayMoveAndStartLaborActivity` re-syncs its `Quantity` to
an adjusted on-hand, or still expects the task's original planned
amount, is genuinely unknown — see `complete_putaway_line()`'s and
`adjust_ilpn_quantities()`'s docstrings.

**Reason codes**: `mawm_client.ADJUSTMENT_REASON_CODES` — a static
list of 6 (`Charity/CH`, `Inventory Adjustment/IA`,
`Inventory Damaged/DM`, `Inventory Delete/ID`,
`Lost in cycle count/LC`, `Mass Inventory Movement/MM`), extracted from
a real captured lookup response, not a live search — that lookup
(`adjustmentReasonCodes/list`) only ever appeared nested inside an
active DMM ModifyIlpn workflow session in the HAR, and building that
whole workflow just to fetch six stable codes was out of scope.
Preloaded once per session via `/api/preload_adjustment_reason_codes`
(single source of truth in `mawm_client.py`, not duplicated in
`public/app.js` — mirrors the `preloadStorageLocations()` pattern).
Hardcoded default `IA`, always changeable. The dropdown
(`.reason-code-select`) only becomes visible next to a row once its
Completed Qty box is actually edited away from its default — same
"only show/send when it matters" pattern as everything else here.

**MIXED containers now expand into an accordion**
(`resolve_search()`'s no_task branch now returns `mixedItems:
[{itemId, description, quantity}, ...]` on the summary line instead of
just a total). `MIXED` itself moved from the Description column into
the Item column (Description now blank for that row), per explicit
instruction. Each item gets its own sub-row (`.mixed-item-row`,
`.mixed-qty-input`) with an independent Completed Qty box and reason
dropdown, defaulted to that item's own on-hand — collapsed by default,
toggled via the `▶ MIXED` button. The summary row keeps the *shared*
To Location input (one container, one destination) and shows the
aggregate Planned Qty read-only. Submitting sends every item's current
box value as one multi-entry `itemAdjustments` array (unchanged items
safely no-op server-side, no client-side "did it change" detection
needed for this case, unlike the single-item box).

**Multi-item putaway completion is now CONFIRMED supported, 2026-08-08
(fifth session)** — the "must resolve to one item first" restriction
described here in the fourth session was wrong and has been removed;
see `complete_container_putaway()`'s docstring for the full story (a
real HAR capture of the mobile RF client, `userdirectedmultiple.har`,
showed MAWM's own workflow state carries a `multiItemContainer: true`
flag and moves every item on a container to the same destination in
one call — no per-item ItemId/Quantity input was ever needed).
`item_adjustments` is still useful for correcting quantities beforehand,
just no longer required to complete a MIXED container. **Confirmed
live** against the exact real container from the original bug report,
`0000099999000008672` (items `6000106`/10 units, `8000145`/2 units) —
completed to `R2R40105` after confirming the same `DCI::120` warning
the mobile capture hit, both items independently verified at the new
location afterward.

**Completed Qty no longer disabled for no-task/container rows** — the
original disable-for-no-task reasoning ("the DMM AcceptContainer step
has no quantity input") is now moot: a different quantity is corrected
via Modify iLPN *before* AcceptContainer ever runs, so
`complete_container_putaway()` needed zero changes itself to support
this — it already re-queries live on-hand at call time regardless.

**Validity gating relaxed**: Completed Qty no longer has an upper bound
(`validateQty()`/`isQtyValid()`) — before this change, editing the box
could only ever express a *smaller* quantity on the same move, so it
was capped at "remaining." Now it triggers a real inventory correction
that can go either direction — finding *more* units than expected is
just as valid a correction as finding fewer.

**Column reorder**: Task/Container moved from last to right after LPN
(`Line, LPN, Task/Container, Item, Description, ...`), per explicit
instruction. A small UOM column was added between Planned Qty and
Completed Qty (no header text) — cosmetic only, `uomId`'s own
resolution/threading is unchanged (still blank for no-task lines, still
the raw `UomTypeId` value for task lines — see the "Open question"
above).

**One more real bug found and fixed** while live-testing the no-task
completion path in the browser: `complete_container_putaway()`'s
success response never included a `quantity` field at all (unlike the
task-mode path), so a successful completion showed "Completed
undefined  on line N" in the frontend instead of the real amount.
Fixed by including the actual post-adjustment on-hand quantity
(`rows[0]["OnHand"]`, already fetched for the single-item check) in the
response.

## UOM display and conversion, full-width results table (2026-08-08, fifth session)

**UOM display and conversion** — `task_service._package_conversion_factor()`,
ported from `receivingworkbench`'s already-confirmed `rw_service.py`
`_package_conversion_factor()`, per explicit instruction to reference
that app's logic rather than re-derive it. Resolves the real story
behind the "Open question" above: `TaskDetail.UomTypeId` (e.g. `"LPN"`)
is a *code*, not a label — `Quantity`/`CompletedQuantity` are always
base-unit, and the item's `ItemPackage[]` (now fetched by
`mawm_client.search_items()`, Template extended to match
receivingworkbench's exactly) holds the real conversion factor and
human label for that code. **CONFIRMED live**: item `50002217`
(`IBPWIBPT0929`) — raw 240 base units, `UomTypeId="LPN"` resolves to
`ItemPackage` entry `{Quantity: 24, UomId: "units"}` → displays as
`10 Units`, not the misleading raw `"240 LPN"`. Item `3000223`
similarly resolves its own "LPN" code to `Case`/factor 50 — its earlier
50-unit Substitute Location test line (this session's history) really
was exactly 1 Case.

This is a **real, bidirectional conversion**, not just a display fix —
matching how receivingworkbench's own `receive_line()` converts a
user-entered display-unit quantity back to base units before ever
calling MAWM. Every line now carries `uomFactor` alongside the already-
converted `uomId`/`plannedQuantity`/`completedQuantity`/
`remainingQuantity`; `public/app.js`'s Completed Qty inputs
(`.completed-qty-input`/`.mixed-qty-input`) carry `data-uom-factor`, and
`collectItemAdjustments()` multiplies the box's display-unit value back
up by that factor before it's ever sent as `desiredQty` — MAWM and
`adjust_ilpn_quantities()` only ever see base units. No-task/container
lines resolve their factor from the item's own `DisplayUomId` instead
of a per-line code (there's no "shipped as X" concept for on-hand
inventory) — including per-item for a MIXED container's accordion rows.

The UOM label itself shows in its own column between Planned Qty and
Completed Qty (no header text) — `public/index.html`'s `.col-uom`,
added in the fourth session but was rendering blank until this
conversion actually resolved a label to put there.

**Full-width results table**: `.app-shell`'s `max-width: 1200px` /
`margin: 0 auto` were removed (per explicit instruction — "running out
of real estate"). The filters/auth screen keeps its own narrower
centered width via its `.card-panel`'s own inline `max-width: 620px`,
so login/search still reads comfortably narrow while the results
table now uses the full viewport width minus the shell's existing
padding.

**Complete Line / Complete All buttons** moved from centered to
left-aligned (`.action-toolbar`'s `justify-content: center` →
`flex-start`), per explicit instruction.

## Required reason code, "Scan Another" retains search, mixed header UOM (2026-08-08, sixth session)

- **"Scan Another" (`showFilters()`) no longer clears the search box** —
  per explicit instruction ("especially for testing!"). Also selects
  the retained text so typing/scanning still immediately replaces it.
- **MIXED accordion summary row now shows a UOM** in its own column
  when every real item underneath happens to share the same one (blank
  if they differ, same as before) — `renderLineRow()` computes this
  from `line.mixedItems[].uomId` at render time, no backend change.
- **Reason code is now required, not defaulted** — `.reason-code-select`
  starts on a "Select Reason" placeholder (`value=""`), styled red via
  the same `.invalid` class/CSS every other input here already uses,
  instead of silently pre-picking `IA`. `isReasonValid()` (mirrors
  `isQtyValid()`) gates `Complete Line`/`Complete All` alongside
  location and quantity validity: a reason is only required once its
  row's Completed Qty is actually overridden (the select is invisible
  otherwise), but once shown, the placeholder blocks submission until a
  real code is chosen — checked independently per item for a MIXED
  container's accordion. A `change` listener toggles `.invalid` off
  once a real value is picked and re-evaluates button state.

  **Real bug caught live while testing this**: the `.mixed-qty-input`
  input handler toggled the reason select's visibility/styling but
  never called `updateLineActionButtons()` afterward (unlike the
  single-item box, where `validateQty()` already does) — so editing a
  MIXED item's quantity revealed an invalid reason select without
  actually disabling the buttons until something unrelated happened to
  re-evaluate them. Fixed by calling `updateLineActionButtons()`
  directly in that branch too. Confirmed live (buttons disabled on
  reveal, re-enabled the moment a real reason was picked) before
  shipping.

## Task-mode quantity correction: sequence reversed, two-API branch (2026-08-08, sixth session)

**Per explicit domain-expertise instruction**: an LPN still allocated
to an open task cannot be adjusted via Modify iLPN at all. It only
works once the task releases the LPN, which for Putaway means *after*
it's been put away, not before. This is why the allocated-LPN test kept
failing/wasn't reachable — `adjust_ilpn_quantities()` was being called
on an LPN the task still owned.

**`complete_putaway_line()`'s sequence is reversed**: complete the
full, unmodified putaway first (`desired_qty` plays no part in that
call), and only *after* it succeeds, correct the quantity. If putaway
fails or hits a warning, that's returned exactly as before (no
adjustment ever runs). If putaway succeeds but the *adjustment*
afterward fails, the response is `"success": True` (the putaway really
happened, can't be hidden) plus a separate `"adjustmentSuccess": False`
/ `"adjustmentError"` pair (and `"adjustmentTarget"`, `"lpn"` or
`"location"` — see below) — `public/app.js`'s
`completeLine()`/`confirmAllLines()` surface that as its own distinct
message ("completed, but the quantity correction failed") rather than
either a misleading plain success or failure.

**Which of two APIs to use is decided automatically — CONFIRMED live,
correcting an earlier wrong assumption**: `LocationTypeId='STORAGE'`
(the only destination type `validate_storage_location()` allows) does
**not** by itself mean the destination keeps LPN-level inventory — an
earlier note here claimed "every destination reachable through this
app today is in-scope" for Modify iLPN, which was wrong.
`Location.InventoryReservationTypeId` ("LPN" vs "LOCATION") is a
genuinely independent property from `LocationTypeId`; confirmed live
that `A1AC0114`/`C1CS0110` are both `LocationTypeId='STORAGE'` but
`InventoryReservationTypeId='LOCATION'` — putaway there consumes the
LPN into the location's own inventory record, same as a Pick location
would.

Rather than pre-checking the destination location's config, the app
checks the *LPN itself* after putaway — confirmed live as a clean,
reliable signal:

| LPN | Destination `InventoryReservationTypeId` | Post-putaway `Ilpn.Status` |
|---|---|---|
| `LPN000000000315` → `A1AC0114` | `LOCATION` | **9000 ("Consumed")**, `CurrentLocationId` → `null` |
| `LPN000000000010` → `R2R40105` | `LPN` | 3000 ("Not Allocated"), still a live container |
| `LPN00076` → `R1R20701` | `LPN` | 3000 |
| `0000099999000008672` → `R2R40105` | `LPN` | 3000 |

`search_ilpn_current_location()`'s Template now also fetches `Status`
(`mawm_api_library`'s `ilpn_dc_inventory_status` domain — `9000` =
"Consumed", `3000` = "Not Allocated"; that library documents the status
ladder but has no prose anywhere explaining `IsClosed`,
`ConsumeOnLocate`, or PICK/STORAGE/RESERVE tracking behavior — this
table is net-new, from live testing, not something already written
down). `complete_putaway_line()` checks this right after putaway
succeeds: `Status == ILPN_CONSUMED_STATUS` routes to
`adjust_location_quantities()` (new — see below) against the actual
destination used (`putaway_result["toLocationId"]`, correct whether
default or Substitute-Location-overridden); otherwise
`adjust_ilpn_quantities()` (unchanged) against the LPN, same as before.

**`adjust_location_quantities()` / `mawm_client.adjust_location_inventory()`**
— per `mawm_adjust_location_api.md`, a **third** distinct MAWM API in
this app (`POST dcinventory/api/dcinventory/inventory/adjustInventory`,
a raw JSON array body — an object wrapper causes a
`NullPointerException`, confirmed in the document). Unlike Modify
iLPN's `ScannedQuantity`, this endpoint's `Quantity` is a genuine
signed **delta** (`New OnHand = Current + Quantity`) with no
absolute-value shortcut, so the function computes
`delta = desired − current` itself. **CONFIRMED live, both directions,
against two different locations**, independently verified by re-query
each time:
- `A1AC0114` item `3000223` (a single clean inventory record): 50 → 51
  (add) → 49 (subtract) → 50 (restored).
- `C1CS0110` item `50002236` — a real example of the "multiple
  inventory records for the same item at one location" case the
  document warns about: 4 records, all identical attributes (batch,
  product status, country of origin, `InventoryAttribute1` all null —
  nothing to distinguish them by), `OnHand` 0/10/10/10. This app's
  code doesn't disambiguate (takes the first match — see
  `adjust_location_quantities()`'s docstring), and it worked correctly
  anyway: the same record (by `InventoryId`) came back first across
  three separate queries (baseline, post-add, post-subtract), so
  0 → 1 (add) → 0 (subtract) landed on that one record consistently,
  confirmed by checking every record's `InventoryId` individually, not
  just the location's total. **This is empirical, not a documented
  guarantee** — MAWM's search result ordering happened to be stable
  across this test, but nothing confirms it's *always* stable, so a
  location with genuinely ambiguous duplicate records is still a real
  risk for a future call, just a lower one than initially assumed.
  `DCI::313` (stale record) handling is best-effort/**UNCONFIRMED** —
  the document only describes it in prose, no captured error response
  body exists, so the retry trigger is a text-match on
  `LOCATION_ADJUSTMENT_STALE_RECORD_CODE` rather than a confirmed
  envelope shape (not actually triggered in either test above — both
  succeeded on the first attempt).

**CONFIRMED live, 2026-08-08 (eighth session)** — the full *integrated*
chain inside `complete_putaway_line()`, first successful end-to-end
test: `IBPWIBPT0105` (item `50002236`, `LPN00760`, planned qty 10,
destination `C1CS0110`), completed with `desired_qty=9`
(`item_id`/`lpn_id` from the loaded line, `adjustment_reason_code:
"IA"`). Result: `{"success": true, "quantity": 10, "adjustmentTarget":
"lpn", "adjustmentSuccess": true}`. Independently re-verified, not just
trusted: the task itself shows `Completed`/`completedQuantity: 10` (its
own unmodified work-order record — the full putaway that ran first),
while `LPN00760`'s own container inventory genuinely holds **9** units
(a direct re-query, not the response body) — the correction landed on
the LPN itself, exactly as `adjustmentTarget: "lpn"` reported.

**This corrected a wrong assumption, not just confirmed the mechanism**:
`C1CS0110` is `InventoryReservationTypeId='LOCATION'` — the earlier
(no-task/DMM-flow) testing predicted this destination would consume the
LPN, routing to `adjust_location_quantities()`. It didn't happen here —
the LPN stayed a live, independently-adjustable container
(`Ilpn.Status` came back `"3000"` / "Not Allocated," not `"9000"` /
"Consumed"). Best current read: **task-mode completion (Path C,
`fetchNextPutawayMoveAndStartLaborActivity`/`commitAndFetchNextMove`)
and no-task completion (the DMM Mobile Facade's "User Directed
Putaway," `AcceptContainer`/`AcceptLocation`) are different MAWM code
paths and may follow different LPN-consumption rules for the very same
destination** — one data point isn't proof of a general rule, but it's
a direct, live contradiction of the earlier assumption, so don't trust
`InventoryReservationTypeId` alone to predict task-mode behavior. This
is exactly why the auto-detection checks the *LPN's actual post-putaway
status* rather than pre-guessing from the destination's config — that
design choice is what made this test succeed correctly despite the
wrong assumption underneath it. **Still not live-tested**: a task-mode
completion that *does* get auto-routed to
`adjust_location_quantities()` (i.e., a real allocated task whose LPN
genuinely comes back `Status: "9000"` after Path C putaway) — not yet
observed even once; whether that's rare, or task-mode Path C simply
never consumes the LPN at all, is still an open question.

Also revealed: `search_ilpn_current_location()`'s `CurrentLocationId`
came back `null` for this LPN too, same as a consumed LPN — but its
`Status` was `"3000"`, not `"9000"`. So `CurrentLocationId: null` alone
does **not** reliably mean "consumed" (`_ilpn_display_fields()`'s `*`-
marked-previous-location fallback already only fires on `Status ==
ILPN_CONSUMED_STATUS`, not on a blank `CurrentLocationId` by itself, so
this doesn't change that logic — but it does mean a task-mode line
whose LPN ends up in this state shows a genuinely blank Current
Location with no `*` fallback, which is accurate, just worth knowing
isn't the same situation as a truly consumed LPN).

**Repeated with a second, independent task/LPN — same result.**
`IBPWIBPT0110` (item `50002236`, `LPN00764`, planned qty 10, same
destination `C1CS0110`), `desired_qty=9`: `adjustmentTarget: "lpn"`
again, and re-queried directly (not just trusted): `LPN00764` stayed
`Status: "3000"` (not consumed), its own container inventory shows
`OnHand: 9.0` (the correction landed on the LPN), and `C1CS0110`'s
location inventory for `50002236` was untouched by the adjustment —
still the same 0/10/10/10 rows (one row's `UpdatedTimestamp` lines up
with this putaway move and stayed at `0.0`, so that's the move itself
touching a location-level shadow record, not the adjustment logic
firing). Two different tasks/LPNs to the same `LOCATION`-typed
destination now agree, which weighs against this being a one-off
bad-data artifact — but per the user, still worth confirming by hand
in WM after a refresh, since two data points from the same test
environment/config could still share a root cause. To re-check
manually: find a fresh allocated task headed to `C1CS0110` (or another
`InventoryReservationTypeId='LOCATION'` destination), complete the
putaway there, and see whether WM shows the LPN as consumed (no longer
independently adjustable) or still live. `IBPWIBPT0105`/`LPN00760` and
`IBPWIBPT0110`/`LPN00764` are both already used up, so this needs a
*different* fresh task.

**Third test (`IBPWIBPT0052`) found a real bug, not bad data** — this
task was structurally different from the first two: `taskTransactionId:
"Storage Putaway"` (not `"Putaway"`), `lpnId` equal to `toLocationId`
itself (`A1AC0212`), `ilpnStatus` blank, `uomId: "Units"` not `"LPN"`.
`search_ilpn_current_location('A1AC0212', ...)` returned `null` — there
was never a real ILPN container for this line at all; it's a loose/
location-direct putaway. The routing logic's `consumed` check
(`bool(ilpn) and Status == "9000"`) treated "no record found" the same
as "not consumed," so it wrongly went down the LPN-adjustment branch —
which then threw an **uncaught `RuntimeError`** trying to refresh a
read-timestamp on a container that doesn't exist (MAWM returned a raw
500, "Could not commit JPA transaction"). The putaway itself had
already committed by that point (task went to `Completed`,
`completedQuantity: 10`) — only the follow-up adjustment crashed, and
it crashed the whole call instead of degrading gracefully.

Fixed in `complete_putaway_line()`: `ilpn is None` now routes to the
location branch (renamed `consumed` → `route_to_location`), on the
reasoning that no ILPN record at all means there's no LPN-level
inventory to adjust — same effective situation as a consumed LPN, just
arrived at differently. Also wrapped both adjustment calls in
`try/except` so a crash there degrades to `adjustmentSuccess: False` +
`adjustmentError` instead of taking down the whole response, since the
putaway has already committed by that point regardless. **Not yet
re-verified live** — `A1AC0212`'s inventory is already muddled by this
test (two existing rows, 40.0/20.0, now including this task's
uncorrected 10 units merged in somewhere) and the task itself is
already `Completed`, so a clean re-test needs a fresh task, which lines
up with the user's own plan to create new manual putaway tasks after a
data refresh rather than keep testing against old data.

## LPN status badge, consumed-location display (2026-08-08, seventh session)

- **`mawm_client.ILPN_STATUS_LABELS`/`ilpn_status_description()`** — the
  confirmed `ilpn_dc_inventory_status` domain (1000 In Transit, 2000
  Pre-Receipt Allocated, 3000 Not Allocated, 4000 Partially Allocated,
  5000 Allocated, 9000 Consumed, 10000 Lost, 11000 Canceled), mirroring
  `TASK_STATUS_LABELS`/`task_status_description()`'s existing pattern.
  A genuinely different ladder from `TASK_STATUS_LABELS` and from
  receivingworkbench's own `LPN_STATUS_LABELS` (that one's the
  *receiving-component* ASN-nested `LpnStatus` domain — same numeric
  codes, different meanings; `mawm_api_library` is explicit the two
  must not be merged).
- **Always shown now, both modes** — per explicit instruction ("let's
  always display LPN status so it's consistent"). No-task/container
  lines already did an iLPN lookup (`search_ilpn_current_location()`,
  Template now also fetches `PreviousLocationId`); task-mode lines
  didn't do one at all before this, so `_build_task_response()` now
  batches one `search_ilpn_statuses()` call (new — the same `IlpnId in
  (...)` convention `search_items()` already uses for items) covering
  every line's own LPN in one round trip, not one call per line.
  `_ilpn_display_fields()` is the shared helper both code paths call.
- **Consumed-location display, confirmed live on two real LPNs**:
  `LPN000000000006` (the user's own test case) and `LPN000000000315`
  (this session's own earlier test) — both `Status: 9000`,
  `CurrentLocationId: null`, and `PreviousLocationId` holding exactly
  the location the LPN was consumed into (`A1AC0401` and `A1AC0114`
  respectively — the latter matching exactly where this app put it
  away earlier). When `Status == ILPN_CONSUMED_STATUS` and
  `CurrentLocationId` is blank, the no-task line's Current Location
  shows `PreviousLocationId` with a trailing `*` (e.g. `A1AC0401*`) —
  the user's own suggested notation — so there's still something
  useful to show instead of blank, while staying visually distinct
  from a real current location. **Task-mode lines' own Current
  Location is deliberately NOT touched by this** — that column there
  is `TaskDetail.SourceLocationId` (where the task expects to pick
  from), a different concept from the iLPN's own tracked location;
  only the status badge applies to task-mode lines, not the `*`-marked
  location fallback.
- **Placement — the LPN column itself, not a new column**: the
  existing "Task/Container" column was the obvious other candidate
  (it already shows a status badge), but it's hidden by default for
  the common single-group case (`multi-group` CSS class), which would
  have silently broken "always display" for the majority of searches.
  `lpnCellHtml()` puts the badge directly under the LPN id instead,
  which is always visible regardless of group count.
- **Color rule — its own, distinct from the task-status badge's
  red/green split**: `ilpnStatusBadgeClass()` — Consumed is grey
  (`.badge.status-grey`, new CSS), Lost/Canceled stay red, everything
  else green. Per explicit instruction: Consumed is the *expected*
  outcome after putaway, not a success or a problem, so it doesn't fit
  either end of the existing red/green rule.
- Confirmed live via both `resolve_search_multi()` output and a
  browser render: `LPN000000000006` → grey "Consumed" badge +
  `A1AC0401*`; `IBPWIBPT0109`'s line (`LPN00763`) → green "Allocated"
  badge, `STAGIB0204` (its real TaskDetail source location) unchanged.

## Consumed LPNs are read-only (2026-08-08, seventh session)

Per explicit instruction: a consumed LPN's inventory has already moved
to a location record — there's nothing left on the LPN to adjust or
putaway, so it's now blocked at every layer rather than relying on any
single check:

- **Frontend inputs disabled outright** — `toLocationCellHtml()`'s To
  Location input and the Completed Qty input (both the single-item box
  and every mixed-item sub-row's box, keyed off the *parent* line's
  status since only the container carries its own iLPN status) get
  `disabled` + an explanatory `title` tooltip when `isConsumedLine(line)`
  (`line.ilpnStatus === "9000"`, mirroring `mawm_client
  .ILPN_CONSUMED_STATUS`). `validateLocation()` also skips a disabled
  input rather than showing it red/"invalid" — a disabled empty box
  reading as an error would be misleading when there's nothing to fix.
- **Buttons gated explicitly** — `updateLineActionButtons()`/
  `allOutstandingLinesValid()` both add `!isConsumedLine(...)` alongside
  the existing location/qty/reason checks, and `completeLine()` shows
  its own clear message ("already been consumed and can no longer be
  updated") if a consumed line is ever selected, mirroring the existing
  "already complete" pattern.
- **Backend guard, defense in depth** — `complete_container_putaway()`
  now looks up the container's iLPN status itself and refuses outright
  (before touching anything else — adjustment, workflow_init, all of
  it) if `Status == ILPN_CONSUMED_STATUS`, so a direct API call can't
  bypass the frontend's own restriction. Confirmed live against
  `LPN000000000006`: `{"success": false, "error": "This LPN has
  already been consumed — its inventory moved to a location and it can
  no longer be updated."}`.
- Confirmed via browser render too: both inputs correctly greyed out,
  Complete Line correctly disabled, no red "invalid" flash on the
  disabled To Location.
## Consumed LPN item/qty history (2026-08-09, tenth session)

The gap flagged above — a consumed LPN's own inventory/location come
back empty, so there was nothing to show for what it actually held —
is now filled in, via a genuinely different MAWM component (DMUI
Search's Activity Tracking view, not dcinventory/task/inventory-
management like everything else in this app).

- **`mawm_client.search_ilpn_activity_history(container_id, ...)`** —
  `POST dmui-search/api/dmui-search/entity/search`, filtered only by
  `ContainerId` (no item/transaction/date filter, per the source
  document — deliberately returns the LPN's complete event history so
  the selection logic below can pick the right one). A different shape
  from every other call in this app: `Filters`/`ViewName` instead of
  `Query`/`Template`, and the real rows live under `data.Results`, not
  `data` directly.
- **`task_service.select_terminal_ilpn_activity(records)`** — the
  confirmed-by-user-capture selection logic
  (`mawm_lpn_history_api_and_selection_logic.md`): group rows
  representing the same business event (same `TraceId` +
  `ActivityDateTime` + `TransactionTypeId` + `TransactionId` — a single
  Modify iLPN adjustment can be split across two published rows, each
  carrying different fields, merged here so nothing gets lost to
  whichever row got picked); prefer a row whose transaction explicitly
  represents consumption if one exists (not confirmed to ever actually
  occur — the source document's own example never had one); otherwise
  the latest `ActivityDateTime` row that moved the LPN to
  `ILPN_CONSUMED_STATUS`.
- **Wired into `resolve_search()`'s no_task branch, per explicit
  instruction to reuse the existing Item/Qty columns** — the same way
  Current Location already gets a `*`-marked `PreviousLocationId`
  fallback, not new dedicated fields. `itemId`/`plannedQuantity`/
  `remainingQuantity` all get a trailing `*` when populated this way
  (`"5000221*"`, `"6*"`) — the user's own words: "we can do the same
  for item and qty for now until I see it working and then perhaps we
  can remove the * indicator." `plannedQuantity`/`remainingQuantity`
  become **strings** in this case (`_num("6*")` would silently
  collapse to 0 — `_dec()` swallows the parse failure — so the marked
  value bypasses `_num()` entirely) — confirmed this doesn't break
  anything downstream: a consumed line is already excluded from
  `remainingQty()`-based gating via `isConsumedLine()` checks
  everywhere that matters, and the one place `remainingQty()` still
  runs unconditionally (`Number("6*") → NaN → 0`) degrades to exactly
  the right answer anyway ("nothing remaining" is correct for an
  already-consumed line).
- **CONFIRMED live, twice, plus a clean negative case**:
  - `LPN000000000006` (the source document's own example) — live API
    call, not the document's cached response — returned exactly
    `itemId: "5000221*"`, `description: "Floral Print Dress"`,
    `fromLocationId: "A1AC0401*"`, `plannedQuantity: "6*"`, matching
    the document's derivation precisely. Confirmed rendering correctly
    through the real browser UI too.
  - Most *other* consumed LPNs in this org (`LPN00479`,
    `LPN000000000496`, `LPN01007`, etc.) have **no**
    `PreviousLocationId` at all, unlike `LPN000000000006` — a real
    stress test for whether activity history could find something the
    old location-only fallback couldn't. It didn't: `LPN00479` has
    **zero** Activity Tracking records (confirmed directly — real
    seed/imported demo data has no transactional history, not a bug).
    Correctly degrades to the same blank fields as before this
    feature existed, no crash, no regression.
  - A normal non-consumed LPN with real inventory (`LPN00760`)
    confirmed completely unaffected — plain numeric quantity, no `*`,
    exactly as before.

## Known-good test Task Id

Refreshed 2026-08-08 (third session) — the demo environment's data
drifts as tests consume it (and appears to periodically reset/reseed
independently — several tasks below have flipped status more than once
across sessions without this app changing anything), so treat
"current" state here as a snapshot, not a guarantee; re-check live
before trusting an old note over what MAWM actually returns.

**Fully spent (Completed) as of this writing** — need a fresh task to
exercise Complete Line / Complete All again:
- `IBPWIBPT0929` (`LPN00076`, item `50002217`, 240 units → `R1R20701`)
- `IBPWIBPT0221` (`LPN000000000315`/`A1AC0114`, item `3000223`, 50
  units → `A1AC0119`, Substitute Location)

**Known live data conflict, not a code bug**: `IBPWIBPT0109`
(`LPN00763`, 10 units remaining) reads fine (`Status: Ready For
Assignment` via `resolve_search`), but its Path C fetch
(`fetchNextPutawayMoveAndStartLaborActivity`) returns `FWTSK::019`,
*"Task IBPWIBPT0109 cannot be assigned to user demoweb@ss-demo"* —
confirmed live, 2026-08-08. Don't use this task for write-path testing
until that clears; it's exactly the kind of thing
`extract_message()`'s fallback fix (above) was for, not something this
app can work around.

**No open task (`mode: "no_task"`), useful for the container/DMM
flow**: `LPN00076` (now that `IBPWIBPT0929` is Completed — its task no
longer counts as "open" — current location `R1R20701`) and
`LPN000000000010` (item `5000225`, now 190 units at `R2R40106`,
following the Modify iLPN + Complete Line browser test above). Note
`R2R40105`/`R2R40106`/`R2R61001` all appear to be *destination
locations* dedicated to a specific item (each reliably reproduces
`DCI::120`, "Location permanently dedicated to a different item," for
these test items) — that warning is a property of the destination, not
something that "clears" on the source LPN over time.

**Modify iLPN / multi-item test containers**: `0000099999000008672`
(the source document's own test iLPN) — `6000105` was deleted via the
qty-0 test (fourth session), then item `8000145` reappeared on it
(added back via the user's own Postman testing, matching the
document's own add-line example) making it genuinely MIXED again; used
in the fifth session to confirm multi-item putaway completion works —
now single-item again, `6000106` (10 units) + `8000145` (2 units) both
at `R2R40105`. `0000099999100000772` was a real MIXED container (items
`4000042`/`4000043`) used to prove the accordion end-to-end — now
single-item (`4000042`, 3 units) at `R2R40105`, `4000043` zeroed out.
`0000099999000005596` is still genuinely MIXED (3 items —
`6000102`/`6000103`/`6000104`) and untouched, if another real MIXED
container is needed for testing.

## Status badge

`taskStatusLabel` (Tier 1, `mawm_api_library/_conventions/statuses.json`
domain `task_status`) drives a vasexecution-style soft-chip badge
(`public/app.js`'s `statusBadgeClass()`/`statusBadgeHtml()`, CSS in
`public/index.html`). Current rule, per explicit instruction: Completed
and Canceled render red, everything else green — a placeholder, not a
final design; revisit if the status set needs more granularity later.

## Ad hoc Cycle Count (2026-08-08, ninth session)

First feature outside Putaway. Ported from the sibling `cyclecount` app
(`C:\Users\ssmith\Personal\Development\work\cyclecount`, v1.3.1) —
same host/auth, a different MAWM component
(`inventory-management`/`dcinventory`, not `putaway`/`task`).

**Search-box integration**: the existing "Task Id or iLPN" box now also
accepts one or more Storage locations (same `;`/`,`/whitespace
delimiter as the existing multi-search). `public/app.js`'s
`classifySearchInput()` checks each token against the already-preloaded
`state.storageLocations` set (see `preloadStorageLocations()`) — all
tokens recognized as locations → `cycle_count` mode; all
unrecognized → `task` mode; a mix of both → **Load Task is disabled**
with an explanatory hint (per explicit instruction: mixing is not
allowed, not auto-resolved by guessing intent). `state.searchMode`
drives this at type-time; `state.lastSearchMode` remembers which mode
the *last successful load* used, so `reloadCurrentSearch()` calls the
right endpoint.

**Separate results table** (`#cycleCountLinesTable`/`#cycleCountLinesBody`,
per explicit instruction over conditional columns on the putaway table
— confirmed live in the browser): columns are Line/Location/Item
(editable text input)/Description/Qty/Result. The Qty box starts
genuinely blank (not pre-filled with current on-hand) and is **forced**
— `isCycleCountQtyValid()` checks the raw string is non-empty before
`Number()`, so an untouched blank box doesn't silently pass as "0" the
way `Number("") === 0` would otherwise allow; `0` typed explicitly is
valid. A location with more than one genuinely distinct item (after
`resolve_cycle_count_location()`'s ItemId-dedup, see below) renders as
the same MIXED accordion pattern already used for multi-item no-task
containers — confirmed live for the single-item case; the accordion
path for a real multi-item location is unconfirmed (no live example
found yet).

**Backend**: `task_service.resolve_cycle_count_search_multi()`
(parallel to `resolve_search_multi()`) → `resolve_cycle_count_location()`
per location, using `search_location_inventory()` (already existing,
same `dcinventory` endpoint the old app's `getInventory` called
directly) for item-from-location lookup.

**Critical fix, confirmed live 2026-08-08**: `resolve_cycle_count_location()`
**deduplicates by ItemId** — a location can carry more than one raw
inventory *record* for the same item (`C1CS0110` came back as 4 rows
all for item `50002236`; same duplicate-record pattern already
documented for `adjust_location_quantities()`). Without this, the
first cut of this feature rendered 4 identical duplicate lines with
colliding keys. One line per distinct ItemId, not one line per row.

### The six-call completion chain, and why the first cut of it was wrong

`task_service.complete_cycle_count_line()` runs `initiateCount` →
`validateItemAndGetItemDetails` → `acceptQuantity` →
`persistCountDetails` → `endCount` (`mawm_client.py`'s Cycle Count
section has each call's payload shape, ported from the old app).

**The first implementation auto-overrode a quantity-mismatch WARNING
from `acceptQuantity` and reported success** — this looked plausible
(HTTP 200/201 the whole way through) but was **directly contradicted
by live testing**: counting `A1AC0114` at 5 when the real on-hand was
50 reported "Completed" while on-hand silently stayed 50. Same result
at two more locations, including one that never had any inventory at
all (`A1AC0117`, counted for the first time — stayed empty). The
reference `cyclecount` app has the identical payload shape and almost
certainly shares this exact gap; it apparently was never caught because
its own success reporting never independently re-verifies the write —
exactly the trap this whole project's live-verification-over-trusting-
`success:true` methodology exists to catch.

**Explained by the user, then confirmed live against a real 3-location
sample file** (`C:\Users\ssmith\OneDrive - Manhattan
Associates\Desktop\cyc.txt`: `A1AC0123`→240, `A1AC1201`→39,
`A2AC1201`→10) and a document the user captured
(`mawm_cycle_count_location_investigation.md`) — this isn't a bug in
the six-call chain at all. None of those six calls' own HTTP status or
messages reveal whether a count actually gets applied; MAWM books (or
doesn't) **asynchronously**, and the real outcome is only observable
via three separate read-only endpoints, none previously known to any
app in this workspace:

- `search_location_count_info()` → `POST dcinventory/api/dcinventory/locationCountInfo/search`
  — the location-level lock flag, `CycleCountPending` (`true` while a
  count is unresolved, `false` once resolved — "resolved" isn't the
  same as "succeeded").
- `search_inventory_count_runs()` → `POST inventory-management/api/inventory-management/inventoryCountRun/search`
  — every count run ever attempted for a location; each row nests its
  own `ContainerCount[]`/`InventoryCount[]` detail (the same data
  `inventoryCountResult` below returns flat).
- `search_inventory_count_results()` → `POST inventory-management/api/inventory-management/inventoryCountResult/search`
  — the flat, item-level detail for one `CountRunId`:
  `OriginalQuantity`/`CountQuantity`/`VarianceQuantity`/`Status` (a
  human label, not just a code)/`BookingFailureReason`. This is what
  `complete_cycle_count_line()` polls.

Tested with and without a `Template` on all three — identical full row
either way; the document's templated field list is the complete set.

**CONFIRMED live, three real outcomes** (the sample file's three
locations, then reproduced again through the corrected production code
path on fresh, never-before-touched locations):

1. **Perfect match** (`A1AC0123`: counted 240 = on-hand 240;
   `A1AC0122`: counted 996 = on-hand 996) — every call clean 2xx, no
   warning anywhere, books synchronously. `inventoryCountResult.Status:
   "Booked"`, `StatusKey: "80"`.
2. **Within tolerance** (`A1AC1201`: counted 39 vs on-hand 41, -2
   variance) — `acceptQuantity` returns HTTP 400 with `INM::227`
   ("Quantity mismatch") as a `WARNING` only (no `ERROR`). The count
   still books, but **asynchronously** — an immediate re-query after
   `endCount` returns can still show the pre-count value;
   `complete_cycle_count_line()` polls `inventoryCountResult` up to 5
   times, 0.6s apart, same posture as Modify iLPN's `endIlpn`
   elsewhere in this app.
3. **Out of tolerance** (`A2AC1201`: counted 10 vs on-hand 20, -10
   variance; `A1AC0127`: counted 890 vs on-hand 898, -8 variance) —
   `acceptQuantity` returns the same `INM::227` WARNING **plus**
   `INM::411` ("Recount required") as an `ERROR`. `persistCountDetails`/
   `endCount` still both report clean success, but the count run parks
   at `Status: "Pending Booking"` (`StatusKey: "35"`) indefinitely — not
   an async delay, confirmed by re-checking after a 6-second wait and
   again minutes later. `locationCountInfo.CycleCountPending` flips
   `true` and stays there — the location is genuinely locked pending a
   real human/supervisor booking decision outside this app's reach.

Exact tolerance thresholds are MAWM-side configuration, not something
this app determines or should try to reverse-engineer — the -8/898
(~0.9%) case went out-of-tolerance while the -2/41 (~4.9%) case stayed
within it, so it is **not** a simple percentage cutoff.

`complete_cycle_count_line()` now runs the full six-call chain to
completion regardless of what any individual step reports (a WARNING
or ERROR message at validate/accept is not, by itself, treated as
failure — confirmed live that both still let the chain proceed all the
way to `endCount` returning 200), then polls for the real outcome.
`success` is only `True` when the poll finds `Status == "Booked"`;
`"Pending Booking"` and any other non-Booked status come back as
`success: False` with the real status, variance, and
`bookingFailureReason` attached — distinct from an actual chain
exception (network failure, unparseable response), which still stops
the chain early and returns a plain error.

The **frontend** (`cycleCountResultText()`/`cycleCountResultKind()`
in `app.js`) shows three distinct Result-cell states matching this:
green "Booked: X → Y" (done, inputs disabled), muted/italic "Pending
supervisor booking… Location locked — not yet applied" (left editable,
not marked done — a corrected recount is plausible), red for a genuine
failure. `locationLockedBefore`/`locationLockedAfter` are returned by
`complete_cycle_count_line()` but not yet surfaced in the UI.

**Progressive UI, added same session per explicit instruction** ("it
sometimes takes the system a few seconds to update to booked... first
display the [in-flight] status and then poll a few times to update
that field once its booked... show the previous/counted qty and the
variances... so the user can see the full result"):

- `complete_cycle_count_line()` no longer blocks synchronously waiting
  for booking — it runs the six-call chain, checks
  `inventoryCountResult` **once** immediately, and returns whatever
  status that shows right then (often still `"Count Initiated"` or
  `"Pending Booking"`), instead of the earlier version's ~3s blocking
  poll loop before responding.
- New `check_cycle_count_status()` (`POST /api/check_cycle_count_status`,
  `{locationId, itemId, countRunId}`) is a lightweight re-check —
  same `inventoryCountResult` lookup, no chain re-run. `app.js`'s
  `pollCycleCountLineStatus()` calls it after any non-`"Booked"`
  completion result, live-updating the row each time. There's no way
  to tell an eventually-resolving in-flight status apart from a
  genuinely stuck out-of-tolerance one from the status text alone, so
  it always polls the full window regardless — confirmed live for
  both: a within-tolerance count visibly flipped from red "Count not
  booked (status: Count Initiated)" to green "Booked: 10 → 9
  (variance -1)" a few seconds later; an out-of-tolerance count
  settled into the muted "Pending supervisor booking…" state and
  correctly stopped polling rather than spinning forever.
- Table gained two new columns, **Previous Qty** and **Variance**
  (`.cc-previous-qty`/`.cc-variance` cells, populated by the shared
  `applyCycleCountResultToRow()` used by both the initial completion
  and every subsequent poll tick) — both empty until a real
  `inventoryCountResult` row exists.
- Polling is fire-and-forget (not awaited by the caller) so **Complete
  All** isn't blocked waiting for each line's booking to resolve
  before moving to the next line — each line's poll runs independently
  in the background.

**Real near-miss found and fixed the same session** — the poll window
started at 8 attempts × 1.8s (~14.4s), sized from a single isolated
test. Running Complete All across 3 real sample locations
(`A1AC0123`/`A1AC1201`/`A2AC1201`) exposed it as too tight: measured
live via `inventoryCountRun`'s `Created`/`UpdatedTimestamp`,
`A1AC1201`'s real booking took **~14.7 seconds** — past the old
cutoff by about 300ms, so polling gave up moments before the
resolution landed, leaving the row stuck on "Count not booked" even
though the count had, in fact, booked (confirmed by re-querying
`inventoryCountRun`/`search_location_inventory` directly afterward —
`Status: 80` "Booked", `OnHand` matching the counted quantity).
Widened to **30 attempts × 2s = 60s** — `check_cycle_count_status()`
is a cheap read, so the wider window costs little even for the
out-of-tolerance case that never resolves and just polls uselessly
until giving up. Re-confirmed live afterward: Complete All across
`A1AC0134`/`A1AC0139`/`A1AC0140` (perfect/within-tolerance/out-of-
tolerance) all resolved correctly, including the within-tolerance
line flipping from "Count not booked (status: In Booking)" to
"Booked: 9 → 8 (variance -1)" a few seconds later.

Also fixed in the same pass: Complete All's failure/issues summary
used to read "Line 1: ...; Line 1: ...; Line 1: ..." for a 3-location
batch — every location's own line numbering restarts at 1 (see
`resolve_cycle_count_location()`), so bare `lineNumber` is ambiguous
once more than one location is involved. `cycleCountFailureLabel()`
now always prefixes with the location. The summary banner itself was
also reworded — it used to read as a final tally ("Completed 0 of 3
lines"), which is misleading given booking is asynchronous and a line
counted as an "issue" the instant the loop finishes may still book
moments later via the background poll.

**WM-matching lock icon + terser Pending Booking display (2026-08-08,
same session, per explicit instruction with a reference screenshot of
the real WM UI)**:

- **Location lock icon**: `resolve_cycle_count_location()` now also
  calls `search_location_count_info()` up front and returns
  `locationLocked` on both the group and every line (denormalized the
  same way as `groupLocationId` etc), so the frontend knows a
  location's `CycleCountPending` state **before any count is even
  started** — confirmed live: searching two already-locked locations
  (`A1AC0140`/`A2AC1201`, still pending from earlier same-session
  testing) showed the icon immediately on load, with no completion
  action taken yet. No source asset for the real WM icon (two circular
  arrows) was found anywhere on disk despite a real search — `app.js`'s
  `cycleCountLockIconHtml()` uses Font Awesome's `fa-arrows-rotate`
  (already loaded in this app) as the closest equivalent rather than
  guessing at/fabricating a custom icon. Always rendered in the row's
  HTML but display-toggled (`setCycleCountLockIcon()`) so a later
  completion/poll result can show or hide it live without re-rendering
  the row — "before or after count," per explicit instruction.
- **Terser, red Pending Booking message**: was a full sentence
  ("Pending supervisor booking (out of tolerance): was 20, counted 10
  (variance -10). Location locked — not yet applied.") in muted grey;
  now "Pending Booking 20 → ~~10~~ (variance -10)" in red — the counted
  qty (not the still-current previous qty) struck through, since that's
  the value that didn't actually take effect. `setCycleCountResultCell()`
  switched from `textContent` to `innerHTML` to support the `<s>` tag;
  `cycleCountResultText()` still escapes any free-text MAWM
  error/failure-reason string before it reaches that innerHTML, since
  the numeric qty/variance fields are always trusted API values but
  error text technically isn't.
- **Variance dollar value**: `_cycle_count_result_response()` now also
  returns `varianceValue` (MAWM's `VarianceValue` field, confirmed in
  the user-captured investigation doc). Shown in the Variance column as
  smaller grey text to the right of the quantity variance — "10 ($50)"
  per explicit instruction — rounded to whole dollars,
  accounting-style parens for negative (no explicit minus sign inside,
  matching the requested example literally) via
  `formatVarianceValueHtml()`.
- All three confirmed together live: `A1AC0142` (baseline 574, counted
  200) rendered the red lock icon next to the location, "Pending
  Booking 574 → ~~200~~ (variance -374)" in red, and "-374 ($1870)" in
  the Variance column.

**Consolidated into 3 stacked lines + always-parens variance
(2026-08-08, same session, per explicit follow-up instruction)**:

- The separate Previous Qty and Variance *columns* are gone — folded
  into the Status column (`.col-cc-result`, widened to `16rem`) as
  three stacked `<div class="cc-result-line">` rows: status label /
  `before → after` (counted struck through unless `success`) /
  `variance (variance-value)`. Applies uniformly to every real status
  MAWM returns, not just "Booked"/"Pending Booking" — confirmed live:
  an in-flight "Count Initiated" result already showed all three lines
  ("Booked" / "20 → 15" / "-5 ($0)" once resolved).
  `setCycleCountResultCell()` switched to `innerHTML` for this (see
  above); `cycleCountResultText()` builds the three divs directly.
- `formatVarianceValueHtml()` now **always** parenthesizes the dollar
  value regardless of sign (2026-08-08, revised again per explicit
  instruction — the first cut only did accounting-style parens for
  negative) — confirmed live showing `($0)` for a zero-value variance,
  not a bare `$0`.
- Description column narrowed (`.col-desc-narrow`, `max-width: 12rem`)
  to give the wider Status column room; overflowing text truncates
  with an ellipsis and the full text is still available via a `title`
  tooltip. **Real CSS gotcha found and fixed while verifying this
  live**: `max-width`/`overflow`/`text-overflow` applied directly to
  the `<td>` silently does nothing in a normal (auto-layout) HTML
  table — confirmed via direct DOM measurement
  (`scrollWidth === clientWidth`, i.e. no truncation happening at all)
  before the fix. Wrapping the text in an inner `<div>` and applying
  the truncation styles to *that* div instead fixed it (confirmed via
  the same measurement after the fix, `scrollWidth 497 > clientWidth
  192`, using a synthetic M&M-candy-length description since no real
  item in this demo org has one long enough to trigger it naturally).

**Open, unexplained, and deliberately not chased further**:
`A1AC0124` (an item location with 24+ pre-existing `inventoryCountRun`
rows already on it — clearly reused/heavily-seeded demo data, not a
fresh location) produced a count run stuck at `Status: "Count
Initiated"` (`StatusKey: "20"`, a status not seen anywhere else) with
a GUID-shaped `TaskId` instead of the `CCNTINM0005xx`-style TaskId
every other test produced, and never progressed even after several
minutes. Also seen once: `StatusKey: "95"` on some of that location's
historical runs, meaning unknown. Given the heavy pre-existing count
history specific to that one location, this reads as a location-
specific artifact of the demo data rather than a general behavior —
not reproduced on any genuinely fresh location — but flagged here
rather than silently ignored.

## Multi-item location: atomic completion required (2026-08-08, tenth session)

**Test location found**: `A1AC0924` — `LocationTypeId: STORAGE`,
`InventoryReservationTypeId: LOCATION` (non-LPN-tracked), two genuinely
distinct real merchandise items (`5000001` "10 Pack SD Rice", OnHand
1739; `5000002` "10 Pack SD Wheat", OnHand 750) — found by a broad
`dcinventory/inventory/search` for `InventoryContainerTypeId='LOCATION'`
grouped by `LocationId` in Python to find one with 2+ distinct
`ItemId`s. Most multi-item hits in this org are equipment/asset
records (`GAYLORD`, `CHEP PALLET`, `CAGE`, etc), not real SKUs — this
one was the only clean real-merchandise match. Never locked, 0 prior
count runs, before this session's testing.

**Per-item independent completion does NOT work for a multi-item
location — confirmed live, then fixed.** The original design (each
item runs its own full `initiateCount`→...→`endCount` chain,
independent of its siblings) was based on an untested assumption.
Real behavior, confirmed by completing just one of the two items:

- The count parks at `"Count Initiated"` **indefinitely** — polled for
  30+ seconds straight with zero change, not just "a bit slower."
- Calling `endCount` before every item at the location is addressed
  returns HTTP 400 with `INM::230` ("Not all the Items in the Location
  are counted") — a real `WARNING`, not a silent "unaddressed item
  counts as 0" default. Confirmed safe by direct verification: the
  addressed item's result stays at `"Count Initiated"` (not falsely
  booked), the *unaddressed* item has **no `inventoryCountResult` row
  created at all** (never force-evaluated, no phantom variance/
  tolerance failure), and real on-hand for both items is untouched.
  This directly answers a real concern raised before testing (an
  uncounted item silently defaulting to 0 and triggering a false
  out-of-tolerance/tolerance-check failure) — confirmed live that
  MAWM's own safeguard rules this out; nothing on this app's side had
  to compensate for it.
- A second item's own `initiateCount` call against an already-open
  count for the same location reuses the **same** `CountRunId`/
  `TaskId` (MAWM's own dedup — confirmed live, not something this app
  requests or controls). Once every item has been persisted under that
  shared run, a single `endCount` call succeeds and every item
  transitions together — through a previously unseen intermediate
  status, `"Count Complete"` (`StatusKey: 30`), to `"Booked"`.

**Fixed**: `task_service.complete_cycle_count_location()` (new,
alongside `check_cycle_count_location_status()` for polling) runs the
correct atomic sequence — one `initiateCount`, then
`validateItemAndGetItemDetails`/`acceptQuantity`/`persistCountDetails`
looped per item (all reusing the same `CountRunId`/`TaskId`), then
exactly one `endCount` call at the very end, only once every item has
been persisted. If any single item's own validate/accept/persist call
raises, this stops immediately *without* calling `endCount` — leaving
the run parked at `"Count Initiated"` (confirmed safe above) rather
than calling `endCount` on incomplete data for a different, more
confusing rejection. `complete_cycle_count_line()`/
`check_cycle_count_status()` (the original per-item versions) are kept
for the single-item case — a location with exactly one item has no
"other items" to wait for, so the atomic version isn't strictly
required there, but the frontend now always uses the atomic path
uniformly (see below) since a single-item location is just "a group of
1" and needs no special-casing.

**Frontend reworked to select/complete by *location*, not by line**
(`app.js`) — per explicit instruction ("we probably need to force a
user to enter all lines, with 0 being a valid number"):
- Every cycle-count row (single-item, MIXED summary, MIXED sub-rows)
  now carries `data-group-key`; clicking any of them selects the whole
  group (`selectCycleCountGroup()`), highlighting every row in it.
- `isCycleCountGroupDone()`/`isCycleCountGroupValid()` require **every**
  line in the group to be done/valid — Complete Line stays disabled
  until every item in a MIXED location has a real quantity entered (0
  valid, matching `isCycleCountQtyValid()`'s existing empty-string
  check from earlier this session).
- `completeCycleCountGroupAction()` calls the new
  `/api/complete_cycle_count_location` with every item's current
  itemId/quantity in one request; `pollCycleCountGroupStatus()` is the
  group-level counterpart to the earlier per-line poller, calling
  `/api/check_cycle_count_location_status` for all items at once.
- Complete All now iterates *groups*, not flat lines — a MIXED
  location counts as one unit in "N of M" progress and in the
  confirmation modal/failure summary.

**Confirmed live end-to-end through the real UI** (not just direct
Python calls): `A1AC0924`, both items counted at their exact current
on-hand (1739/750, no variance) via one Complete Line click on the
selected group — both resolved to `"Booked"` (`"1739 → 1739"`/
`"750 → 750"`, `"0 ($0)"` variance), the action-status banner read
"Location A1AC0924 booked.", both rows correctly marked done
(qty/item inputs disabled), and real MAWM data independently confirmed
unchanged on-hand (1739/750) with the location unlocked afterward.

**Within-tolerance multi-item test, same session, per explicit
instruction** ("test one line difference within tolerance… before out
of tolerance") — one item off by 1 on its larger on-hand quantity
(`5000001`: 1739 → counted 1738, then a follow-up UI test 1738 →
counted 1737), the other exact (`5000002`: 750 → 750). Confirmed live
via both a direct `complete_cycle_count_location()` call and, in a
follow-up pass, the real UI end to end:

- Immediately after submission both items show
  `"Count Initiated"`/`"In Booking"` (a **third** new intermediate
  status not seen before — the observed progression across all
  multi-item tests so far is `Count Initiated` → `In Booking` →
  `Count Complete` (multi-item only, once every item is persisted) →
  `Booked`; not all stages necessarily show for every case, e.g. a
  perfect match can skip straight to `Booked`).
- The mismatched item's `acceptQuantity` step returns the usual
  `INM::227` "Quantity mismatch" WARNING; polling confirmed both items
  resolve to `"Booked"` together, same as the no-variance case.
- Real on-hand correctly updated for the mismatched item only
  (1739→1738, then 1738→1737 in the follow-up test) — the exact-match
  item stayed untouched (750) both times, confirmed via direct
  `search_location_inventory()` re-query after each test, not just
  trusted from the API response.
- The UI test specifically confirmed the polling path: the row showed
  `"In Booking"` with the not-yet-applied counted qty struck through
  immediately after clicking Complete Line, then flipped to green
  `"Booked: 1738 → 1737 (variance -1)"` a few seconds later via
  `pollCycleCountGroupStatus()`'s background poll, with the
  action-status banner updating to "Location A1AC0924 booked." —
  matching the single-item polling behavior confirmed earlier in this
  session, now proven for the multi-item atomic path too.

**Out-of-tolerance multi-item test — a genuinely important finding**
(same session, tested right after within-tolerance per explicit
instruction): `5000001` counted way off (1737 → 800, variance -937),
`5000002` counted exactly right (750 → 750, variance 0). Confirmed
live via both a direct `complete_cycle_count_location()` call and the
real UI end to end:

- **Both items** ended up at `Status: "Pending Booking"` — including
  `5000002`, the exact match with zero variance. One item's tolerance
  failure blocks the **entire location's** count from booking, not
  just the offending item — this is evaluated at the location/count-
  run level, not purely per item.
- Real on-hand confirmed **unchanged for both items** (`5000001`
  stayed 1737, `5000002` stayed 750) — the correctly-counted item's
  correction does not get applied just because it happened to be
  right; it's held hostage by its sibling until a supervisor resolves
  the whole location.
- UI correctly reflected this: both rows showed red `"Pending Booking"`
  with the counted qty struck through (`"1737 → ~~800~~"` and
  `"750 → ~~750~~"`), the lock icon displayed, and — importantly —
  **neither row was marked done**; both stayed editable/retryable
  (inputs not disabled), unlike the "Booked" case which locks them.
  This is the right behavior (a supervisor might reject the whole
  count, at which point the operator would need to recount and
  resubmit), but it's worth knowing: a single bad count on one SKU in
  a multi-item location holds up every other SKU there too, with no
  partial credit for the ones that were actually right.
- The action-status banner correctly showed the improved summary from
  the same-session fix above ("Still processing (Count Initiated,
  Count Initiated) — status will keep updating.") rather than a bare
  "Complete failed."

## Tasked (non-ad-hoc) Cycle Count — task closure confirmed (2026-08-09, tenth session)

Ad hoc counting confirmed solid, so investigation moved to real WM-
scheduled Cycle Count tasks (`TransactionTypeId ='Cycle Count'` in
`task/api/task/task/search`, same generic Task Management endpoint
Putaway uses). 44 total in `SS-DEMO`, all but 2 already `Status: 8000`
(Completed). Of the 2 open ones, only one was a genuine candidate —
`CCNTINM0000000021` turned out to be leftover byproduct state from
this session's own earlier ad hoc single-item test at `A1AC0139` (its
`TransactionId: "Cycle Count Active-API"` and `AssignedTaskPoolId:
"SystemTaskPool"` match the ad hoc flow exactly, not a real
WM-generated task).

**The real candidate**: `CCNTINM000023`, `Status: 3000` (Ready For
Assignment), `Description: "Recount Forward Picking"`,
`TransactionId: "Recount"`, `AssignedTaskPoolId: "Task Interleaving"`,
pointing at `C1CS0111` (`LocationTypeId: STORAGE`,
`InventoryReservationTypeId: LOCATION` — non-LPN-tracked, confirmed).
`TaskDetail` carries no item (`ItemId: null`, `Quantity: 0`) — per
explicit instruction, this is expected: "when WM creates count tasks,
it is for the entire location," so the app is expected to look the
item(s) up from the location itself, exactly like ad hoc already does
via `search_location_inventory()` — `resolve_cycle_count_location()`
needs no changes for this. `C1CS0111` holds one real item (`123459`,
"Pima Cotton Sweater", OnHand 150) and was already locked
(`CycleCountPending: true`) purely from the task's existence — zero
prior `inventoryCountRun` records, confirming the lock flag can be set
by WM's own task-creation process independent of the ad hoc
`initiateCount` flow entirely.

**Confirmed live: the completion mechanism is shared.** Calling the
exact same ad hoc `initiate_count('C1CS0111', ...)` — no task
parameter, nothing cycle-count-task-specific — auto-detected and
tried to claim the real task by name. First attempt failed with
`FWTSK::019`, *"Task CCNTINM000023 cannot be assigned to user
sdtadmin@ss-demo"* (a live task-assignment conflict, same error class
already documented for Putaway tasks earlier in this project — not a
code bug). After the user manually reassigned the task, the identical
call succeeded and returned `CountRunId: "CNT000800"` tied to
`TaskId: "CCNTINM000023"` — confirming this is genuinely the same
underlying task, not a coincidence.

Ran the full remaining chain (validate/accept/persist/end,
exact-match count, 150 no variance) — all clean 200s. The count itself
resolved correctly: `inventoryCountResult.Status: "Booked"`, real
on-hand confirmed unchanged at 150, `locationCountInfo.CycleCountPending`
cleared to `false`.

**But the task itself never closed via `/count/end`.** `CCNTINM000023`
stayed at `Status: "7000"` (In Progress), its `TaskDetail.Status`
stayed `"1000"` (Created), and `CompletedQuantity`/`Quantity` never
got populated with the real counted values — confirmed genuinely
stuck, not async lag (polled every 3s for 15s straight, zero change).
This meant the ad hoc `initiateCount`→...→`endCount` chain only
handles the inventory-count side (booking the count into inventory);
it does not close out the Task Management record — parallel to how
Putaway needed its own task-completion mechanism beyond just moving
inventory.

**Resolved (2026-08-09): `/count/endCount/trigger` closes the task.**
The user fed the above findings into Glean, which proposed replacing
the final `/count/end` call with:

```
POST {host}/inventory-management/api/inventory-management/count/endCount/trigger
```

```json
{
  "LocationId": "C1CS0111",
  "CountRunId": "CNT000800",
  "TaskId": "CCNTINM000023",
  "CountSequence": 1,
  "CountMode": "USER_DIRECTED",
  "LpnTracking": false,
  "NumberOfLPNs": 0,
  "TransactionId": "Cycle Count Active-API",
  "CriteriaId": "Cycle Count Active-API Mode",
  "CountCriteriaId": "Cycle Count Active-API Mode",
  "TaskIntegrationDTO": {
    "TransactionId": "Cycle Count Active-API",
    "TransactionTypeId": "Cycle Count",
    "LaborActivityId": "Cycle Count",
    "LocationId": "C1CS0111",
    "TaskId": "CCNTINM000023"
  }
}
```

Tested live against `CCNTINM000023`/`CNT000800`, which had already
been fully booked earlier via the old `/count/end` call (count side
was done; only the task was stuck) — called this new endpoint
directly against that existing run instead of starting a fresh count,
since it's a different endpoint, not a replay of the same call.
Response was `200 {"success": true, "data": "OK"}`. **Independently
re-queried task search 6× over 18s afterward** (never trust the
response alone): `Task.Status` had genuinely changed `7000` → `8000`
("Completed" per our own `TASK_STATUS_LABELS`), stable across every
poll, with `ActualEndTime` now populated — not async lag, a real
change.

**Important nuance found in `TaskDetail`**: the task actually carries
**two** `TaskDetail` rows, not one. The original row (`Sequence: 1`,
created when WM first scheduled the task, `ItemId: null`) stayed at
`CompletedQuantity: 0.0` / `Quantity: 0.0` even after closure — it's a
placeholder that never gets touched by the count flow. A **second**
row (`Sequence: 2`, `CreatedTimestamp` matching `initiateCount`'s
timestamp, `ItemId: "123459"`) is the one that actually carries the
real values: `Quantity: 150.0`, `CompletedQuantity: 150.0` — an exact
match, `Status: "8000"`. So checking only the first `TaskDetail` array
element (the natural first instinct) gives a false negative; the
count-relevant row must be identified by `ItemId` being non-null (or
by matching `CreatedTimestamp` to the count run), not by array
position.

Also reconfirmed via the other read-only endpoints, all consistent:
`locationCountInfo.CycleCountPending: false`,
`inventoryCountRun.Status: 80` (Booked), `inventoryCountResult.Status:
"Booked"` with `OriginalQuantity`/`CountQuantity` both `150`,
`VarianceQuantity: 0`.

**Wired into the backend (2026-08-09).** Since the user didn't have an
open real WM-scheduled Cycle Count task to test against yet at the
time, wiring was done conservatively rather than switching the proven
ad hoc path over to unconfirmed behavior:

- `mawm_client.trigger_end_count(location_id, count_run_id, task_id,
  token, org, location=None)` — new function, the confirmed
  `/count/endCount/trigger` call.
- `task_service.complete_cycle_count_line()` and
  `complete_cycle_count_location()` both gained an `is_tasked: bool =
  False` parameter. At the final step, `is_tasked=True` calls
  `trigger_end_count()` instead of `end_count()`; everything else in
  the chain (initiate/validate/accept/persist, atomicity, polling) is
  completely unchanged either way. Default `False` means every
  existing ad hoc caller behaves exactly as before — **confirmed via
  live regression test** after this change: exact-match count at
  `A1AC0123` (item `6000108`, 240 = 240) still books correctly
  (`Count Initiated` → `Booked` after the normal short async delay via
  `check_cycle_count_status()` polling).
- `/api/complete_cycle_count_line` and
  `/api/complete_cycle_count_location` both accept an optional
  `isTasked`/`is_tasked` boolean in the POST body (default `false`,
  forwarded straight through) so the wired-up path can be exercised
  directly once real task data exists, without needing the UI.

**Confirmed end-to-end, fresh, real tasks (2026-08-09).** The user
found a genuine task-creation call —
`POST /inventory-management/api/inventory-management/count/cycleCountTask/create`,
body a bare array of location IDs (e.g. `["A1AC1201"]`) — that creates
a real WM Cycle Count task for a location after a short delay and sets
the location's `CycleCountPending` lock, with a lot of admin
configuration behind it the user set up outside this app. Used it to
create two fresh tasks, `CCNTINM000548` (`A1AC0123`) and `CCNTINM000549`
(`A1AC1201`), then ran `task_service.complete_cycle_count_line(...,
is_tasked=True)` directly (bypassing the UI, per the plan below) for a
single-item exact-match happy-path test on each — **both fully
confirmed**, count and task together, first try:

- `CCNTINM000548`/`A1AC0123`, item `6000108`, counted `240` (matches
  on-hand exactly): count run `CNT000802` reached
  `inventoryCountResult.Status: "Booked"` in ~3s;
  `task/api/task/task/search` on `CCNTINM000548` showed `Status: 8000`
  and the real `TaskDetail` row (`ItemId: "6000108"`)
  `CompletedQuantity: 240.0` = `Quantity: 240.0`, stable across 18s of
  polling. `locationCountInfo.CycleCountPending` cleared to `false`.
- `CCNTINM000549`/`A1AC1201`, item `4000087`, counted `40` (exact
  match): identical result shape — `Booked` in ~3s,
  `Task.Status: 8000`, real `TaskDetail.CompletedQuantity: 40.0` =
  `Quantity: 40.0`.

**Worth noting**: these two tasks are a different flavor from
`CCNTINM000023` tested earlier — `TransactionId: "Cycle Count"` (not
`"Recount"`) and `AssignedTaskPoolId: "SystemTaskPool"` (not `"Task
Interleaving"`), which is the exact same `AssignedTaskPoolId` value
previously used as the signature of an ad hoc *byproduct* task
(`CCNTINM0000000021`). The user flagged in advance that this
`TransactionId` "may or may not work as expected" — it worked cleanly
for both single-item happy-path tests, but this is a second, distinct
task-creation path from `CCNTINM000023`'s, so it's not yet proof every
task-creation flavor behaves identically. **Per explicit instruction,
did not chase or "fix" anything speculative here** — both tests were
clean, so there was nothing to react to.

Both tests above are single-item, exact-match only.

**Within-tolerance and out-of-tolerance also confirmed (2026-08-09),
same session**, each via a *freshly created* task (using
`cycleCountTask/create` per above, confirming after a few seconds'
wait that a new open task actually appeared before counting against
it — not just reusing an old one):

- **Within tolerance**: created `CCNTINM000550` for `A1AC1201`
  (on-hand `40`), counted `39` (off by 1). `acceptQuantity`-level
  behavior matched ad hoc exactly — count passed through an
  intermediate `"In Booking"` stage before reaching
  `inventoryCountResult.Status: "Booked"` at ~9s, `varianceQty: -1`.
  **The task closed to `Status: 8000` almost immediately (~3s) — before
  the count itself finished booking.** Real `TaskDetail`
  `CompletedQuantity: 39.0` = `Quantity: 39.0` (the *counted* value,
  not the original on-hand). Task closure and count booking are
  evidently independent/asynchronous of each other, not sequenced.
- **Out of tolerance**: created `CCNTINM000551` for `A2AC1201`
  (on-hand `20`), counted `10` (-50% variance). Immediately came back
  `Status: "Pending Booking"`, exactly like ad hoc. **Confirmed stuck
  there across 18s of polling** — `inventoryCountRun.Status: 35`,
  `BookedDateTime: null`, `locationCountInfo.CycleCountPending`
  stayed `true` throughout, real inventory unchanged at `20`.

  **But the task still closed anyway** — `Task.Status: 8000` from the
  very first poll (~3s), real `TaskDetail.CompletedQuantity: 10.0` =
  `Quantity: 10.0` (the counted, *not-yet-applied* quantity), stable
  across every subsequent poll. **This answers the open question from
  the section above, and it's worth flagging clearly rather than
  smoothing over**: `trigger_end_count()` closes the Task Management
  record regardless of whether the underlying inventory count ever
  actually books. A supervisor looking at Task Management alone would
  see this Cycle Count task as "Completed" even though the real
  inventory correction is still sitting unresolved, pending their own
  manual booking decision, exactly the scenario `Pending Booking`
  exists to gate. Task status is **not** a reliable signal for
  "the count was actually applied" — only `inventoryCountResult.Status
  == "Booked"` (or `locationCountInfo.CycleCountPending == false`) is.
  This app's own UI must keep surfacing count/booking status
  independently of task status once tasked cycle count is wired into
  it — never treat a closed task as proof the count succeeded.

Multi-item locations (more than one distinct item under one task/run)
under a real task are still untested — everything above has been
single-item locations only. No UI wiring exists yet either way.

**No UI wiring done** — there is still no way to reach `is_tasked=True`
from the actual app UI. Two separate gaps remain, both still open:
1. The search box doesn't route a real Cycle Count `TaskId` (like
   `CCNTINM000548`) to the cycle-count UI at all yet — it would fall
   into the generic Putaway-style task path instead.
2. Even if it did, nothing in the UI/frontend sets `is_tasked: true`
   when calling the two completion endpoints.
Both need more confirmed backend behavior first (see below), so
they're intentionally left until then.

**Remaining test plan**, same rigor as every test above (never trust
response `success` alone — always independently re-query):
1. Multi-item location under a real task — no-variance first, then
   within-tolerance, then out-of-tolerance, mirroring how ad hoc was
   proven, using `/api/complete_cycle_count_location` with
   `isTasked: true` and every item's real quantity. (All three
   tolerance outcomes are now confirmed at the *single-item* level —
   this is specifically about whether the atomic multi-item behavior
   ad hoc already relies on, e.g. one item's tolerance failure holding
   back the whole location, carries over unchanged under a real task.)
2. A task created via a real WM scheduling flow (not
   `cycleCountTask/create`) with a different `TransactionId`/
   `AssignedTaskPoolId` than both flavors tested so far, if one
   becomes available, to widen confidence beyond these two known
   shapes.
3. Only after multi-item tasked completion is confirmed should the
   UI/search-routing gaps above get built — single-item tasked
   completion (happy path, within-tolerance, out-of-tolerance) is now
   fully confirmed.
