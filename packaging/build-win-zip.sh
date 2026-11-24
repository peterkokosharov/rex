#!/bin/bash
# Build the Windows x64 zip for ai-rex with pkgkit: dist/ai-rex-<ver>-win-x64.zip
# Optional: x86_64-w64-mingw32-g++ (MINGW_CXX) cross-builds rex_native.dll into the payload; without it the pure
# Python fallback is used. Usage: ./packaging/build-win-zip.sh   (PYTHON=... to choose the interpreter, 3.11+)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
PYTHON="${PYTHON:-python}"
CXX="${MINGW_CXX:-x86_64-w64-mingw32-g++}"
EXTRA=()

if command -v "$CXX" >/dev/null 2>&1; then
    mkdir -p "$DIST/native"
    echo "==> Cross-building rex_native.dll"
    "$CXX" -std=c++17 -O2 -shared -static-libgcc -static-libstdc++ -pthread \
        "$ROOT/native/rex_native.cpp" -o "$DIST/native/rex_native.dll"
    EXTRA+=(--extra "$DIST/native/rex_native.dll=runtimelib/native/rex_native.dll")
    for dll in libwinpthread-1.dll libstdc++-6.dll libgcc_s_seh-1.dll; do
        candidate="$("$CXX" -print-file-name="$dll" 2>/dev/null || true)"
        if [ -n "$candidate" ] && [ -f "$candidate" ]; then
            EXTRA+=(--extra "$candidate=$dll")
            echo "   included $dll"
        fi
    done
else
    echo "==> $CXX not found: building without rex_native.dll (pure Python fallback)"
fi

"$PYTHON" -I "$ROOT/packaging/vendor/pkgkit/build.py" --root "$ROOT" --app "$ROOT/packaging/app.toml" \
    --out "$DIST" "${EXTRA[@]}"
