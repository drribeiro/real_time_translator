#!/bin/bash
# Build RealtimeTranslator as a macOS .app bundle

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "==================================="
echo "  Building RealtimeTranslator.app"
echo "==================================="
echo ""

# Check venv
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -r requirements.txt -q
pip install pyinstaller -q

# Generate icon if missing
if [ ! -f "icon.icns" ]; then
    echo "Generating icon..."
    python create_icon.py
fi

# Clean previous builds
rm -rf build dist

# Build with PyInstaller
echo "Building .app bundle..."
pyinstaller \
    --name "RealtimeTranslator" \
    --windowed \
    --icon icon.icns \
    --add-data ".env.example:." \
    --add-data "icon.png:." \
    --hidden-import "PyQt6.sip" \
    --hidden-import "deepgram" \
    --hidden-import "deepl" \
    --hidden-import "openai" \
    --hidden-import "sounddevice" \
    --hidden-import "numpy" \
    --osx-bundle-identifier "com.realtimetranslator.app" \
    --noconfirm \
    ui.py

# Copy .env if exists (for local use only)
if [ -f ".env" ]; then
    cp .env "dist/RealtimeTranslator.app/Contents/MacOS/.env"
    echo "Copied .env to app bundle"
fi

# Set LSUIElement to make it a proper menu bar app
PLIST="dist/RealtimeTranslator.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PLIST" 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Set :LSUIElement true" "$PLIST"

echo ""
echo "==================================="
echo "  Build complete!"
echo "==================================="
echo ""
echo "  App: dist/RealtimeTranslator.app"
echo ""
echo "  To install:"
echo "    cp -r dist/RealtimeTranslator.app /Applications/"
echo ""
echo "  To run:"
echo "    open dist/RealtimeTranslator.app"
echo ""
echo "  NOTE: On first run, macOS may ask for"
echo "  microphone and accessibility permissions."
echo "==================================="
