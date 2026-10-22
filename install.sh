#!/bin/bash
# Installs Kokoro Studio: creates a Python 3.12 venv, installs dependencies and
# builds a double-clickable "Kokoro Studio.app" in this folder.
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"

if command -v uv >/dev/null 2>&1; then
  uv venv --python 3.12 --managed-python .venv
  uv pip install --python .venv/bin/python -r requirements.txt
else
  PY="$(command -v python3.12 || true)"
  if [ -z "$PY" ]; then
    echo "Python 3.12 not found. Install it (brew install python@3.12) or install uv (brew install uv) and re-run." >&2
    exit 1
  fi
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi

APP="Kokoro Studio.app/Contents"
rm -rf "Kokoro Studio.app"
mkdir -p "$APP/MacOS" "$APP/Resources"

cat > "$APP/MacOS/launch" <<EOF
#!/bin/bash
exec "$DIR/.venv/bin/python" "$DIR/app.py"
EOF
chmod +x "$APP/MacOS/launch"

cat > "$APP/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Kokoro Studio</string>
<key>CFBundleDisplayName</key><string>Kokoro Studio</string>
<key>CFBundleExecutable</key><string>launch</string>
<key>CFBundleIdentifier</key><string>local.kokoro.studio</string>
<key>CFBundleIconFile</key><string>icon</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
EOF

# App icon from assets/logo.png
ICONSET="$(mktemp -d)/icon.iconset"
mkdir -p "$ICONSET"
for n in 16 32 128 256; do
  sips -z $n $n assets/logo.png --out "$ICONSET/icon_${n}x${n}.png" >/dev/null
  sips -z $((n*2)) $((n*2)) assets/logo.png --out "$ICONSET/icon_${n}x${n}@2x.png" >/dev/null
done
cp assets/logo.png "$ICONSET/icon_512x512.png"
iconutil -c icns "$ICONSET" -o "$APP/Resources/icon.icns"

echo
echo "Done. Open \"$DIR/Kokoro Studio.app\" (drag it to the Dock or Applications if you like)."
echo "First generation downloads the Kokoro model (~330 MB)."
