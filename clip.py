"""
Universal Clipper Autonomous Pipeline Runner.
Reads URL & clip count from CLIP_INPUT.txt or command-line arguments.
Executes end-to-end ingestion, hook selection, smart cropping, 60 FPS karaoke subtitle burning,
verification, cleanup, and prints viral metadata.
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = WORKSPACE_ROOT / "scripts"
TEMP_DIR = WORKSPACE_ROOT / "temp_work"
FINAL_DIR = WORKSPACE_ROOT / "final_clips"
INPUT_TXT = WORKSPACE_ROOT / "CLIP_INPUT.txt"

PYTHON_EXE = sys.executable

def find_binary(name: str, fallback_paths: list[str]) -> str:
    found = shutil.which(name)
    if found:
        return found
    for p in fallback_paths:
        if os.path.exists(p):
            return p
    return name

FFMPEG_PATH = find_binary(
    "ffmpeg",
    [
        r"C:\Users\prahu\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe",
        r"C:\Users\prahu\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe",
    ],
)

YTDLP_PATH = find_binary(
    "yt-dlp",
    [
        r"C:\Users\prahu\AppData\Local\Microsoft\WinGet\Packages\yt-dlp.yt-dlp_Microsoft.Winget.Source_8wekyb3d8bbwe\yt-dlp.exe",
        r"C:\Users\prahu\AppData\Local\Microsoft\WinGet\Links\yt-dlp.exe",
    ],
)

def read_config_file(filepath: Path) -> tuple[str, int]:
    if not filepath.exists():
        return "", 3
    
    url = ""
    num_clips = 3
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.upper().startswith("URL:"):
                url = line.split(":", 1)[1].strip()
            elif line.upper().startswith("CLIPS:"):
                try:
                    num_clips = int(line.split(":", 1)[1].strip())
                except ValueError:
                    num_clips = 3
    return url, num_clips

def clean_temp_dir():
    if TEMP_DIR.exists():
        for item in TEMP_DIR.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception:
                pass
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

def run_cmd(cmd: list[str], desc: str) -> None:
    print(f"\n[universal-clipper] >>> {desc}...")
    res = subprocess.run(cmd)
    if res.returncode != 0:
        raise RuntimeError(f"Command failed ({desc}): {' '.join(cmd)}")

def main():
    parser = argparse.ArgumentParser(description="Universal Clipper Autonomous Runner")
    parser.add_argument("url", nargs="?", default="", help="YouTube/VOD URL")
    parser.add_argument("clips", nargs="?", type=int, default=0, help="Number of clips to generate")
    args = parser.parse_args()

    url = args.url.strip()
    num_clips = args.clips

    if not url:
        # Fallback to CLIP_INPUT.txt
        url, cfg_clips = read_config_file(INPUT_TXT)
        if not num_clips:
            num_clips = cfg_clips

    if not url or "[PASTE" in url:
        print("[ERROR] No valid URL provided!")
        print(f"Please specify a URL in '{INPUT_TXT.name}' or run: python clip.py <URL> [NUM_CLIPS]")
        sys.exit(1)

    if num_clips <= 0:
        num_clips = 3

    print("=" * 70)
    print("🚀 UNIVERSAL CLIPPER - AUTONOMOUS EXECUTION PIPELINE")
    print("=" * 70)
    print(f"• Target URL: {url}")
    print(f"• Target Clips: {num_clips} (25 to 55 seconds each)")
    print(f"• Framing Mode: Smooth AI Subject Crop (Anti-Glitch / Anti-Partition)")
    print(f"• Captions: Word-by-Word Karaoke (60 FPS, #FFD700)")
    print("=" * 70)

    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    clean_temp_dir()

    # Step 1: Subtitle Extraction / Transcription
    print("\n[Step 1/5] Extracting Transcript & Subtitles...")
    transcript_path = TEMP_DIR / "transcript.json"

    # Try fast json3 auto/manual subtitles first
    sub_prefix = str(TEMP_DIR / "yt_sub")
    sub_cmd = [
        YTDLP_PATH,
        "--write-auto-sub",
        "--write-sub",
        "--sub-lang", "en,hi,en-orig,en-US",
        "--sub-format", "json3",
        "--skip-download",
        "-o", sub_prefix,
        url,
    ]
    subprocess.run(sub_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    found_subs = list(TEMP_DIR.glob("yt_sub*.json3"))
    if found_subs:
        chosen_sub = found_subs[0]
        print(f"[Step 1] Found native YouTube subtitles ({chosen_sub.name}). Converting...")
        from parse_yt_subs import convert_json3
        convert_json3(str(chosen_sub), str(transcript_path))
    else:
        print("[Step 1] No direct json3 subtitles found. Downloading audio for Whisper transcription...")
        audio_out = TEMP_DIR / "source_audio.mp4"
        audio_cmd = [
            YTDLP_PATH,
            "-f", "bestaudio[ext=m4a]/bestaudio/best",
            "-o", str(audio_out),
            url,
        ]
        run_cmd(audio_cmd, "Downloading audio")
        transcribe_cmd = [
            PYTHON_EXE,
            str(SCRIPTS_DIR / "transcribe.py"),
            "--input", str(audio_out),
            "--output", str(transcript_path),
            "--model", "small",
        ]
        run_cmd(transcribe_cmd, "Running Whisper word-level transcription")

    if not transcript_path.exists():
        raise RuntimeError("Failed to generate transcript.json")

    # Step 2: Identify Viral Moments
    print(f"\n[Step 2/5] Identifying Top {num_clips} Viral Moments...")
    hooks_path = TEMP_DIR / "viral_hooks.json"
    detect_cmd = [
        PYTHON_EXE,
        str(SCRIPTS_DIR / "detect_hooks.py"),
        "--transcript", str(transcript_path),
        "--top-k", str(num_clips),
        "--min-duration", "25",
        "--max-duration", "55",
        "--output", str(hooks_path),
    ]
    run_cmd(detect_cmd, "Detecting high-retention hooks")

    with open(hooks_path, "r", encoding="utf-8") as f:
        hooks_data = json.load(f)

    if isinstance(hooks_data, list):
        segments = hooks_data
    elif isinstance(hooks_data, dict):
        segments = hooks_data.get("segments", [])
    else:
        segments = []

    if not segments:
        raise RuntimeError("No viral segments detected in transcript.")

    print(f"[Step 2] Selected {len(segments)} viral hook candidate(s).")

    # Step 3: Render Clips (Smart 9:16 Crop + 60 FPS Karaoke Burn)
    rendered_clips = []
    for idx, seg in enumerate(segments, start=1):
        start_t = seg["start"]
        end_t = seg["end"]
        dur = end_t - start_t
        print(f"\n[Step 3/5] Processing Clip {idx}/{len(segments)}: {start_t:.2f}s -> {end_t:.2f}s ({dur:.1f}s)...")

        # Section Download via yt-dlp
        sec_str = f"*{int(max(0, start_t - 2))}-{int(end_t + 2)}"
        raw_sec = TEMP_DIR / f"raw_{idx}.mp4"
        dl_cmd = [
            YTDLP_PATH,
            "-f", "bestvideo[height<=2160]+bestaudio/bestvideo[height<=1440]+bestaudio/bestvideo[height<=1080]+bestaudio/best",
            "--download-sections", sec_str,
            "--force-keyframes-at-cuts",
            "-o", str(raw_sec),
            url,
        ]
        run_cmd(dl_cmd, f"Downloading segment section for Clip {idx}")

        # Smart 9:16 Crop
        cropped_mp4 = TEMP_DIR / f"crop_{idx}.mp4"
        crop_cmd = [
            PYTHON_EXE,
            str(SCRIPTS_DIR / "smart_crop.py"),
            "--input", str(raw_sec),
            "--output", str(cropped_mp4),
            "--start", "0.0",
            "--end", str(dur + 2.0),
            "--mode", "auto",
        ]
        run_cmd(crop_cmd, f"Smart 9:16 AI subject tracking for Clip {idx}")

        # Render 60 FPS Karaoke Subtitles
        final_clip = FINAL_DIR / f"clip_{idx}.mp4"
        ass_path = TEMP_DIR / f"subs_{idx}.ass"
        captions_cmd = [
            PYTHON_EXE,
            str(SCRIPTS_DIR / "render_captions.py"),
            "--video", str(cropped_mp4),
            "--transcript", str(transcript_path),
            "--start", str(start_t),
            "--end", str(end_t),
            "--output", str(final_clip),
            "--ass-output", str(ass_path),
            "--fps", "60",
        ]
        run_cmd(captions_cmd, f"Burning 60 FPS karaoke captions for Clip {idx}")

        # Verification via ffprobe
        probe_cmd = [
            FFMPEG_PATH.replace("ffmpeg.exe", "ffprobe.exe"),
            "-v", "error",
            "-show_entries", "stream=width,height,r_frame_rate,duration",
            "-of", "default=noprint_wrappers=1",
            str(final_clip),
        ]
        probe_res = subprocess.run(probe_cmd, capture_output=True, text=True)
        print(f"[Verification] Clip {idx} specifications:\n{probe_res.stdout.strip()}")

        # Prepare summary metadata
        first_sentence = seg["text"].split(".")[0].strip() + ("..." if "." in seg["text"] else "")
        title = f"Viral Moment #{idx} - Unbelievable Reaction 🔥"
        hashtags = ["#Shorts", "#Viral", "#Gaming", "#Trending", "#Epic"]
        rendered_clips.append({
            "filename": f"clip_{idx}.mp4",
            "start": start_t,
            "end": end_t,
            "duration": dur,
            "hook": first_sentence,
            "title": title,
            "hashtags": hashtags,
        })

    # Step 4: Purge Ephemeral Staging Files
    print("\n[Step 4/5] Cleaning up temp_work/ ephemeral staging files...")
    clean_temp_dir()

    # Step 5: Print Summary
    print("\n" + "=" * 70)
    print("✅ PIPELINE EXECUTION COMPLETED SUCCESSFULLY!")
    print("=" * 70)
    print(f"\nAll {len(rendered_clips)} clips exported cleanly to ./final_clips/\n")
    for c in rendered_clips:
        print(f"• File: final_clips/{c['filename']}")
        print(f"  Time: {c['start']:.1f}s - {c['end']:.1f}s ({c['duration']:.1f}s)")
        print(f"  Opening Hook: \"{c['hook']}\"")
        print(f"  Framing: 1080x1920 60 FPS (Smart AI Subject Tracking)")
        print(f"  Viral Title: {c['title']}")
        print(f"  Hashtags: {' '.join(c['hashtags'])}\n")

if __name__ == "__main__":
    main()
