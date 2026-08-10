#!/bin/bash
# Downloads the real JasperReports 6.4.0 jar plus its minimal required
# dependencies (for compiling/filling a classic-format JRXML using a
# JsonQLDataSource - NOT chart/Excel/PDF-export, those need more jars)
# into ./cp/, matching MAWM's actual JasperReports version.
#
# Every version pinned here was resolved through real trial and error in a
# Claude Code session on 2026-08-09/10 - see ../README.md's "Local
# JasperReports 6.4.0 test harness" section for the full story of why
# each one is needed (e.g. the ecj version had to match jasperreports
# 6.4.0's pom.xml exactly, not just "a recent JDT compiler", or
# JasperCompileManager throws NoSuchMethodError).
#
# Usage: run this script from within this directory (Jasper/local-640-harness/).
# Requires curl. Downloads ~11MB total into ./cp/ (gitignored, not committed).

set -e
mkdir -p cp
cd cp

M2="https://repo1.maven.org/maven2"

download() {
  local url="$1"
  local out="$2"
  if [ -f "$out" ]; then
    echo "already have $out"
  else
    echo "downloading $out ..."
    curl -sL -o "$out" "$url"
  fi
}

download "$M2/net/sf/jasperreports/jasperreports/6.4.0/jasperreports-6.4.0.jar" "jasperreports-6.4.0.jar"
download "$M2/antlr/antlr/2.7.5/antlr-2.7.5.jar" "antlr-2.7.5.jar"
download "$M2/org/eclipse/jdt/core/compiler/ecj/4.3.1/ecj-4.3.1.jar" "ecj-4.3.1.jar"
download "$M2/commons-digester/commons-digester/2.1/commons-digester-2.1.jar" "commons-digester-2.1.jar"
download "$M2/commons-beanutils/commons-beanutils/1.9.4/commons-beanutils-1.9.4.jar" "commons-beanutils-1.9.4.jar"
download "$M2/commons-collections/commons-collections/3.2.2/commons-collections-3.2.2.jar" "commons-collections-3.2.2.jar"
download "$M2/commons-logging/commons-logging/1.2/commons-logging-1.2.jar" "commons-logging-1.2.jar"
download "$M2/com/fasterxml/jackson/core/jackson-core/2.18.2/jackson-core-2.18.2.jar" "jackson-core-2.18.2.jar"
download "$M2/com/fasterxml/jackson/core/jackson-databind/2.18.2/jackson-databind-2.18.2.jar" "jackson-databind-2.18.2.jar"
download "$M2/com/fasterxml/jackson/core/jackson-annotations/2.18.2/jackson-annotations-2.18.2.jar" "jackson-annotations-2.18.2.jar"

cd ..
echo ""
echo "Done. Classpath jars are in ./cp/"
echo "Compile the test harness with, e.g.:"
echo '  javac -cp "cp/*" -d . ProdValidate.java'
echo "Run with:"
echo '  java -cp "cp/*;." ProdValidate <path-to-jrxml> <path-to-json>'
