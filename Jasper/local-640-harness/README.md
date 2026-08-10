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

- **`setup.sh`** — downloads dependencies into `./cp/`.
- **`ProdValidate.java`** — the main validation tool. Compiles a `.jrxml`,
  fills it via the two-argument path against a JSON file, and prints a
  pass/fail summary for each expected piece of data. This is what to use
  to validate any future query/field-mapping change before deploying.
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
