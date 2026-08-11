#!/bin/bash
# Downloads the additional jars needed to actually EXPORT a PDF and
# render the QR code locally - setup.sh alone only gets you far enough
# to compile/fill against a JsonQLDataSource (its own explicit scope,
# see its header comment). This script adds what RenderPdf.java needs
# on top of that: barcode4j/ZXing (draws the QR), Batik (rasterizes the
# QR's SVG for PDF embedding), and iText (JasperReports 6.4.0's actual
# PDF export backend - confirmed from a real WMS-generated PDF's own
# /Producer metadata: "iText 2.1.7 by 1T3XT").
#
# Versions are pinned to exactly what jasperreports-6.4.0's own pom.xml
# declares (same discipline as setup.sh) - fetched directly from Maven
# Central's copy of that pom, not guessed.
#
# Run this after setup.sh, from within this directory
# (Jasper/local-640-harness/). Requires curl.

set -e
mkdir -p cp
cd cp

download() {
  local url="$1"
  local out="$2"
  if [ -f "$out" ]; then
    echo "already have $out"
  else
    echo "downloading $out..."
    curl -sSfL -o "$out" "$url"
  fi
}

BASE="https://repo1.maven.org/maven2"

# Batik 1.8 - the full module set batik-bridge/batik-svggen transitively
# need (per their own poms), not just the two jasperreports.pom lists
# directly.
for m in batik-anim batik-awt-util batik-bridge batik-css batik-dom \
         batik-ext batik-gvt batik-parser batik-script batik-svg-dom \
         batik-svggen batik-util batik-xml; do
  download "$BASE/org/apache/xmlgraphics/$m/1.8/$m-1.8.jar" "$m-1.8.jar"
done

download "$BASE/xalan/xalan/2.7.0/xalan-2.7.0.jar" "xalan-2.7.0.jar"
download "$BASE/xml-apis/xml-apis/1.3.04/xml-apis-1.3.04.jar" "xml-apis-1.3.04.jar"
download "$BASE/xml-apis/xml-apis-ext/1.3.04/xml-apis-ext-1.3.04.jar" "xml-apis-ext-1.3.04.jar"

# barcode4j + zxing - what actually draws the QR/barcode image.
download "$BASE/net/sf/barcode4j/barcode4j/2.1/barcode4j-2.1.jar" "barcode4j-2.1.jar"
download "$BASE/com/google/zxing/core/3.2.1/core-3.2.1.jar" "core-3.2.1.jar"

# xmlgraphics-commons - NOT declared in jasperreports' or batik's own
# pom.xml as far as could be found, but genuinely required at runtime
# (org.apache.xmlgraphics.java2d.color.NamedColorSpace, hit mid-PDF-export
# without it) - a real undeclared-transitive-dependency gap in the
# published poms from this era, discovered by running RenderPdf.java and
# reacting to the actual NoClassDefFoundError, not by finding it declared
# anywhere. Version isn't pinned by any pom for the same reason; 2.2 is
# a modern, broadly-compatible release and confirmed working here.
download "$BASE/org/apache/xmlgraphics/xmlgraphics-commons/2.2/xmlgraphics-commons-2.2.jar" "xmlgraphics-commons-2.2.jar"

# iText 2.1.7.js5 - JasperReports' own forked build, NOT on Maven
# Central (confirmed: 404s there). Lives on Jaspersoft's own public
# Artifactory instead. Needs -L: the direct URL 302-redirects to a
# signed S3 URL, and a plain `curl -o` (no -L) silently saves the
# ~0-byte redirect response instead of erroring - confirmed live, this
# produced a 0-byte "successfully downloaded" jar that only failed much
# later at class-load time. Always verify jar sizes after adding a new
# download source, not just curl's own exit code.
download "https://jaspersoft.jfrog.io/jaspersoft/third-party-ce-artifacts/com/lowagie/itext/2.1.7.js5/itext-2.1.7.js5.jar" "itext-2.1.7.js5.jar"

echo ""
echo "Done. cp/ now has everything RenderPdf.java needs for a full PDF export with QR."
