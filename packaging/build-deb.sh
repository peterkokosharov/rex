#!/bin/bash
# Build a Debian/Ubuntu package for ai-rex (Python runtime + optional native helper).
# Usage: ./packaging/build-deb.sh [version]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Single version source: runtimelib/__init__.py (__version__)
SRC_VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$ROOT/runtimelib/__init__.py" | head -n 1)"
VERSION="${1:-${VERSION:-$SRC_VERSION}}"
[ -n "$VERSION" ] || { echo "cannot determine version" >&2; exit 1; }
ARCH="${ARCH:-amd64}"
BUILD="$ROOT/build-deb"
STAGE="$BUILD/stage"
DIST="$ROOT/dist"
PYDIR="$STAGE/usr/lib/python3/dist-packages"
DEB="$DIST/ai-rex_${VERSION}_${ARCH}.deb"

rm -rf "$BUILD"
mkdir -p "$STAGE/DEBIAN" "$STAGE/usr/bin" \
         "$PYDIR/runtimelib/native" "$STAGE/usr/share/doc/ai-rex" "$DIST"

echo "==> Building native helper"
g++ -std=c++17 -O2 -fPIC -shared -pthread \
    "$ROOT/native/rex_native.cpp" -o "$PYDIR/runtimelib/native/librex_native.so"

echo "==> Staging Python modules"
cp -a "$ROOT/runtime" "$PYDIR/"
cp -a "$ROOT/runtimelib" "$PYDIR/"
# pinned kikolib copy as the top-level package "kikolib" (imports succeed before runtimelib's vendor append)
cp -a "$ROOT/vendor/kikolib" "$PYDIR/"
find "$PYDIR" -type d -name __pycache__ -prune -exec rm -rf {} +

cat > "$STAGE/usr/bin/ai-rex" <<'EOF'
#!/bin/sh
# Launch ai-rex from the system Python; modules live in /usr/lib/python3/dist-packages.
PYTHONPATH=/usr/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}
export PYTHONPATH
exec python3 -m runtime "$@"
EOF
chmod 0755 "$STAGE/usr/bin/ai-rex"

install -m 0644 "$ROOT/README.md"  "$STAGE/usr/share/doc/ai-rex/README.md"
install -m 0644 "$ROOT/LICENSE"    "$STAGE/usr/share/doc/ai-rex/copyright"
install -m 0644 "$ROOT/native/README-WINDOWS.md" "$STAGE/usr/share/doc/ai-rex/native-windows.md"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: ai-rex
Version: $VERSION
Section: devel
Priority: optional
Architecture: $ARCH
Maintainer: Peter Kokosharov <peter.kokosharov@gmail.com>
Depends: python3 (>= 3.12)
Replaces: rex (<< 0.4~)
Breaks: rex (<< 0.4~)
Description: AI agent runtime with a JSON-lines bridge
 ai-rex is the Python runtime driven by Layka and other front-ends over stdin/stdout.
 This package installs the 'ai-rex' command, the runtime/runtimelib modules, the pinned kikolib 0.2.x and the
 optional native helper as /usr/lib/python3/dist-packages/runtimelib/native/librex_native.so.
EOF

echo "==> Building $DEB"
dpkg-deb --root-owner-group --build "$STAGE" "$DEB"
dpkg-deb --contents "$DEB" | sed -n '1,12p'
echo "==> Done: $DEB"
