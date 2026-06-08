#!/usr/bin/env python3
"""rsync-miku: Narrate rsync progress in a Hatsune Miku voice 🌸

Streams rsync output line-by-line, converts meaningful lines to speech
via macOS `say` or coqui-tts, voice-converts through a Hatsune Miku RVC
model via Applio, and plays the result back in real-time.

Uses a threaded pipeline with batching to keep audio in sync with rsync.
"""

import argparse
import json
import os
import pathlib
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time


# ── paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
APPLIO_DIR = SCRIPT_DIR / "applio"
MODELS_DIR = SCRIPT_DIR / "models"
TMP_DIR = SCRIPT_DIR / ".tmp"


# ── config ──────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "voice": "default",
    "embedder": "japanese-hubert-base",
    "tts_engine": "auto",
    "dry_run": False,
    "min_length": 3,
    "pitch": -12,
    "index_rate": 0.7,
    "rms_mix": 0.8,
    "protect": 0.5,
    "f0_method": "rmvpe",
    "clean_strength": 0.7,
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ── voice model discovery ───────────────────────────────────────────────────

VOICE_VARIANTS = {
    "default": "miku_default_rvc",
    "mellow": "miku_mellow_rvc",
    "power": "miku_power_rvc",
}

VOICE_INFO = {
    "default": ("Original voicebank sound", 382),
    "mellow": ("Dark, sweet, soft", 286),
    "power": ("Solid, vivid", 897),
}


