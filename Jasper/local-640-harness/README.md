# Local JasperReports 6.4.0 test harness

A real, working copy of the exact JasperReports version MAWM runs (6.4.0,
confirmed via MAWM's own supported-Jasper documentation), runnable
entirely from the command line — no Jaspersoft Studio, no WMS upload,
no waiting. This exists because guessing at JsonQL query syntax one slow
WMS deployment round-trip at a time was extremely costly; this harness
answers the same questions in seconds, locally.

**Important**: this is the actual open-source `net.sf.jasperreports:jasperreports:6.4.0`
artifact from Maven Central — not the Jaspersoft Studio-bundled jar used
elsewhere in this project. Studio here bundles JasperReports 7.0.6, whose
classic-format loader has a deliberate license gate that only works
inside Studio itself (see the main `README.md`'s "How to verify a change"
section). This 6.4.0 jar has no such gate — it's the real deal, runnable
anywhere.

## Setup

Requires a JDK on PATH (`java`/`javac`) — a modern one is fine (17 or 21
LTS both work; only the *bundled ecj compiler jar* in `cp/` is
version-pinned to what 6.4.0 itself needs, not the JVM you run it with).
Not installed by default — if `java -version` fails, install one first
(e.g. Eclipse Temurin: `winget install EclipseAdoptium.Temurin.17.JDK`
on Windows, or any standard JDK install for your OS).

```
bash setup.sh
```

Downloads ~11MB of jars into `./cp/` (gitignored, not committed —
re-download any time). Every version is pinned to what JasperReports
6.4.0's own `pom.xml` actually declares — **do not casually bump these**,
especially `ecj` (the Eclipse Java compiler): a newer JDT/ecj jar is
binary-incompatible and throws `NoSuchMethodError` on
`Compiler.<init>` at report-compile time. If `setup.sh` ever needs a new
dependency, check `jasperreports-6.4.0.pom` (downloaded alongside the
jar, or fetch directly from Maven Central) for the exact declared version
first.

## Rendering a real PDF for local QC (no Studio, no WMS upload)

`render.ps1` compiles `cyclecountsheet.jrxml`, fills it against a JSON
payload you edit, and exports an actual PDF — the same thing the WMS
does at print time, just runnable in seconds on your own machine. This
is the tool that would have caught the `<sortField>` regression (see the
main `README.md`'s "sortField regression" section) before it ever
reached a real WMS upload — `ProdValidate.java`'s text-presence checks
were never re-run against that change either, but a PDF you can actually
look at is a much lower-effort check to remember to run than writing new
assertions every time.

```powershell
cd local-640-harness
.\render.ps1
```

**Parameters** (all optional — every one has a default, override only
the ones you need):

| Parameter | Default | What it is |
|---|---|---|
| `-Jrxml` | `..\cyclecountsheet.jrxml` | The report to render. Point this at any other `.jrxml` — useful for testing a *different* report entirely, not just this one (see below). |
| `-Json` | `my_test_payload.json` | The data payload to render it against. |
| `-Out` | `preview.pdf` | Where to write the resulting PDF. |

```powershell
.\render.ps1 -Json my_other_payload.json -Out check2.pdf
```

**Testing a different report** (not `cyclecountsheet.jrxml`) — pass
`-Jrxml` explicitly, either a full path or relative to wherever you're
running the script from:

```powershell
.\render.ps1 -Jrxml "..\some_other_report.jrxml" -Json other_payload.json -Out other_preview.pdf
```

This whole `render.ps1`/`RenderPdf.java` setup assumes a **classic-format**
JRXML (`<queryString language="jsonql">`, `JsonQLDataSource`, etc. — see
the main `README.md`'s syntax notes) — the same format `cyclecountsheet.jrxml`
uses. A new report headed for the WMS the same way will need that same
classic-format treatment to render correctly here.

**Edit `my_test_payload.json` directly** to try different data — add
more locations, more items per location, longer descriptions, whatever
you want to see rendered. It's already seeded with 3 locations (one
single-item, one two-item) using real item/location IDs from earlier
testing in this project.

**Get the JSON shape right — this matters as much as the JRXML itself.**
The shape is **not** what you might guess from a hypothetical "generate
document" API call. Two real things to know:

- **The root key is `Data`, not `Locations` or `Payload.Locations`.**
  There's no outer `Payload`/`ResponseType`/`DocumentFormat` wrapper —
  `Data` sits at the top level of the file, a sibling of
  `DocumentTemplateTypeId`/`DocumentTemplateId` (both harmless to
  include or omit — the report's own root `<queryString>` is empty and
  never reads them, but they're in the real captured payload
  (`mawm_sample.json` in this same folder) so `my_test_payload.json`
  keeps them for realism).
- **Each item's on-hand quantity field is `OnHandSum`, not `OnHandQty`.**
  There's no `OnHandDisplay` field either — the report doesn't use one.
  Get either of these wrong and you'll see the exact silent-blank-output
  symptom the main `README.md` spends most of its length chasing down —
  the JSON parses fine, the report "succeeds," and the field just never
  populates. `RenderPdf.java` won't warn you about this; only the
  rendered PDF (empty where you expected data) will.

See the main `README.md`'s "The real MAWM payload shape" section for
the full field list this is based on.

### One-time extra setup: PDF-export jars

`setup.sh` alone only gets you far enough to compile/fill (what
`ProdValidate.java` needs) — it deliberately doesn't include anything
for actually exporting a PDF or drawing the QR (see its own header
comment). Run this once, from this same folder, before the first
`render.ps1`:

```
bash setup_pdf_export.sh
```

Downloads barcode4j/ZXing (draws the QR), the full Batik 1.8 module set
(rasterizes the QR's SVG for PDF embedding), xalan/xml-apis(-ext)
(Batik's own transitive deps), `xmlgraphics-commons` (an
undeclared-but-genuinely-required transitive dependency — not listed in
any pom found for this era, discovered by reacting to a real
`NoClassDefFoundError` for `NamedColorSpace` at PDF-export time, not
found by inspection), and iText 2.1.7.js5 (JasperReports 6.4.0's real
PDF export backend — confirmed via a real WMS-generated PDF's own
`/Producer` metadata reading `"iText 2.1.7 by 1T3XT"`).

**iText specifically isn't on Maven Central** (that exact
Jaspersoft-forked build 404s there) — it lives on Jaspersoft's own
public Artifactory
(`https://jaspersoft.jfrog.io/jaspersoft/third-party-ce-artifacts/...`).
**That URL 302-redirects to a signed S3 link** — `curl -o` without `-L`
silently saves the redirect response's own (near-empty) body instead of
erroring, producing a 0-byte "successfully downloaded" jar that only
fails much later, at class-load time, with a confusing
`NoClassDefFoundError` that doesn't obviously point back at a bad
download. `setup_pdf_export.sh` uses `-fL`; if any future new download
source is added here, verify the resulting jar's actual size, not just
curl's exit code — this one produced a "successful," genuinely 0-byte
file the first time.

**Confirmed working end to end, 2026-08-10** (the session that added
this tooling, right after the `<sortField>` regression documented in
the main `README.md`): `render.ps1` against `my_test_payload.json`
produced a real `preview.pdf` — the enlarged/centered title, the QR in
the upper-right corner, and the fixed single-row blank-line all
rendered correctly for all 3 test locations. This is the *first* real
visual confirmation of that whole round of layout fixes — the actual
WMS upload of the same JRXML version had the (since-reverted)
`<sortField>` bug masking everything else, so this local render is
what actually validated title/subtitle/line before the next WMS
upload, not a redundant re-check of something already proven live.
**Still owed**: an actual WMS upload of this exact (sortField-reverted)
version, to confirm this local render's output really does match what
the WMS produces — strongly expected to, given everything else about
this harness (the exact 6.4.0 jar, the exact two-argument fill path,
the exact classic JRXML) has matched production so far, but not yet
independently re-confirmed for *this* specific version.

## The critical gotcha: which `fillReport()` overload you call matters

This tripped up testing for an entire round before being caught. Two
different ways to fill a report produce **genuinely different, non-equivalent
behavior**:

- **Three-argument** `JasperFillManager.fillReport(report, params, dataSource)`
  — you construct the `JRDataSource` yourself in Java and pass it directly.
  **This completely bypasses the report's own `<queryString>` element** —
  it's never executed. Testing this way only proves whatever
  `selectExpression` you hardcoded in your own Java code works, not
  anything about the actual `.jrxml` file's query.
- **Two-argument** `JasperFillManager.fillReport(report, params)` — you
  provide raw JSON via the built-in parameters `JsonQueryExecuterFactory
  .JSON_INPUT_STREAM` (an `InputStream`) or `.JSON_SOURCE` (a File/String
  path, `net.sf.jasperreports.json.source`), and JasperReports' own
  `jsonql` query engine executes the report's **actual** `<queryString>`
  against it, then populates the built-in `REPORT_DATA_SOURCE` parameter
  with the result.

**This report's nested table casts `$P{REPORT_DATA_SOURCE}` straight to
`JsonQLDataSource`** — that only works at all if `REPORT_DATA_SOURCE` is
already a real data source object by the time that expression runs, which
is exactly what the two-argument path does automatically. This is strong
evidence MAWM's real runtime invocation uses this same two-argument-style
pattern (raw JSON in, JasperReports executes the query itself) rather
than pre-constructing a data source object — so **always test with the
two-argument path** (`ProdValidate.java`, `ScenarioTest.java`) to get
results that actually reflect what MAWM does.
(`JasperTest640.java` uses the three-argument path and is kept only as a
reference for what that mistake looks like — don't use it to validate a
query string change.)

## Files

- **`setup.sh`** — downloads the base compile/fill dependencies into
  `./cp/`.
- **`setup_pdf_export.sh`** — downloads the *additional* jars needed to
  actually export a PDF and render the QR (barcode4j, ZXing, Batik,
  xalan, xml-apis(-ext), xmlgraphics-commons, iText) — run once, after
  `setup.sh`, before the first `render.ps1`. See "One-time extra setup:
  PDF-export jars" below for exactly why each one is needed and a real
  gotcha around downloading iText specifically.
- **`RenderPdf.java`** / **`render.ps1`** / **`my_test_payload.json`** —
  the QC tool: renders a real PDF from `cyclecountsheet.jrxml` (or any
  other `.jrxml` you point `-Jrxml` at) against a JSON payload you edit
  yourself, without ever opening Jaspersoft Studio (which would silently
  rewrite the classic-format file — see the main README's "Files"
  section). This is what to use to visually check a layout/data change
  *before* burning a WMS upload round-trip on it — see "Rendering a real
  PDF for local QC" below for the full how-to and what went wrong the
  one time this app was uploaded without this check first (a
  `<sortField>` regression that a local PDF render would have caught
  immediately). `render.ps1` auto-detects a JDK even if it's not yet on
  this shell's own `PATH` (falls back to searching common install
  locations directly) — handles the exact situation of installing a JDK
  mid-session and having the current shell not see it yet.
- **`ProdValidate.java`** — the original validation tool. Compiles a
  `.jrxml`, fills it via the two-argument path against a JSON file, and
  prints a pass/fail summary for each expected piece of data (text
  presence only, no PDF file produced) — useful for a quick scripted
  check, but `RenderPdf.java` is what to use when you actually need to
  *look* at the output.
  Usage: `java -cp "cp/*;." ProdValidate <path-to-jrxml> <path-to-json>`
- **`ScenarioTest.java`** — runs multiple named query-string variants
  against different JSON root shapes in one pass, for comparing
  hypotheses side by side (e.g. "does the root need to be `Data.*` or
  bare `*`, and does that depend on whether the JSON is enveloped or a
  bare array"). Edit the `main()` method to add/remove scenarios.
- **`JsonQLProbe.java`** — the lowest-level tool. Directly instantiates
  `JsonQLDataSource` with a given `selectExpression` and counts/prints
  records via `next()`/`getFieldValue()`, bypassing report
  compilation entirely. Use this to isolate pure JsonQL query-syntax
  questions (e.g. "does `Foo` vs `Foo.*` actually change record count")
  without the overhead of compiling a report each time. Builds field
  values via a `java.lang.reflect.Proxy` implementing `JRField` — if
  you hit `NoSuchMethodError`/unexpected nulls extending this, re-check
  the real `JRField`/`JRPropertiesHolder`/`JRCloneable` interfaces via
  `javap` first (all their methods must be handled in the
  `InvocationHandler`, including `equals`/`hashCode`/`toString`, or a
  framework internal that calls one of those on the proxy will misbehave).
- **`JasperTest640.java`** — reference-only, see the gotcha above. Don't
  use for query validation.
- **`mawm_sample.json`** — the real captured MAWM payload (full envelope:
  `{"Data": [...]}`), as provided by Glean during troubleshooting.
- **`mawm_sample_bare_array.json`** — the same data, manually unwrapped to
  just the bare `Data` array (`[...]`, no envelope) — for testing the
  "does MAWM pass the envelope or just the array" hypothesis.

## What this harness definitively resolved

See the main `README.md`'s "JsonQL vs plain JSON" section for the full
story, but briefly: this harness proved that `Foo` vs `Foo.*` makes
**zero difference** for a simple top-level array selection in real
JasperReports 6.4.0 (both iterate correctly) — ruling out an entire
category of hypothesis that consumed several deployment rounds before
this harness existed. It also reproduced the exact "compiles fine, PDF
has zero pages" symptom locally (querying `Data.*` against a bare,
non-enveloped array silently returns no records instead of erroring) —
proving that symptom is a real, reproducible structural mismatch, not
random/flaky behavior.

## What's still unresolved as of this writing

Whether MAWM's real runtime call passes the full envelope or the bare
array remains genuinely unknown — this harness can prove what happens
for either shape once you know which one MAWM sends, but it can't
observe MAWM's actual runtime payload itself. See the main `README.md`'s
"Current status" section for where the investigation stood when last
touched.
