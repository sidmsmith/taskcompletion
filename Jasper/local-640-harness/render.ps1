<#
.SYNOPSIS
  Renders cyclecountsheet.jrxml (or any other .jrxml you point it at)
  against a JSON payload into a real PDF you can open and QC before
  uploading to the WMS - no Jaspersoft Studio involved, so it can't
  accidentally rewrite the classic-format file.

.EXAMPLE
  .\render.ps1
  (uses the defaults below: ..\cyclecountsheet.jrxml, my_test_payload.json, preview.pdf)

.EXAMPLE
  .\render.ps1 -Json my_other_payload.json -Out out2.pdf

.EXAMPLE
  .\render.ps1 -Jrxml ..\some_other_report.jrxml -Json other.json -Out other.pdf
#>
param(
    [string]$Jrxml = "..\cyclecountsheet.jrxml",
    [string]$Json = "my_test_payload.json",
    [string]$Out = "preview.pdf"
)

$ErrorActionPreference = "Stop"
$cp = "cp/*;."

function Resolve-JavaExe {
    param([string]$ExeName)
    $onPath = Get-Command $ExeName -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    # Fall back to a direct search under common JDK install locations -
    # covers the case where a JDK was just installed but this shell's
    # own PATH hasn't picked it up yet (a new shell would; this one won't).
    $candidates = @(
        "C:\Program Files\Eclipse Adoptium\*\bin\$ExeName.exe",
        "C:\Program Files\Java\*\bin\$ExeName.exe",
        "C:\Program Files\Microsoft\jdk-*\bin\$ExeName.exe",
        "C:\Program Files\Amazon Corretto\*\bin\$ExeName.exe"
    )
    foreach ($pattern in $candidates) {
        $found = Get-ChildItem $pattern -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    return $null
}

$javaExe = Resolve-JavaExe "java"
$javacExe = Resolve-JavaExe "javac"

if (-not $javaExe -or -not $javacExe) {
    Write-Error "No JDK found (checked PATH and common install locations). Install one, e.g.:`n  winget install EclipseAdoptium.Temurin.17.JDK`nthen open a new terminal (or just re-run this script - it will look in Program Files even if PATH isn't updated yet)."
    exit 1
}

if (-not (Test-Path $Jrxml)) {
    Write-Error "JRXML not found: $Jrxml"
    exit 1
}
if (-not (Test-Path $Json)) {
    Write-Error "JSON payload not found: $Json (edit my_test_payload.json, or pass -Json <path>)"
    exit 1
}
if (-not (Test-Path "cp")) {
    Write-Error "cp/ not found - run 'bash setup.sh' then 'bash setup_pdf_export.sh' first to download the JasperReports 6.4.0 jars."
    exit 1
}
if (-not (Test-Path "cp\itext-2.1.7.js5.jar")) {
    Write-Error "cp/ is missing the PDF-export jars (itext, batik, barcode4j, etc.) - run 'bash setup_pdf_export.sh' first."
    exit 1
}

if (-not (Test-Path "RenderPdf.class") -or (Get-Item "RenderPdf.java").LastWriteTime -gt (Get-Item "RenderPdf.class").LastWriteTime) {
    Write-Host "Compiling RenderPdf.java..."
    & $javacExe -cp $cp RenderPdf.java
    if ($LASTEXITCODE -ne 0) { exit 1 }
}

Write-Host "Rendering $Jrxml against $Json -> $Out"
& $javaExe -cp $cp RenderPdf $Jrxml $Json $Out
if ($LASTEXITCODE -eq 0) {
    Write-Host "Done. Open $Out to QC it."
}
