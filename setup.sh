#!/usr/bin/env bash
# Setup script for rsync-miku
# Installs Applio and downloads a Hatsune Miku voice model

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "🌸 rsync-miku setup"
echo "===================="

# 1. Install Applio
if [ ! -d "applio" ] || [ ! -f "applio/core.py" ]; then
    echo ""
    echo "📦 Installing Applio..."
    git clone https://github.com/IAHispano/Applio.git applio
    cd applio
    ./run-install.sh
    python core.py prerequisites --models True --pretraineds_hifigan True
    cd "$SCRIPT_DIR"
else
    echo "✅ Applio already installed"
fi

# 2. Install coqui-tts (optional, for better quality)
echo ""
if command -v pip3 &>/dev/null; then
    if pip3 show coqui-tts &>/dev/null; then
        echo "✅ coqui-tts already installed"
    else
        echo "🔤 Installing coqui-tts (optional, for better TTS quality)..."
        pip3 install coqui-tts
    fi
else
    echo "⚠️  pip3 not found — skipping coqui-tts install"
    echo "   macOS \`say\` will be used as fallback (no install needed)"
fi

# 3. Download voice model
echo ""
if [ -d "models/miku_default_rvc" ] || [ -f "models/miku_default_rvc.pth" ]; then
    echo "✅ Voice model already downloaded"
else
    echo "🎤 Downloading Hatsune Miku voice model..."
    mkdir -p models
    cd models

    if command -v kaggle &>/dev/null; then
        kaggle download -d aple/hatsunemikurvc miku_default_rvc.zip
        unzip -q miku_default_rvc.zip
        rm miku_default_rvc.zip
    else
        echo "   kaggle CLI not found — download manually:"
        echo "   https://huggingface.co/aple/HatsuneMikuRVC/resolve/main/miku_default_rvc.zip"
        echo "   Then extract to: $SCRIPT_DIR/models/"
    fi

    cd "$SCRIPT_DIR"
fi

# 4. Make wrapper executable
chmod +x rsync-miku
chmod +x rsync_miku.py

echo ""
echo "✅ Setup complete!"
echo ""
echo "Usage:"
echo "  ./rsync-miku source/ dest/"
echo "  ./rsync-miku -avz --voice mellow source/ dest/"
echo "  ./rsync-miku --dry-run -a source/ dest/"
echo ""
echo "Voice models: default | mellow | power"
echo "  Download from: https://huggingface.co/aple/HatsuneMikuRVC"
echo ""
echo "For better TTS quality, install coqui-tts:"
echo "  pip3 install coqui-tts"
