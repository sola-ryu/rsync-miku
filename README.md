# rsync-miku 🌸

rsync wrapper that narrates file transfers in a Hatsune Miku voice.

## How it works

1. Runs `rsync` with Japanese locale (`ja_JP.UTF-8`) so output appears in Japanese
2. Streams each line of rsync's progress output
3. Converts text to speech via macOS `say` (or coqui-tts if available)
4. Voice-converts the TTS audio through a Hatsune Miku RVC model using Applio
5. Plays the result back in real-time

## Setup

### 1. Install Applio

```bash
cd ~/.openclaw/workspace/rsync-miku
git clone https://github.com/IAHispano/Applio.git applio
cd applio
./run-install.sh
python core.py prerequisites --models True --pretraineds_hifigan True
```

### 2. Download a Hatsune Miku RVC model

Three variants available on [HuggingFace](https://huggingface.co/aple/HatsuneMikuRVC):

| Model | Size | Description |
|-------|------|-------------|
| `miku_default_rvc` | 382 MB | Original voicebank sound |
| `miku_mellow_rvc` | 286 MB | Dark, sweet, soft |
| `miku_power_rvc` | 897 MB | Solid, vivid |

```bash
# Download and extract (example: default)
cd models
kaggle download -d aple/hatsunemikurvc miku_default_rvc.zip
unzip miku_default_rvc.zip
```

Or manually download from HuggingFace and place in `models/`.

### 3. (Optional) Install coqui-tts for better TTS quality

```bash
pip install coqui-tts
```

## Usage

```bash
# Basic usage
rsync-miku source/ dest/

# With rsync flags
rsync-miku -avz --progress source/ dest/

# Specify a Miku voice variant
rsync-miku --voice mellow source/ dest/

# Dry run (no audio, just show what would be spoken)
rsync-miku --dry-run -av source/ dest/

# Skip short lines (under 3 chars) to avoid noise from status updates
rsync-miku --min-length 5 source/ dest/
```

## Configuration

Edit `config.json` or pass overrides:

```bash
rsync-miku --voice power --embedder contentvec --tts-engine say source/ dest/
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--voice` | `default` | Miku voice variant (`default`, `mellow`, `power`) |
| `--embedder` | `japanese-hubert-base` | RVC embedder model |
| `--tts-engine` | `auto` | TTS engine (`say`, `coqui`, `auto`) |
| `--dry-run` | `false` | Show lines without speaking |
| `--min-length` | `3` | Skip lines shorter than this (reduces noise) |
| `--pitch` | `-12` | Pitch shift for voice conversion (-24 to +24) |
| `--index-rate` | `0.7` | Search feature ratio for RVC |
| `--rms-mix` | `0.8` | Volume envelope mix rate |

## Architecture

```
rsync (ja_JP locale)
  ↓ stream lines
TTS engine (say / coqui-tts)
  ↓ .wav output
Applio voice conversion
  ↓ Miku RVC model
afplay audio output
```

## Notes

- rsync's Japanese locale affects display text only (e.g., "file" → "ファイル")
- File paths and technical content remain unchanged
- Audio plays in real-time as rsync progresses
- For large transfers, use `--min-length` to skip brief status lines
- The Miku voice adds a fun overlay to otherwise boring sync output