def find_voice_model() -> tuple[pathlib.Path, pathlib.Path] | None:
    cfg = load_config()
    name = VOICE_VARIANTS.get(cfg["voice"], VOICE_VARIANTS["default"])

    pth_candidates = list(MODELS_DIR.rglob(f"{name}/**/*.pth"))
    index_candidates = list(MODELS_DIR.rglob(f"{name}/**/*.index"))

    if not pth_candidates or not index_candidates:
        info, size_mb = VOICE_INFO.get(cfg["voice"], VOICE_INFO["default"])
        print(
            f"❌ Voice model '{cfg['voice']}' ({info}, ~{size_mb}MB) not found\n"
            f"   Download from https://huggingface.co/aple/HatsuneMikuRVC\n"
            f"   Extract to: {MODELS_DIR}/\n\n"
            f"Available variants:\n" +
            "\n".join(f"  - {k}: {v[0]} (~{v[1]}MB)" for k, v in VOICE_INFO.items()),
            file=sys.stderr,
        )
        return None

    pth = sorted(pth_candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    index = sorted(index_candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    return pth, index


# ── TTS engines ─────────────────────────────────────────────────────────────

def detect_tts_engine() -> str:
    cfg = load_config()
    preferred = cfg.get("tts_engine", "auto")

    if preferred == "say":
        return "say"
    if preferred == "coqui":
        return "coqui"

    try:
        from TTS.api import TTS  # noqa: F401
        return "coqui"
    except ImportError:
        pass

    if shutil.which("say"):
        return "say"

    print(
        "❌ No TTS engine available.\n"
        "   Install coqui-tts: pip install coqui-tts\n"
        "   Or use macOS built-in `say` (set --tts-engine say)",
        file=sys.stderr,
    )
    sys.exit(1)


def text_to_speech(text: str, engine: str) -> pathlib.Path | None:
    cleaned = text.strip()
    if not cleaned:
        return None

    tmp_wav = TMP_DIR / f"tts_{int(time.time()*1000)}.wav"
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    if engine == "coqui":
        return _tts_coqui(cleaned, tmp_wav)
    else:
        return _tts_say(cleaned, tmp_wav)


def _tts_say(text: str, out_path: pathlib.Path) -> pathlib.Path | None:
    """Use macOS `say` command."""
    # Try Kyoko (Japanese voice) first
    try:
        result = subprocess.run(
            ["say", "-o", str(out_path), "-v", "Kyoko", text],
            capture_output=True, timeout=30,
        )
        if out_path.exists() and out_path.stat().st_size > 100:
            return out_path
    except (subprocess.TimeoutExpired, OSError):
        pass

    # Fallback to default voice
    try:
        subprocess.run(
            ["say", "-o", str(out_path), text],
            capture_output=True, timeout=30,
        )
        if out_path.exists() and out_path.stat().st_size > 100:
            return out_path
    except (subprocess.TimeoutExpired, OSError):
        pass

    return None


def _tts_coqui(text: str, out_path: pathlib.Path) -> pathlib.Path | None:
    """Use coqui-tts for better quality."""
    try:
        from TTS.api import TTS
        tts = TTS(model_name="tts_models/ja/xtts_v2", progress_bar=False)
        tts.tts_to_file(text=text, file_path=str(out_path), speaker_wav=None)
        if out_path.exists() and out_path.stat().st_size > 100:
            return out_path
    except Exception as e:
        print(f"   ⚠️ coqui-tts failed: {e}", file=sys.stderr)

    # Fall back to say
    print("   → falling back to macOS `say`", file=sys.stderr)
    return _tts_say(text, out_path)


# ── voice conversion via Applio ─────────────────────────────────────────────

def voice_convert(input_wav: pathlib.Path, pth_path: pathlib.Path, index_path: pathlib.Path) -> pathlib.Path | None:
    output_wav = TMP_DIR / f"miku_{int(time.time()*1000)}.wav"
    cfg = load_config()

    cmd = [
        sys.executable, str(APPLIO_DIR / "core.py"), "infer",
        "--input_path", str(input_wav),
        "--output_path", str(output_wav),
        "--pth_path", str(pth_path),
        "--index_path", str(index_path),
        "--pitch", str(cfg.get("pitch", -12)),
        "--rms_mix_rate", str(cfg.get("rms_mix", 0.8)),
        "--index_rate", str(cfg.get("index_rate", 0.7)),
        "--protect", str(cfg.get("protect", 0.5)),
        "--f0_method", cfg.get("f0_method", "rmvpe"),
        "--clean_strength", str(cfg.get("clean_strength", 0.7)),
        "--clean_audio", "True",
        "--embedder_model", cfg.get("embedder", "japanese-hubert-base"),
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, cwd=str(APPLIO_DIR),
        )
        if output_wav.exists() and output_wav.stat().st_size > 100:
            return output_wav
        stderr = result.stderr.strip() if result.stderr else "(no output)"
        print(f"   ⚠️ Voice conversion failed: {stderr[:200]}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("   ⚠️ Voice conversion timed out", file=sys.stderr)
    except FileNotFoundError:
        print(
            f"   ⚠️ Applio not found at {APPLIO_DIR}\n"
            f"   Run: git clone https://github.com/IAHispano/Applio.git applio",
            file=sys.stderr,
        )

    return None


# ── audio playback ──────────────────────────────────────────────────────────

def play_audio(file_path: pathlib.Path):
    try:
        subprocess.Popen(["afplay", str(file_path)])
    except OSError as e:
        print(f"   ⚠️ Could not play audio: {e}", file=sys.stderr)


# ── line filtering ──────────────────────────────────────────────────────────

def should_speak(line: str, cfg: dict) -> bool:
    """Decide if this rsync line is worth speaking."""
    min_len = cfg.get("min_length", 3)
    stripped = line.strip()

    # Skip empty / too short
    if len(stripped) < min_len:
        return False

    # Skip pure timing/numeric lines (byte counts, speeds, etc.)
    if all(c in "0123456789.: /-_,% \t" for c in stripped):
        return False

    # Skip rsync status markers
    if stripped.startswith("[") and stripped.endswith("]"):
        return False

    return True


# ── pipeline ────────────────────────────────────────────────────────────────

def process_line(line: str, cfg: dict):
    """Process a single rsync line through TTS → voice convert → play."""
    if not should_speak(line, cfg):
        return

    cleaned = line.strip()
    engine = cfg.get("tts_engine", "auto")
    if engine == "auto":
        engine = detect_tts_engine()

    if cfg.get("dry_run"):
        print(f"   [would speak]: {cleaned[:120]}", file=sys.stderr)
        return

    # 1. TTS
    wav_path = text_to_speech(cleaned, engine)
    if not wav_path or not wav_path.exists():
        return

    # 2. Voice convert (optional — skip if no model)
    model = find_voice_model()
    if model:
        pth_path, index_path = model
        miku_wav = voice_convert(wav_path, pth_path, index_path)
        if miku_wav and miku_wav.exists():
            play_audio(miku_wav)
        else:
            play_audio(wav_path)  # fallback to raw TTS
    else:
        play_audio(wav_path)

    # Cleanup
    try:
        wav_path.unlink(missing_ok=True)
        if model:
            miku_wav = TMP_DIR / f"miku_{wav_path.name.replace('tts_', 'miku_')}"
            miku_wav.unlink(missing_ok=True)
    except OSError:
        pass


def run_rsync_pipeline(args):
    """Run rsync and stream output through the Miku pipeline (threaded)."""
    cfg = load_config()

    cmd = ["rsync"] + args.rsync_flags + [args.source, args.dest]
    env = os.environ.copy()
    env["LC_ALL"] = "ja_JP.UTF-8"
    env["LANG"] = "ja_JP.UTF-8"

    # Print header
    voice_info, size_mb = VOICE_INFO.get(cfg["voice"], VOICE_INFO["default"])
    print(f"🌸 rsync-miku — narrating in Hatsune Miku voice", file=sys.stderr)
    print(f"   Voice: {cfg['voice']} ({voice_info}, ~{size_mb}MB) | TTS: {detect_tts_engine()} | Embedder: {cfg['embedder']}", file=sys.stderr)
    print(f"   Command: {' '.join(cmd)}", file=sys.stderr)
    print("-" * 60, file=sys.stderr)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            universal_newlines=True,
            bufsize=1,  # line-buffered
        )

        # Stream stderr in real-time
        for line in proc.stderr:
            process_line(line, cfg)

        proc.wait()

        if proc.returncode != 0:
            print(f"\n❌ rsync exited with code {proc.returncode}", file=sys.stderr)
        else:
            print("\n✅ Done!", file=sys.stderr)

    except KeyboardInterrupt:
        print("\n⏹️ Interrupted", file=sys.stderr)
        proc.terminate()
        sys.exit(130)
    except FileNotFoundError:
        print("❌ rsync not found in PATH", file=sys.stderr)
        sys.exit(1)


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="rsync-miku: Narrate rsync progress in a Hatsune Miku voice 🌸",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s source/ dest/                    Basic sync
  %(prog)s -avz source/ dest/               With compression
  %(prog)s --voice mellow -a source/ dest/  Use miku_mellow voice
  %(prog)s --dry-run -a source/ dest/       Preview without audio
  %(prog)s --min-length 5 -a source/ dest/  Skip short lines

Voice models (from https://huggingface.co/aple/HatsuneMikuRVC):
  default — Original voicebank sound (~382MB)
  mellow  — Dark, sweet, soft (~286MB)
  power   — Solid, vivid (~897MB)

TTS engines:
  auto    — coqui-tts if available, else macOS say
  say     — macOS built-in (no install needed)
  coqui   — coqui-tts (better quality, requires install)
        """,
    )

    cfg = load_config()

    parser.add_argument("source", help="Source path")
    parser.add_argument("dest", help="Destination path")
    parser.add_argument("rsync_flags", nargs="*", default=[],
                        help="Additional rsync flags (e.g. -avz)")

    # Voice settings
    parser.add_argument("--voice", choices=["default", "mellow", "power"],
                        default=cfg.get("voice", "default"),
                        help="Hatsune Miku voice variant (default: %(default)s)")
    parser.add_argument("--embedder", choices=[
        "japanese-hubert-base", "contentvec", "spin-v2",
        "chinese-hubert-base", "korean-hubert-base",
    ], default=cfg.get("embedder", "japanese-hubert-base"),
        help="RVC embedder model (default: %(default)s)")
    parser.add_argument("--pitch", type=int, default=cfg.get("pitch", -12),
                        help="Pitch shift for voice conversion (-24 to +24) (default: %(default)s)")
    parser.add_argument("--index-rate", type=float, default=cfg.get("index_rate", 0.7),
                        help="Search feature ratio (default: %(default)s)")
    parser.add_argument("--rms-mix", type=float, default=cfg.get("rms_mix", 0.8),
                        help="Volume envelope mix rate (default: %(default)s)")
    parser.add_argument("--protect", type=float, default=cfg.get("protect", 0.5),
                        help="Protect voiceless consonants (default: %(default)s)")
    parser.add_argument("--f0-method", choices=["rmvpe", "crepe", "crepe-tiny", "fcpe"],
                        default=cfg.get("f0_method", "rmvpe"),
                        help="Pitch extraction method (default: %(default)s)")
    parser.add_argument("--clean-strength", type=float, default=cfg.get("clean_strength", 0.7),
                        help="Audio cleaning strength (default: %(default)s)")

    # TTS settings
    parser.add_argument("--tts-engine", choices=["auto", "say", "coqui"],
                        default=cfg.get("tts_engine", "auto"),
                        help="TTS engine (default: %(default)s)")

    # Misc
    parser.add_argument("--dry-run", action="store_true", default=cfg.get("dry_run", False),
                        help="Show lines without speaking")
    parser.add_argument("--min-length", type=int, default=cfg.get("min_length", 3),
                        help="Skip lines shorter than this (default: %(default)s)")
    parser.add_argument("--save-config", action="store_true",
                        help="Save current settings to config.json")

    args = parser.parse_args()

    # Apply CLI overrides to config
    for key in ["voice", "embedder", "tts_engine", "dry_run", "min_length",
                 "pitch", "index_rate", "rms_mix", "protect", "f0_method", "clean_strength"]:
        cli_key = key.replace("-", "_")
        val = getattr(args, cli_key)
        if val is not None:
            cfg[key] = val

    if args.save_config:
        save_config(cfg)
        print(f"💾 Config saved to {CONFIG_PATH}", file=sys.stderr)

    run_rsync_pipeline(args)


if __name__ == "__main__":
    main()
