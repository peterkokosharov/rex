#!/bin/bash
# Build the Windows x64 zip for ai-rex: Python sources + optional native DLL.
# Needs x86_64-w64-mingw32-g++ and zip. Usage: ./packaging/build-win-zip.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
STAGE="$DIST/win-ai-rex"
CXX="${MINGW_CXX:-x86_64-w64-mingw32-g++}"

rm -rf "$STAGE" "$DIST/ai-rex-win.zip"
mkdir -p "$STAGE/rex/runtimelib/native"

echo "==> Cross-building rex_native.dll"
"$CXX" -std=c++17 -O2 -shared -static-libgcc -static-libstdc++ -pthread \
    "$ROOT/native/rex_native.cpp" -o "$STAGE/rex/runtimelib/native/rex_native.dll"

# Link statically where possible; copy any runtime DLL the toolchain still needs.
for dll in libwinpthread-1.dll libstdc++-6.dll libgcc_s_seh-1.dll; do
    candidate="$("$CXX" -print-file-name="$dll" 2>/dev/null || true)"
    if [ -n "$candidate" ] && [ -f "$candidate" ]; then
        cp "$candidate" "$STAGE/rex/"
        echo "   included $dll"
    fi
done

echo "==> Staging Python sources"
cp -a "$ROOT/runtime" "$STAGE/rex/"
cp -a "$ROOT/runtimelib" "$STAGE/rex/"
mkdir -p "$STAGE/rex/vendor"
cp -a "$ROOT/vendor/kikolib" "$STAGE/rex/vendor/"
find "$STAGE/rex" -type d -name __pycache__ -prune -exec rm -rf {} +

cat > "$STAGE/rex/ai-rex.cmd" <<'EOF'
@echo off
setlocal
set PYTHONPATH=%~dp0;%PYTHONPATH%
python -P -m runtime %*
EOF

cat > "$STAGE/rex/README.txt" <<'EOF'
ai-rex for Windows (x64)

1. Install Python 3.12 or newer and make sure `python` is in PATH.
   Install Git for Windows too (https://git-scm.com/download/win): ai-rex REQUIRES Git Bash on
   Windows and runs every bash command with it (never cmd.exe).
2. Unzip anywhere, e.g. C:\Tools. The program folder inside the zip is still called `rex`
   (front-ends such as Layka and Sisi look for that name); the command is `ai-rex.cmd`.
3. Run:  ai-rex.cmd --help
   or:   python -P -m runtime --jsonl --local-tools

Layka users: unzip ai-rex-win.zip next to layka.exe. Layka will find the `rex`
subfolder automatically. rex_native.dll is optional; if it cannot be loaded,
the pure Python fallback is used.

Endpoint and key are normally passed by the front-end as
AI_REX_LITELLM__BASE_URL and AI_REX_LITELLM__API_KEY.
EOF

(cd "$STAGE" && zip -qr "$DIST/ai-rex-win.zip" rex)
echo "==> Done: $DIST/ai-rex-win.zip"
