"""
Universal Clipper Web UI - Studio Control Center.
Built with Python standard library ThreadingHTTPServer.
Features:
- Video Ingestion & 4K/2K/1080p source download
- Intelligent Hook Detection with Extreme Intensity scoring & niche-matched tags
- Resume Clipping from last final_clip timestamp
- Regenerate Metadata (titles & tags) on-demand
- Interactive Timeframe Selection: Preview all hot moments in source player & pick which to clip
- Custom timeframe drawer
- Ultra-HD 60 FPS vertical rendering (Lanczos4, color & contrast grading, unsharp masking)
- Draft Clips review with Bulk Approval and Individual Approval workflows
- Fully hides source video deletion during all active processing stages
"""

import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WORKSPACE_ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = WORKSPACE_ROOT / "scripts"
TEMP_DIR = WORKSPACE_ROOT / "temp_work"
DRAFT_DIR = WORKSPACE_ROOT / "draft_clips"
FINAL_DIR = WORKSPACE_ROOT / "final_clips"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from generate_metadata import generate_clip_metadata
except ImportError:
    def generate_clip_metadata(text, hook, idx=1, variation=0):
        return f"Viral Moment #{idx} 🤯", "#Shorts #Viral #Trending #HighStakes #FYP"

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

def get_last_clipped_info() -> tuple[int, float]:
    """Inspects final_clips/ to find the highest clip index and its end timestamp."""
    max_num = 0
    max_end_sec = 0.0

    if FINAL_DIR.exists():
        for f in FINAL_DIR.glob("clip_*.txt"):
            m = re.match(r"clip_(\d+)\.txt", f.name)
            if m:
                num = int(m.group(1))
                if num > max_num:
                    max_num = num
            try:
                content = f.read_text(encoding="utf-8")
                for line in content.splitlines():
                    if line.startswith("Timestamps:"):
                        ts_str = line.split(":", 1)[1]
                        secs = re.findall(r"(\d+(?:\.\d+)?)s", ts_str)
                        if len(secs) >= 2:
                            end_s = float(secs[1])
                            if end_s > max_end_sec:
                                max_end_sec = end_s
                        else:
                            hms = re.findall(r"(\d+):(\d+):(\d+)", ts_str)
                            if hms:
                                for h, m, s in hms:
                                    t = int(h)*3600 + int(m)*60 + int(s)
                                    if t > max_end_sec:
                                        max_end_sec = float(t)
            except Exception:
                pass

        for mp4 in FINAL_DIR.glob("clip_*.mp4"):
            m = re.match(r"clip_(\d+)\.mp4", mp4.name)
            if m:
                num = int(m.group(1))
                if num > max_num:
                    max_num = num

    return max_num, max_end_sec


class AppState:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with getattr(self, "lock", threading.Lock()):
            self.status = "idle"  # idle, downloading, transcribing, detecting, timeframes_ready, clipping, completed, error
            self.progress_pct = 0
            self.stage_message = "Ready to start"
            self.logs = []
            self.source_ready = False
            self.source_duration = 0.0
            self.source_filename = ""
            self.timeframes = []
            self.draft_clips = []
            self.approved_clips = []
            self.error_message = ""
            self.can_delete_source = False
            self.current_url = ""
            self.target_clips = 3
            self.subtitle_theme = "hormozi"
            self.last_clipped_num = 0
            self.last_clipped_end_sec = 0.0
            self.download_info = {
                "active": False,
                "percent": 0.0,
                "size": "",
                "speed": "",
                "eta": "",
                "status_text": ""
            }

    def log(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        print(f"[kernel] {entry}")
        with self.lock:
            self.logs.append(entry)

    def to_dict(self):
        max_num, max_end = get_last_clipped_info()
        with self.lock:
            return {
                "status": self.status,
                "progress_pct": self.progress_pct,
                "stage_message": self.stage_message,
                "logs": self.logs[-200:],
                "source_ready": self.source_ready,
                "source_duration": self.source_duration,
                "source_filename": self.source_filename,
                "timeframes": self.timeframes,
                "draft_clips": self.draft_clips,
                "approved_clips": self.approved_clips,
                "error_message": self.error_message,
                "can_delete_source": self.can_delete_source,
                "current_url": self.current_url,
                "target_clips": self.target_clips,
                "subtitle_theme": getattr(self, "subtitle_theme", "hormozi"),
                "download_info": dict(self.download_info),
                "last_clipped_num": max_num,
                "last_clipped_end_sec": max_end,
            }

state = AppState()

def generate_thumbnail(video_path: Path, thumb_path: Path) -> bool:
    if thumb_path.exists() and thumb_path.stat().st_size > 0:
        return True
    try:
        import cv2
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(fps * 0.5))
        ret, frame = cap.read()
        if not ret:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()
        if ret and frame is not None:
            cv2.imwrite(str(thumb_path), frame)
            cap.release()
            return True
        cap.release()
    except Exception as e:
        print(f"Error generating thumbnail for {video_path}: {e}")
    return False

def refresh_approved_clips():
    approved = []
    if FINAL_DIR.exists():
        def get_clip_num(filepath: Path) -> int:
            m = re.search(r"clip_(\d+)", filepath.stem)
            return int(m.group(1)) if m else 999999

        for mp4 in sorted(FINAL_DIR.glob("*.mp4"), key=get_clip_num):
            base_name = mp4.stem
            clip_num = get_clip_num(mp4)
            txt_file = FINAL_DIR / f"{base_name}.txt"
            thumb_file = FINAL_DIR / f"{base_name}.jpg"
            title = base_name.replace("_", " ").title()
            hashtags = "#Shorts #Viral #Trending"
            hook = ""
            timestamps = ""
            framing = "1080x1920 60 FPS (Lanczos4 + Color Graded)"

            if not thumb_file.exists() or thumb_file.stat().st_size == 0:
                generate_thumbnail(mp4, thumb_file)

            if txt_file.exists():
                try:
                    content = txt_file.read_text(encoding="utf-8")
                    for line in content.splitlines():
                        if line.startswith("Viral Title:"):
                            title = line.split(":", 1)[1].strip()
                        elif line.startswith("Hashtags:"):
                            hashtags = line.split(":", 1)[1].strip()
                        elif line.startswith("Opening Hook Line:") or line.startswith("Opening Hook:"):
                            hook = line.split(":", 1)[1].strip().strip('"')
                        elif line.startswith("Timestamps:"):
                            timestamps = line.split(":", 1)[1].strip()
                        elif line.startswith("Framing:"):
                            framing = line.split(":", 1)[1].strip()
                except Exception:
                    pass

            approved.append({
                "id": base_name,
                "clip_num": clip_num,
                "filename": mp4.name,
                "title": title,
                "hashtags": hashtags,
                "hook": hook,
                "timestamps": timestamps,
                "framing": framing,
                "poster": f"/media/final/{base_name}.jpg" if thumb_file.exists() else "",
                "size_mb": round(mp4.stat().st_size / (1024 * 1024), 2),
            })
    with state.lock:
        state.approved_clips = approved

refresh_approved_clips()


def run_scan_pipeline(url: str, num_detect: int = 15, resume: bool = False):
    """Stage 1: Downloads video, extracts transcript, and identifies ALL hot moments."""
    try:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        DRAFT_DIR.mkdir(parents=True, exist_ok=True)
        FINAL_DIR.mkdir(parents=True, exist_ok=True)

        start_after_sec = 0.0
        start_index = 1
        if resume:
            max_num, max_end = get_last_clipped_info()
            start_after_sec = max_end
            start_index = max_num + 1

        with state.lock:
            state.status = "downloading"
            state.progress_pct = 5
            state.stage_message = "Ingesting video in maximum available resolution (4K / 2K / 1080p)..."
            state.current_url = url
            state.can_delete_source = False
            state.timeframes = []

        state.log("=" * 60)
        state.log("🔍 Scanning Video For Intense Viral Hooks")
        state.log(f"Target URL: {url}")
        if resume:
            state.log(f"⚡ RESUME MODE: Starting scan after {start_after_sec:.1f}s (New clips start at #{start_index})")
        state.log("=" * 60)

        source_mp4 = TEMP_DIR / "source.mp4"

        # Step 1: Download
        if not source_mp4.exists() or source_mp4.stat().st_size == 0:
            state.log("[Stage 1/2] Downloading source video container...")
            with state.lock:
                state.download_info = {
                    "active": True,
                    "percent": 0.0,
                    "size": "Connecting...",
                    "speed": "",
                    "eta": "",
                    "status_text": "Connecting & Ingesting Video Stream..."
                }

            dl_cmd = [
                YTDLP_PATH,
                "--newline",
                "-f", "bestvideo[height<=2160]+bestaudio/bestvideo[height<=1440]+bestaudio/bestvideo[height<=1080]+bestaudio/best",
                "--merge-output-format", "mp4",
                "--extractor-args", "youtube:player_client=android,web",
                "--retries", "5",
                "-o", str(source_mp4),
                "--force-overwrites",
                url,
            ]
            state.log(f"Running download: yt-dlp to {source_mp4.name}...")
            proc = subprocess.Popen(dl_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            last_logged_pct = -10
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                m = re.search(r"\[download\]\s+([\d\.]+)%\s+of\s+~?([\d\.]+\w+)(?:\s+at\s+([\d\.]+\w+/s))?(?:\s+ETA\s+([\d:]+))?", line)
                if m:
                    pct = float(m.group(1))
                    size = m.group(2) or ""
                    speed = m.group(3) or ""
                    eta = m.group(4) or ""
                    with state.lock:
                        state.download_info = {
                            "active": True,
                            "percent": pct,
                            "size": size,
                            "speed": speed,
                            "eta": eta,
                            "status_text": f"Downloading Source Video: {pct:.1f}% ({size})"
                        }
                        state.progress_pct = int(pct * 0.35)
                        state.stage_message = f"Downloading source video: {pct:.1f}% ({speed})"
                    if pct - last_logged_pct >= 15:
                        state.log(f"Download Progress: {pct:.1f}% of {size} at {speed} (ETA {eta})")
                        last_logged_pct = pct

            proc.wait()
            if proc.returncode != 0 or not source_mp4.exists():
                raise RuntimeError(f"Failed to download video stream.")

            with state.lock:
                state.download_info = {
                    "active": True,
                    "percent": 100.0,
                    "size": f"{round(source_mp4.stat().st_size / (1024*1024), 2)} MB",
                    "speed": "",
                    "eta": "",
                    "status_text": "Download Complete! Extracting transcript..."
                }

        state.log(f"✓ Source video ready: {source_mp4.name} ({round(source_mp4.stat().st_size / (1024*1024), 2)} MB)")

        # Probe duration
        duration = 0.0
        try:
            import cv2
            cap = cv2.VideoCapture(str(source_mp4))
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            if fps > 0 and total_frames > 0:
                duration = float(total_frames / fps)
            cap.release()
        except Exception as e:
            state.log(f"Warning probing duration: {e}")

        with state.lock:
            state.source_ready = True
            state.source_filename = source_mp4.name
            state.source_duration = duration
            state.progress_pct = 35
            state.status = "transcribing"
            state.stage_message = "Source video preview ready! Extracting transcript..."

        state.log(f"Source preview initialized (Duration: {duration:.1f}s). Interactive player active.")

        # Step 2: Transcript
        transcript_path = TEMP_DIR / "transcript.json"
        state.log("[Stage 2/2] Extracting transcript & word-level timestamps...")

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
            state.log(f"Found YouTube native subtitles ({chosen_sub.name}). Converting to structured JSON...")
            import parse_yt_subs
            parse_yt_subs.convert_json3(str(chosen_sub), str(transcript_path))
            state.log("✓ Subtitle conversion complete.")
        else:
            state.log("No pre-rendered subtitles. Running Whisper audio transcription...")
            transcribe_cmd = [
                PYTHON_EXE,
                str(SCRIPTS_DIR / "transcribe.py"),
                "--input", str(source_mp4),
                "--output", str(transcript_path),
                "--model", "small",
            ]
            t_res = subprocess.run(transcribe_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=dict(os.environ, PYTHONIOENCODING="utf-8"))
            if t_res.returncode != 0:
                state.log(f"Transcription error: {t_res.stderr[-300:]}")
                raise RuntimeError("Whisper transcription failed.")
            state.log("✓ Whisper transcription completed.")

        # Step 3: Detect Hooks
        with state.lock:
            state.status = "detecting"
            state.progress_pct = 70
            state.stage_message = "Scoring timeline for extreme tension, cliffhangers & viral hooks..."

        state.log(f"Scanning transcript for top {num_detect} intense viral moments...")
        hooks_path = TEMP_DIR / "viral_hooks.json"
        detect_cmd = [
            PYTHON_EXE,
            str(SCRIPTS_DIR / "detect_hooks.py"),
            "--transcript", str(transcript_path),
            "--top-k", str(num_detect),
            "--min-duration", "25",
            "--max-duration", "55",
            "--start-after", str(start_after_sec),
            "--start-index", str(start_index),
            "--output", str(hooks_path),
        ]
        d_res = subprocess.run(detect_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        if d_res.returncode != 0 or not hooks_path.exists():
            state.log(f"Hook detection failed: {d_res.stderr}")
            raise RuntimeError("Hook detection failed.")

        with open(hooks_path, "r", encoding="utf-8") as f:
            hooks_data = json.load(f)

        if isinstance(hooks_data, list):
            segments = hooks_data
        elif isinstance(hooks_data, dict):
            segments = hooks_data.get("segments", [])
        else:
            segments = []

        if not segments:
            raise RuntimeError(f"No viral segments met criteria (Resume filter was > {start_after_sec:.1f}s).")

        timeframes_list = []
        for offset, seg in enumerate(segments):
            c_id = seg.get("id", start_index + offset)
            seg_text = seg.get("text", "") if isinstance(seg, dict) else ""
            hook_line = seg.get("hook", "") or (seg_text.split(".")[0].strip() + ("..." if "." in seg_text else ""))
            start_t = float(seg.get("start", 0.0))
            end_t = float(seg.get("end", 0.0))
            score_val = float(seg.get("score", 0.0))
            intensity_lbl = seg.get("intensity", "Extreme Intensity 🔥")
            title = seg.get("title") or f"Viral Moment #{c_id} 🤯"
            hashtags = seg.get("hashtags") or "#Shorts #Viral #Trending #HighStakes #FYP"

            timeframes_list.append({
                "id": c_id,
                "start": start_t,
                "end": end_t,
                "duration": round(end_t - start_t, 1),
                "hook": hook_line,
                "full_text": seg_text,
                "score": score_val,
                "intensity": intensity_lbl,
                "title": title,
                "hashtags": hashtags,
                "variation": 0,
                "selected": offset < state.target_clips,
            })

        with state.lock:
            state.timeframes = timeframes_list
            state.status = "timeframes_ready"
            state.progress_pct = 100
            state.stage_message = f"Found {len(timeframes_list)} moments! Select clips to render below, preview in player, or regenerate titles."
            state.can_delete_source = True

        state.log("=" * 60)
        state.log(f"✓ DETECTED {len(timeframes_list)} HIGH-INTENSITY VIRAL MOMENTS!")
        state.log("Click any moment to preview in player. Toggle checkboxes & click 'Render Selected Clips'.")
        state.log("=" * 60)

    except Exception as ex:
        state.log(f"❌ SCAN ERROR: {str(ex)}")
        with state.lock:
            state.status = "error"
            state.error_message = str(ex)
            state.stage_message = f"Error: {str(ex)}"
            state.can_delete_source = state.source_ready


def run_render_worker(selected_moments: list[dict]):
    """Stage 2: Renders only the user-selected moments with Ultra-HD Lanczos4, color grading, and 60 FPS karaoke."""
    try:
        source_mp4 = TEMP_DIR / "source.mp4"
        transcript_path = TEMP_DIR / "transcript.json"

        if not source_mp4.exists():
            raise RuntimeError("Source video not found. Please scan the video first.")

        # Pre-populate draft cards immediately so placeholders appear instantly in the UI
        initial_drafts = []
        for idx, tf in enumerate(selected_moments, start=1):
            start_t = float(tf["start"])
            end_t = float(tf["end"])
            dur = end_t - start_t
            clip_id = tf.get("id", idx)
            clip_filename = f"clip_{clip_id}.mp4"
            title = tf.get("title") or f"Viral Clip #{clip_id} 🤯"
            hashtags = tf.get("hashtags") or "#Shorts #Viral #Trending #FYP"

            initial_drafts.append({
                "id": clip_id,
                "filename": clip_filename,
                "start": start_t,
                "end": end_t,
                "duration": dur,
                "hook": tf.get("hook", ""),
                "full_text": tf.get("full_text", ""),
                "framing": "1080x1920 60 FPS (Lanczos4 + Color Graded)",
                "title": title,
                "hashtags": hashtags,
                "variation": tf.get("variation", 0),
                "selected": True,
                "approved": False,
                "status": "queued",
                "render_stage": "Queued in render pipeline...",
                "render_pct": 0,
                "poster": "",
                "size_mb": 0.0,
            })

        with state.lock:
            state.draft_clips = list(initial_drafts)
            state.status = "clipping"
            state.progress_pct = 10
            state.stage_message = f"Rendering {len(selected_moments)} selected moments in Ultra-HD 60 FPS..."
            state.can_delete_source = False

        state.log("=" * 60)
        state.log(f"🎬 Rendering {len(selected_moments)} User-Selected Clips")
        state.log("Quality: Lanczos4 Sharpness • Dynamic Color Equalizer • 60 FPS Karaoke")
        state.log("=" * 60)

        for idx, tf in enumerate(selected_moments, start=1):
            start_t = float(tf["start"])
            end_t = float(tf["end"])
            dur = end_t - start_t
            clip_id = tf.get("id", idx)

            clip_filename = f"clip_{clip_id}.mp4"
            draft_clip_path = DRAFT_DIR / clip_filename
            cropped_mp4 = TEMP_DIR / f"crop_{clip_id}.mp4"
            ass_path = TEMP_DIR / f"subs_{clip_id}.ass"

            state.log(f"\n--- [Clip {idx}/{len(selected_moments)}] Moment #{clip_id}: {start_t:.1f}s -> {end_t:.1f}s ({dur:.1f}s) ---")
            state.log(f"Viral Title: {tf.get('title')}")
            state.log(f"Niche Hashtags: {tf.get('hashtags')}")

            # 1. Update this clip to 'rendering' with Stage 1 (Smart Crop 25%)
            with state.lock:
                for d in state.draft_clips:
                    if d["id"] == clip_id:
                        d["status"] = "rendering"
                        d["render_stage"] = "AI Subject Tracking & 9:16 Crop..."
                        d["render_pct"] = 25
                pct = 10 + int(((idx - 1) / len(selected_moments)) * 85) + 5
                state.progress_pct = pct
                state.stage_message = f"Rendering Clip #{clip_id} ({idx}/{len(selected_moments)}): AI Subject Tracking..."

            state.log(f"Applying AI subject tracking & Lanczos4 9:16 crop...")
            crop_cmd = [
                PYTHON_EXE,
                str(SCRIPTS_DIR / "smart_crop.py"),
                "--input", str(source_mp4),
                "--output", str(cropped_mp4),
                "--start", str(start_t),
                "--end", str(end_t),
                "--mode", "auto",
            ]
            c_res = subprocess.run(crop_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=dict(os.environ, PYTHONIOENCODING="utf-8"))
            if c_res.returncode != 0:
                state.log(f"Smart crop error: {c_res.stderr[-300:]}")
                raise RuntimeError(f"Smart crop failed on clip {clip_id}")

            # 2. Update this clip to Stage 2 (Karaoke Captions & Color Grading 65%)
            with state.lock:
                for d in state.draft_clips:
                    if d["id"] == clip_id:
                        d["status"] = "rendering"
                        d["render_stage"] = "Burning 60 FPS Karaoke & Color Grading..."
                        d["render_pct"] = 65
                pct = 10 + int(((idx - 1) / len(selected_moments)) * 85) + 40
                state.progress_pct = pct
                state.stage_message = f"Rendering Clip #{clip_id} ({idx}/{len(selected_moments)}): Burning 60 FPS Karaoke..."

            state.log(f"Burning 60 FPS karaoke captions with color grading & unsharp mask...")
            theme_choice = getattr(state, "subtitle_theme", "hormozi")
            captions_cmd = [
                PYTHON_EXE,
                str(SCRIPTS_DIR / "render_captions.py"),
                "--video", str(cropped_mp4),
                "--transcript", str(transcript_path),
                "--start", str(start_t),
                "--end", str(end_t),
                "--output", str(draft_clip_path),
                "--ass-output", str(ass_path),
                "--theme", theme_choice,
                "--fps", "60",
            ]
            k_res = subprocess.run(captions_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=dict(os.environ, PYTHONIOENCODING="utf-8"))
            if k_res.returncode != 0:
                state.log(f"Caption render error: {k_res.stderr[-300:]}")
                raise RuntimeError(f"Caption render failed on clip {clip_id}")

            # 3. Generate thumbnail poster & finalize clip state
            draft_thumb = DRAFT_DIR / f"clip_{clip_id}.jpg"
            generate_thumbnail(draft_clip_path, draft_thumb)
            size_mb = round(draft_clip_path.stat().st_size / (1024 * 1024), 2)

            with state.lock:
                for d in state.draft_clips:
                    if d["id"] == clip_id:
                        d["status"] = "ready"
                        d["render_stage"] = "Render Complete ✓"
                        d["render_pct"] = 100
                        d["size_mb"] = size_mb
                        d["poster"] = f"/media/draft/clip_{clip_id}.jpg" if draft_thumb.exists() else ""
                pct = 10 + int((idx / len(selected_moments)) * 85)
                state.progress_pct = pct

            state.log(f"✓ Saved draft clip #{clip_id} to draft_clips/{clip_filename} ({size_mb} MB)")

        with state.lock:
            state.status = "completed"
            state.progress_pct = 100
            state.stage_message = "All selected clips rendered into draft_clips/! Review and approve below."
            state.can_delete_source = True

        state.log("=" * 60)
        state.log("🎉 ALL SELECTED DRAFT CLIPS GENERATED SUCCESSFULLY!")
        state.log("Review your vertical clips below. Use individual or bulk approval to move to final_clips/.")
        state.log("=" * 60)

    except Exception as ex:
        state.log(f"❌ RENDER ERROR: {str(ex)}")
        with state.lock:
            state.status = "error"
            state.error_message = str(ex)
            state.stage_message = f"Error: {str(ex)}"
            state.can_delete_source = state.source_ready


class StudioRequestHandler(http.server.BaseHTTPRequestHandler):

    def send_file_with_range(self, filepath: Path, content_type: str = "video/mp4"):
        if not filepath.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        file_size = filepath.stat().st_size
        range_header = self.headers.get("Range")

        if range_header:
            range_match = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if range_match:
                start = int(range_match.group(1))
                end = int(range_match.group(2)) if range_match.group(2) else file_size - 1
                start = min(start, file_size - 1)
                end = min(end, file_size - 1)
                length = end - start + 1

                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

                with open(filepath, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk_size = min(64 * 1024, remaining)
                        data = f.read(chunk_size)
                        if not data:
                            break
                        try:
                            self.wfile.write(data)
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                            return
                        remaining -= len(data)
                return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(file_size))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    return

    def do_HEAD(self):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.OK)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ["/", "/index.html"]:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
            return

        if path == "/api/status":
            refresh_approved_clips()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(state.to_dict()).encode("utf-8"))
            return

        if path == "/api/doctor":
            try:
                from doctor import run_diagnostics
                doc_data = run_diagnostics()
            except Exception as e:
                doc_data = {"error": str(e)}
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(doc_data).encode("utf-8"))
            return

        if path == "/media/source":
            source_mp4 = TEMP_DIR / "source.mp4"
            self.send_file_with_range(source_mp4, "video/mp4")
            return

        if path.startswith("/media/draft/"):
            filename = os.path.basename(path.replace("/media/draft/", ""))
            file_path = DRAFT_DIR / filename
            mime = "image/jpeg" if filename.lower().endswith((".jpg", ".jpeg")) else "video/mp4"
            self.send_file_with_range(file_path, mime)
            return

        if path.startswith("/media/final/"):
            filename = os.path.basename(path.replace("/media/final/", ""))
            file_path = FINAL_DIR / filename
            mime = "image/jpeg" if filename.lower().endswith((".jpg", ".jpeg")) else "video/mp4"
            self.send_file_with_range(file_path, mime)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else ""

        if path == "/api/set_theme":
            try:
                data = json.loads(body) if body else {}
                theme = data.get("theme", "hormozi")
                with state.lock:
                    state.subtitle_theme = theme
                state.log(f"Subtitle style preset switched to: {theme.upper()}")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "theme": theme}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(HTTPStatus.BAD_REQUEST)
                self.end_headers()
                return

        # Step 1: Scan video
        if path in ["/api/scan", "/api/start"]:
            if state.status in ["downloading", "transcribing", "detecting", "clipping"]:
                self.send_response(HTTPStatus.BAD_REQUEST)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"detail": "A pipeline task is already actively running."}).encode("utf-8"))
                return

            try:
                data = json.loads(body) if body else {}
                url = data.get("url", "").strip()
                num_clips = int(data.get("num_clips", 3))
                resume = bool(data.get("resume", False))
            except Exception:
                url = ""
                num_clips = 3
                resume = False

            if not url:
                self.send_response(HTTPStatus.BAD_REQUEST)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"detail": "Please provide a valid video link."}).encode("utf-8"))
                return

            state.reset()
            state.current_url = url
            state.target_clips = max(1, min(10, num_clips))

            t = threading.Thread(target=run_scan_pipeline, args=(url, 15, resume), daemon=True)
            t.start()

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"message": "Scanning started", "url": url, "resume": resume}).encode("utf-8"))
            return

        # Step 2: Render selected timeframes
        if path == "/api/render-selected":
            if state.status in ["downloading", "transcribing", "detecting", "clipping"]:
                self.send_response(HTTPStatus.BAD_REQUEST)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"detail": "Processing is already active."}).encode("utf-8"))
                return

            try:
                data = json.loads(body) if body else {}
                selected = data.get("timeframes", [])
            except Exception:
                selected = []

            if not selected:
                self.send_response(HTTPStatus.BAD_REQUEST)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"detail": "Please select at least one timeframe to clip."}).encode("utf-8"))
                return

            t = threading.Thread(target=run_render_worker, args=(selected,), daemon=True)
            t.start()

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"message": "Rendering started", "count": len(selected)}).encode("utf-8"))
            return

        # Step 3: Regenerate metadata for a clip on-demand
        if path == "/api/regenerate-metadata":
            try:
                data = json.loads(body) if body else {}
                text = data.get("text", "")
                hook = data.get("hook", "")
                clip_id = int(data.get("id", 1))
                variation = int(data.get("variation", 0)) + 1
            except Exception:
                text, hook, clip_id, variation = "", "", 1, 1

            new_title, new_hashtags = generate_clip_metadata(text, hook, clip_id, variation)

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "title": new_title,
                "hashtags": new_hashtags,
                "variation": variation
            }).encode("utf-8"))
            return

        # Step 4: Bulk approve drafts
        if path == "/api/bulk-approve":
            try:
                req_data = json.loads(body) if body else {}
                clip_ids = req_data.get("clip_ids", [])
                custom_metadata = req_data.get("metadata", {})
            except Exception:
                clip_ids = []
                custom_metadata = {}

            approved_count = 0
            for cid in clip_ids:
                draft_file = DRAFT_DIR / f"clip_{cid}.mp4"
                if not draft_file.exists():
                    continue
                final_file = FINAL_DIR / f"clip_{cid}.mp4"
                txt_file = FINAL_DIR / f"clip_{cid}.txt"
                draft_thumb = DRAFT_DIR / f"clip_{cid}.jpg"
                final_thumb = FINAL_DIR / f"clip_{cid}.jpg"

                shutil.move(str(draft_file), str(final_file))
                if draft_thumb.exists():
                    shutil.move(str(draft_thumb), str(final_thumb))
                else:
                    generate_thumbnail(final_file, final_thumb)

                meta = custom_metadata.get(str(cid), {})
                draft_info = next((d for d in state.draft_clips if d["id"] == cid), None)

                title = meta.get("title") or (draft_info["title"] if draft_info else f"Clip {cid}")
                hashtags = meta.get("hashtags") or (draft_info["hashtags"] if draft_info else "#Shorts #Viral")
                hook = draft_info["hook"] if draft_info else ""
                timestamps = f"{draft_info['start']:.2f}s - {draft_info['end']:.2f}s ({draft_info['duration']:.1f}s)" if draft_info else ""
                framing = draft_info.get("framing", "1080x1920 60 FPS (Lanczos4 + Color Graded)")

                metadata_content = (
                    f"==================================================\n"
                    f"APPROVED VIRAL CLIP #{cid}\n"
                    f"==================================================\n"
                    f"File: clip_{cid}.mp4\n"
                    f"Timestamps: {timestamps}\n"
                    f"Opening Hook Line: \"{hook}\"\n"
                    f"Framing: {framing}\n"
                    f"Viral Title: {title}\n"
                    f"Hashtags: {hashtags}\n"
                    f"==================================================\n"
                )
                txt_file.write_text(metadata_content, encoding="utf-8")
                approved_count += 1

            state.log(f"✓ Bulk approved {approved_count} clips and moved them to final_clips/.")
            refresh_approved_clips()

            with state.lock:
                for d in state.draft_clips:
                    if d["id"] in clip_ids:
                        d["approved"] = True

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"message": f"Bulk approved {approved_count} clips", "count": approved_count}).encode("utf-8"))
            return

        # Individual Approve
        if path.startswith("/api/approve/"):
            clip_id_str = path.replace("/api/approve/", "").strip("/")
            try:
                clip_id = int(clip_id_str)
            except ValueError:
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid clip ID")
                return

            draft_file = DRAFT_DIR / f"clip_{clip_id}.mp4"
            if not draft_file.exists():
                self.send_error(HTTPStatus.NOT_FOUND, "Draft clip not found")
                return

            final_file = FINAL_DIR / f"clip_{clip_id}.mp4"
            txt_file = FINAL_DIR / f"clip_{clip_id}.txt"
            draft_thumb = DRAFT_DIR / f"clip_{clip_id}.jpg"
            final_thumb = FINAL_DIR / f"clip_{clip_id}.jpg"

            shutil.move(str(draft_file), str(final_file))
            if draft_thumb.exists():
                shutil.move(str(draft_thumb), str(final_thumb))
            else:
                generate_thumbnail(final_file, final_thumb)

            try:
                req_data = json.loads(body) if body else {}
            except Exception:
                req_data = {}

            draft_info = next((d for d in state.draft_clips if d["id"] == clip_id), None)
            title = req_data.get("title") or (draft_info["title"] if draft_info else f"Clip {clip_id}")
            hashtags = req_data.get("hashtags") or (draft_info["hashtags"] if draft_info else "#Shorts #Viral")
            hook = draft_info["hook"] if draft_info else ""
            timestamps = f"{draft_info['start']:.2f}s - {draft_info['end']:.2f}s ({draft_info['duration']:.1f}s)" if draft_info else ""
            framing = draft_info.get("framing", "1080x1920 60 FPS (Lanczos4 + Color Graded)")

            metadata_content = (
                f"==================================================\n"
                f"APPROVED VIRAL CLIP #{clip_id}\n"
                f"==================================================\n"
                f"File: clip_{clip_id}.mp4\n"
                f"Timestamps: {timestamps}\n"
                f"Opening Hook Line: \"{hook}\"\n"
                f"Framing: {framing}\n"
                f"Viral Title: {title}\n"
                f"Hashtags: {hashtags}\n"
                f"==================================================\n"
            )
            txt_file.write_text(metadata_content, encoding="utf-8")
            state.log(f"✓ Approved Clip #{clip_id} moved to final_clips/clip_{clip_id}.mp4 and saved metadata.")

            with state.lock:
                for d in state.draft_clips:
                    if d["id"] == clip_id:
                        d["approved"] = True
            refresh_approved_clips()

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"message": "Approved", "clip_id": clip_id}).encode("utf-8"))
            return

        if path == "/api/delete-source":
            source_mp4 = TEMP_DIR / "source.mp4"
            if not source_mp4.exists():
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"message": "Source video already deleted or not found."}).encode("utf-8"))
                return

            if state.status in ["downloading", "transcribing", "detecting", "clipping"]:
                self.send_error(HTTPStatus.BAD_REQUEST, "Cannot delete source while processing is active.")
                return

            try:
                source_mp4.unlink()
                with state.lock:
                    state.source_ready = False
                    state.source_filename = ""
                state.log("🗑️ Source video (temp_work/source.mp4) deleted by user request.")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"message": "Source video deleted successfully."}).encode("utf-8"))
            except Exception as e:
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format, *args):
        return


HTML_PAGE = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Universal Clipper Studio</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: { 50: '#eef2ff', 500: '#6366f1', 600: '#4f46e5', 700: '#4338ca' }
          }
        }
      }
    }
  </script>
  <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
  <style>
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #18181b; }
    ::-webkit-scrollbar-thumb { background: #3f3f46; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #52525b; }
    .terminal-glow { box-shadow: inset 0 0 10px rgba(0, 0, 0, 0.7); }
    .pulse-amber { animation: pulseAmber 2s infinite; }
    @keyframes pulseAmber {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.6; }
    }
    /* Dynamic Caption Theme Live Preview */
    .karaoke-word {
      display: inline-block;
      margin: 0 4px;
      transition: all 0.15s ease-in-out;
      font-family: 'Arial Black', Impact, sans-serif;
    }
    .theme-hormozi .word-active {
      color: #FFD700 !important;
      -webkit-text-stroke: 1.8px #000;
      text-shadow: 0 3px 6px rgba(0,0,0,0.9);
      transform: scale(1.08);
    }
    .theme-hormozi .word-default {
      color: #FFFFFF !important;
      -webkit-text-stroke: 1.5px #000;
      text-shadow: 0 2px 4px rgba(0,0,0,0.8);
    }
    .theme-mrbeast .word-active {
      color: #33FF00 !important;
      -webkit-text-stroke: 1.8px #000;
      text-shadow: 0 3px 6px rgba(0,0,0,0.9);
      transform: scale(1.08);
    }
    .theme-mrbeast .word-default {
      color: #FFFFFF !important;
      -webkit-text-stroke: 1.5px #000;
      text-shadow: 0 2px 4px rgba(0,0,0,0.8);
    }
    .theme-cyber .word-active {
      color: #00FFFF !important;
      -webkit-text-stroke: 1.8px #000;
      text-shadow: 0 0 10px rgba(0,255,255,0.7), 0 3px 6px rgba(0,0,0,0.9);
      transform: scale(1.08);
    }
    .theme-cyber .word-default {
      color: #FFFFFF !important;
      -webkit-text-stroke: 1.5px #000;
      text-shadow: 0 2px 4px rgba(0,0,0,0.8);
    }
    .theme-crimson .word-active {
      color: #FF2233 !important;
      -webkit-text-stroke: 1.8px #000;
      text-shadow: 0 3px 6px rgba(0,0,0,0.9);
      transform: scale(1.08);
    }
    .theme-crimson .word-default {
      color: #FFFFFF !important;
      -webkit-text-stroke: 1.5px #000;
      text-shadow: 0 2px 4px rgba(0,0,0,0.8);
    }
    .theme-classic_white .word-active {
      color: #FFFFFF !important;
      -webkit-text-stroke: 1.2px #000;
      text-shadow: 0 2px 5px rgba(0,0,0,0.8);
      transform: scale(1.04);
      text-decoration: underline;
    }
    .theme-classic_white .word-default {
      color: #D1D5DB !important;
      -webkit-text-stroke: 1.2px #000;
      text-shadow: 0 2px 4px rgba(0,0,0,0.7);
    }
  </style>
</head>
<body class="bg-zinc-950 text-zinc-100 min-h-screen font-sans flex flex-col">

  <!-- Header -->
  <header class="border-b border-zinc-800 bg-zinc-900/80 backdrop-blur sticky top-0 z-50 px-6 py-3.5 flex items-center justify-between">
    <div class="flex items-center space-x-3">
      <div class="w-9 h-9 rounded-xl bg-gradient-to-tr from-indigo-600 to-violet-500 flex items-center justify-center shadow-lg shadow-indigo-500/20">
        <i class="fa-solid fa-scissors text-white text-base"></i>
      </div>
      <div>
        <h1 class="text-base font-bold tracking-tight bg-gradient-to-r from-white via-zinc-200 to-zinc-400 bg-clip-text text-transparent">Universal Clipper Studio</h1>
        <p class="text-xs text-zinc-400">Intelligent Hook Detection • Resume Clipping • Bulk Approval • 60 FPS Ultra-HD</p>
      </div>
    </div>
    <div class="flex items-center space-x-3">
      <button onclick="openDoctorModal()" class="px-3 py-1.5 rounded-full text-xs font-semibold border border-indigo-700/60 bg-indigo-950/40 text-indigo-300 hover:bg-indigo-900/50 transition-all flex items-center space-x-1.5 cursor-pointer shadow-sm">
        <i class="fa-solid fa-stethoscope text-indigo-400"></i>
        <span>System Doctor</span>
      </button>
      <div id="statusPill" class="px-3 py-1 rounded-full text-xs font-medium border border-zinc-700 bg-zinc-800 text-zinc-300 flex items-center space-x-2">
        <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
        <span id="statusText">System Ready</span>
      </div>
    </div>
  </header>

  <!-- Main Container -->
  <main class="flex-1 max-w-[1700px] w-full mx-auto p-6 space-y-6">

    <!-- Studio Control Deck: 2 Clean Sections (Ingestion & Subtitle Studio) -->
    <div class="bg-zinc-900/90 border border-zinc-800 rounded-2xl p-5 shadow-2xl backdrop-blur space-y-4">
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-5 items-stretch">
        
        <!-- LEFT: URL & Extraction Settings (7 cols) -->
        <div class="lg:col-span-7 flex flex-col justify-between space-y-3.5">
          <!-- Video Input -->
          <div>
            <div class="flex items-center justify-between mb-1.5">
              <label class="text-xs font-bold text-zinc-300 uppercase tracking-wider flex items-center space-x-1.5">
                <i class="fa-brands fa-youtube text-red-500"></i>
                <span>Video / Stream / Podcast Source URL</span>
              </label>
              <button type="button" onclick="pasteVideoUrl()" class="text-[11px] text-indigo-400 hover:text-indigo-300 flex items-center space-x-1 transition-colors cursor-pointer">
                <i class="fa-regular fa-paste"></i>
                <span>Paste Link</span>
              </button>
            </div>
            <div class="relative">
              <input type="text" id="videoUrlInput" 
                placeholder="https://www.youtube.com/watch?v=..." 
                value="https://www.youtube.com/watch?v=BGUTv2K_SWs"
                class="w-full bg-zinc-950 border border-zinc-700/80 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 rounded-xl px-4 py-2.5 text-xs text-white placeholder-zinc-500 transition-all font-mono">
            </div>
          </div>

          <!-- Bottom row of Left Panel: Target Clips, Resume, Scan CTA -->
          <div class="grid grid-cols-1 sm:grid-cols-12 gap-3 items-end pt-1">
            <div class="sm:col-span-4">
              <label class="block text-[11px] font-semibold text-zinc-400 uppercase tracking-wider mb-1">
                Target Clips Count
              </label>
              <div class="flex items-center space-x-1 bg-zinc-950 border border-zinc-800 rounded-xl p-1">
                <button type="button" onclick="setClipCount(3)" class="clip-count-btn flex-1 py-1 text-xs rounded-lg font-semibold bg-indigo-600 text-white" data-val="3">3</button>
                <button type="button" onclick="setClipCount(5)" class="clip-count-btn flex-1 py-1 text-xs rounded-lg font-semibold text-zinc-400 hover:text-white" data-val="5">5</button>
                <button type="button" onclick="setClipCount(10)" class="clip-count-btn flex-1 py-1 text-xs rounded-lg font-semibold text-zinc-400 hover:text-white" data-val="10">10</button>
                <input type="number" id="numClipsInput" value="3" min="1" max="25" class="w-10 bg-transparent text-center text-xs text-white font-bold border-l border-zinc-800 focus:outline-none">
              </div>
            </div>

            <div class="sm:col-span-8 flex flex-col justify-end space-y-1.5">
              <button id="scanBtn" onclick="scanVideo()" 
                class="w-full bg-gradient-to-r from-indigo-600 via-indigo-500 to-violet-600 hover:from-indigo-500 hover:to-violet-500 text-white font-bold text-sm px-5 py-2.5 rounded-xl shadow-lg shadow-indigo-600/30 flex items-center justify-center space-x-2 transition-all active:scale-[0.98] cursor-pointer">
                <i class="fa-solid fa-radar text-indigo-200"></i>
                <span>Scan & Detect Viral Hooks</span>
              </button>
              
              <div class="flex items-center justify-between text-[11px] text-zinc-400 px-1">
                <label class="flex items-center space-x-1.5 cursor-pointer">
                  <input type="checkbox" id="resumeCheckbox" class="w-3.5 h-3.5 rounded text-amber-500 focus:ring-amber-400 bg-zinc-950 border-zinc-700">
                  <span>Resume after last clip</span>
                </label>
                <span class="text-amber-400 font-mono text-[11px]" id="resumeTimeLabel">00:00</span>
              </div>
            </div>
          </div>
        </div>

        <!-- RIGHT: Captions Aesthetic & Dynamic Live Preview (5 cols) -->
        <div class="lg:col-span-5 bg-zinc-950/80 border border-zinc-800/80 rounded-xl p-3.5 flex flex-col justify-between">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs font-bold text-zinc-300 uppercase tracking-wider flex items-center space-x-1.5">
              <i class="fa-solid fa-closed-captioning text-amber-400"></i>
              <span>Captions Style & Live Preview</span>
            </span>
            <span id="activeStyleBadge" class="text-[10px] font-mono px-2 py-0.5 rounded-md bg-amber-500/10 text-amber-400 border border-amber-500/20">Hormozi Gold</span>
          </div>

          <!-- Hidden select for compatibility -->
          <select id="subtitleThemeSelect" class="hidden">
            <option value="hormozi" selected>Hormozi Gold</option>
            <option value="mrbeast">MrBeast Lime</option>
            <option value="cyber">Cyber Cyan</option>
            <option value="crimson">Crimson Red</option>
            <option value="classic_white">Classic White</option>
          </select>

          <!-- Style Selection Pills -->
          <div class="grid grid-cols-5 gap-1.5 mb-2.5" id="stylePillsGroup">
            <button type="button" onclick="selectSubtitleTheme('hormozi')" data-theme="hormozi"
              class="theme-pill px-1 py-1.5 rounded-lg text-center text-[10px] font-bold border transition-all bg-indigo-600/20 border-indigo-500 text-white shadow-sm ring-1 ring-indigo-500/50 cursor-pointer">
              <span class="block w-2.5 h-2.5 rounded-full bg-[#FFD700] mx-auto mb-1 shadow-sm"></span>
              Hormozi
            </button>
            <button type="button" onclick="selectSubtitleTheme('mrbeast')" data-theme="mrbeast"
              class="theme-pill px-1 py-1.5 rounded-lg text-center text-[10px] font-bold border transition-all bg-zinc-900 border-zinc-800 text-zinc-400 hover:text-white hover:border-zinc-700 cursor-pointer">
              <span class="block w-2.5 h-2.5 rounded-full bg-[#33FF00] mx-auto mb-1"></span>
              MrBeast
            </button>
            <button type="button" onclick="selectSubtitleTheme('cyber')" data-theme="cyber"
              class="theme-pill px-1 py-1.5 rounded-lg text-center text-[10px] font-bold border transition-all bg-zinc-900 border-zinc-800 text-zinc-400 hover:text-white hover:border-zinc-700 cursor-pointer">
              <span class="block w-2.5 h-2.5 rounded-full bg-[#00FFFF] mx-auto mb-1"></span>
              Cyber
            </button>
            <button type="button" onclick="selectSubtitleTheme('crimson')" data-theme="crimson"
              class="theme-pill px-1 py-1.5 rounded-lg text-center text-[10px] font-bold border transition-all bg-zinc-900 border-zinc-800 text-zinc-400 hover:text-white hover:border-zinc-700 cursor-pointer">
              <span class="block w-2.5 h-2.5 rounded-full bg-[#FF0000] mx-auto mb-1"></span>
              Crimson
            </button>
            <button type="button" onclick="selectSubtitleTheme('classic_white')" data-theme="classic_white"
              class="theme-pill px-1 py-1.5 rounded-lg text-center text-[10px] font-bold border transition-all bg-zinc-900 border-zinc-800 text-zinc-400 hover:text-white hover:border-zinc-700 cursor-pointer">
              <span class="block w-2.5 h-2.5 rounded-full bg-white mx-auto mb-1"></span>
              Classic
            </button>
          </div>

          <!-- Interactive Live Karaoke Text Preview Card -->
          <div class="relative bg-zinc-900/90 rounded-lg border border-zinc-800/80 p-3 h-20 flex flex-col items-center justify-center overflow-hidden shadow-inner">
            <div class="absolute top-1 left-2 text-[9px] text-zinc-500 font-mono tracking-widest uppercase">Live 9:16 Feed Preview</div>
            <div id="karaokeLivePreview" class="text-center font-black tracking-wide text-sm sm:text-base transition-all select-none pt-1 theme-hormozi">
              <span class="karaoke-word word-default">WAIT</span>
              <span class="karaoke-word word-default">FOR</span>
              <span class="karaoke-word word-active">THIS</span>
              <span class="karaoke-word word-default">MOMENT!</span>
            </div>
            <div class="absolute bottom-1 right-2 text-[9px] text-zinc-500 font-mono flex items-center space-x-1">
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
              <span>Word-by-Word Karaoke</span>
            </div>
          </div>
        </div>

      </div>

      <!-- Live Progress Bar -->
      <div id="progressSection" class="pt-3 border-t border-zinc-800/80 hidden">
        <div class="flex justify-between items-center text-xs mb-1.5">
          <span id="stageMsg" class="text-zinc-300 font-medium">Initializing pipeline...</span>
          <span id="progressPct" class="text-indigo-400 font-bold">0%</span>
        </div>
        <div class="w-full bg-zinc-800 rounded-full h-2 overflow-hidden">
          <div id="progressBar" class="bg-gradient-to-r from-indigo-500 to-violet-500 h-2 rounded-full transition-all duration-300" style="width: 0%"></div>
        </div>
      </div>
    </div>

    <!-- 2-Column Split: Source Video / Timeframes & Kernel Live Console -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">

      <!-- LEFT COLUMN: Source Video & Interactive Timeframes (7 cols) -->
      <div class="lg:col-span-7 space-y-6">

        <!-- Source Video Card -->
        <div class="bg-zinc-900 border border-zinc-800 rounded-2xl p-5 shadow-xl flex flex-col">
          <div class="flex items-center justify-between mb-3">
            <div class="flex items-center space-x-2">
              <span class="w-2.5 h-2.5 rounded-full bg-indigo-500"></span>
              <h2 class="text-sm font-bold uppercase tracking-wider text-zinc-200">Source Video Preview</h2>
              <span id="sourceDurationBadge" class="text-xs bg-zinc-800 text-zinc-400 px-2 py-0.5 rounded-md hidden"></span>
            </div>
            <!-- Delete Source Button (Completely HIDDEN during all active processing) -->
            <button id="deleteSourceBtn" onclick="deleteSourceVideo()" class="hidden text-xs px-3 py-1.5 rounded-lg border border-red-900/50 bg-red-950/30 text-red-400 hover:bg-red-900/40 hover:text-red-200 transition-all flex items-center space-x-1.5">
              <i class="fa-solid fa-trash-can"></i>
              <span>Delete Source Video</span>
            </button>
          </div>

          <!-- Video Player Box -->
          <div class="relative w-full aspect-video bg-black rounded-xl overflow-hidden border border-zinc-800/80 flex items-center justify-center">
            <video id="sourcePlayer" controls class="w-full h-full object-contain hidden" preload="metadata">
              <source id="sourceVideoTag" src="" type="video/mp4">
            </video>
            <div id="sourcePlaceholder" class="text-center p-6 text-zinc-500 flex flex-col items-center space-y-3">
              <i class="fa-regular fa-circle-play text-4xl text-zinc-600"></i>
              <div class="text-xs max-w-xs">Source video will stream here as soon as download completes. Scrub or click any detected moment to preview.</div>
            </div>
          </div>
        </div>

        <!-- Detected Hot Moments & Timeframe Selection Panel -->
        <div class="bg-zinc-900 border border-zinc-800 rounded-2xl p-5 shadow-xl">
          <div class="flex items-center justify-between mb-4">
            <div>
              <h3 class="text-sm font-bold uppercase tracking-wider text-zinc-200 flex items-center space-x-2">
                <i class="fa-solid fa-fire text-amber-500"></i>
                <span>Detected Hot Moments (Select To Clip)</span>
              </h3>
              <p class="text-xs text-zinc-400 mt-0.5">Click any card to preview in player. Check the clips you want to render.</p>
            </div>
            <div class="flex items-center space-x-2">
              <button onclick="selectAllTimeframes(true)" class="text-xs text-indigo-400 hover:text-indigo-300 font-medium px-2 py-1 bg-zinc-800 rounded">Select All</button>
              <button onclick="selectAllTimeframes(false)" class="text-xs text-zinc-400 hover:text-zinc-200 font-medium px-2 py-1 bg-zinc-800 rounded">Clear</button>
            </div>
          </div>

          <!-- Timeframes Container -->
          <div id="timeframesContainer" class="space-y-3 max-h-[380px] overflow-y-auto pr-1">
            <div class="text-xs text-zinc-500 py-8 text-center">
              Click <b>"Scan & Detect Hooks"</b> above to analyze the video and isolate hot moments.
            </div>
          </div>

          <!-- Action Bar: Render Selected Clips & Custom Clip -->
          <div id="renderActionSection" class="mt-4 pt-4 border-t border-zinc-800 flex flex-col sm:flex-row items-center justify-between gap-3">
            <div class="text-xs text-zinc-300 font-medium">
              <span id="selectedCountBadge" class="font-bold text-amber-400">0</span> moments selected
            </div>
            <div class="flex items-center space-x-2 w-full sm:w-auto">
              <button id="renderSelectedBtn" onclick="renderSelectedClips()" disabled
                class="flex-1 sm:flex-none bg-gradient-to-r from-amber-500 to-orange-600 hover:from-amber-400 hover:to-orange-500 text-white font-bold text-xs px-5 py-2.5 rounded-xl shadow-lg shadow-orange-500/20 flex items-center justify-center space-x-2 transition-all opacity-40 cursor-not-allowed">
                <i class="fa-solid fa-play"></i>
                <span id="renderBtnText">Render Selected Clips</span>
              </button>
            </div>
          </div>

          <!-- Custom Timeframe Input Accordion -->
          <div class="mt-4 pt-3 border-t border-zinc-800/80">
            <details class="text-xs text-zinc-400">
              <summary class="cursor-pointer font-semibold hover:text-zinc-200 flex items-center space-x-1.5">
                <i class="fa-solid fa-plus text-indigo-400"></i>
                <span>Add Custom Timeframe from Video Player</span>
              </summary>
              <div class="grid grid-cols-1 sm:grid-cols-4 gap-2.5 mt-3 p-3 bg-zinc-950 rounded-xl border border-zinc-800">
                <div>
                  <label class="block text-[10px] text-zinc-400 mb-1">Start (Seconds)</label>
                  <input type="number" id="customStart" step="0.5" value="0.0" class="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1 text-xs text-white">
                </div>
                <div>
                  <label class="block text-[10px] text-zinc-400 mb-1">End (Seconds)</label>
                  <input type="number" id="customEnd" step="0.5" value="30.0" class="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1 text-xs text-white">
                </div>
                <div class="flex items-end">
                  <button onclick="setCustomFromPlayer()" class="w-full bg-zinc-800 hover:bg-zinc-700 text-zinc-200 py-1.5 px-2 rounded-lg text-[11px] font-medium" title="Set start time to current player position">
                    Use Player Time
                  </button>
                </div>
                <div class="flex items-end">
                  <button onclick="addCustomTimeframe()" class="w-full bg-indigo-600 hover:bg-indigo-500 text-white py-1.5 px-2 rounded-lg text-[11px] font-bold">
                    + Add To List
                  </button>
                </div>
              </div>
            </details>
          </div>

        </div>

      </div>

      <!-- RIGHT COLUMN: Kernel Think & Live Console (5 cols) -->
      <div class="lg:col-span-5 flex flex-col">
        <div class="bg-zinc-900 border border-zinc-800 rounded-2xl p-5 shadow-xl flex-1 flex flex-col">
          <div class="flex items-center justify-between mb-3">
            <div class="flex items-center space-x-2">
              <i class="fa-solid fa-terminal text-emerald-400"></i>
              <h3 class="text-sm font-bold uppercase tracking-wider text-zinc-200">Kernel Process & Live Logs</h3>
            </div>
            <div class="flex items-center space-x-2 text-xs text-zinc-500">
              <span class="inline-block w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
              <span>Live Console</span>
            </div>
          </div>

          <!-- Terminal Screen -->
          <div id="terminalWindow" class="flex-1 bg-black/90 border border-zinc-800 rounded-xl p-4 font-mono text-xs text-zinc-300 overflow-y-auto max-h-[640px] min-h-[500px] space-y-1 terminal-glow">
            <div class="text-zinc-500">// Universal Clipper engine initialized. Ready.</div>
          </div>
        </div>
      </div>

    </div>

    <!-- SECTION 3: DRAFT CLIPS REVIEW WITH BULK APPROVAL -->
    <section class="bg-zinc-900 border border-zinc-800 rounded-2xl p-6 shadow-xl">
      <div class="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6 pb-4 border-b border-zinc-800">
        <div>
          <h2 class="text-base font-bold text-zinc-100 flex items-center space-x-2">
            <i class="fa-solid fa-layer-group text-indigo-400"></i>
            <span>Draft Clips Review (draft_clips/)</span>
          </h2>
          <p class="text-xs text-zinc-400 mt-1">Review 9:16 Shorts rendered with Lanczos4 sharpness & burned captions. Edit title & tags, regenerate if needed, or approve in bulk.</p>
        </div>

        <!-- Bulk Action Controls -->
        <div id="draftBulkControls" class="flex items-center space-x-2.5">
          <span id="draftCountBadge" class="text-xs bg-indigo-950 text-indigo-300 border border-indigo-800 px-3 py-1 rounded-full font-semibold">0 Drafts</span>
          <button onclick="toggleAllDrafts(true)" class="text-xs px-2.5 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg font-medium">Select All</button>
          <button id="bulkApproveBtn" onclick="bulkApproveDrafts()" disabled
            class="text-xs px-3.5 py-1.5 bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-bold rounded-lg shadow-lg shadow-emerald-600/20 opacity-40 cursor-not-allowed flex items-center space-x-1.5 transition-all">
            <i class="fa-solid fa-check-double"></i>
            <span id="bulkApproveText">Approve Selected</span>
          </button>
        </div>
      </div>

      <div id="draftClipsGrid" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
        <div class="col-span-full py-12 text-center text-zinc-500 text-xs">
          No draft clips rendered yet. Scan a video above, select your favorite moments, and click <b>Render Selected Clips</b>.
        </div>
      </div>
    </section>

    <!-- SECTION 4: APPROVED FINAL CLIPS -->
    <section class="bg-zinc-900 border border-zinc-800 rounded-2xl p-6 shadow-xl">
      <div class="flex items-center justify-between mb-6 pb-4 border-b border-zinc-800">
        <div>
          <h2 class="text-base font-bold text-emerald-400 flex items-center space-x-2">
            <i class="fa-solid fa-circle-check"></i>
            <span>Approved Final Clips (final_clips/)</span>
          </h2>
          <p class="text-xs text-zinc-400 mt-1">Ready for publishing to YouTube Shorts, Instagram Reels, and TikTok with verified metadata .txt files.</p>
        </div>
        <span id="approvedCountBadge" class="text-xs bg-emerald-950 text-emerald-300 border border-emerald-800 px-3 py-1 rounded-full font-semibold">0 Approved</span>
      </div>

      <div id="approvedClipsGrid" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
        <div class="col-span-full py-8 text-center text-zinc-500 text-xs">
          No approved clips yet. Approve draft clips above to move them here.
        </div>
      </div>
    </section>

    <!-- System Diagnostics Doctor Modal -->
    <div id="doctorModal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
      <div class="bg-zinc-900 border border-zinc-700 rounded-2xl max-w-xl w-full p-6 shadow-2xl relative space-y-4">
        <div class="flex items-center justify-between border-b border-zinc-800 pb-3">
          <div class="flex items-center space-x-2.5">
            <i class="fa-solid fa-stethoscope text-indigo-400 text-lg"></i>
            <h3 class="text-base font-bold text-white">System Diagnostics & Environment Doctor</h3>
          </div>
          <button onclick="closeDoctorModal()" class="text-zinc-400 hover:text-white text-lg px-2 py-1">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
        <div id="doctorLoading" class="py-8 text-center text-zinc-400 text-sm">
          <i class="fa-solid fa-circle-notch fa-spin text-2xl text-indigo-500 mb-2"></i>
          <p>Running hardware & environment diagnostics...</p>
        </div>
        <div id="doctorContent" class="space-y-3 text-xs hidden">
          <!-- Dynamically populated -->
        </div>
        <div class="border-t border-zinc-800 pt-3 flex justify-end">
          <button onclick="closeDoctorModal()" class="bg-zinc-800 hover:bg-zinc-700 text-white px-4 py-1.5 rounded-lg text-xs font-semibold">Close</button>
        </div>
      </div>
    </div>

  </main>

  <footer class="border-t border-zinc-800/80 py-4 text-center text-xs text-zinc-500">
    Universal Clipper Studio • 1080x1920 60 FPS Ultra-HD • Lanczos4 Sharpness • Dynamic Niche Metadata
  </footer>

  <script>
    let pollInterval = null;
    let lastLogCount = 0;
    let lastApprovedFingerprint = "";
    let lastDraftsFingerprint = "";
    let lastTimeframesFingerprint = "";
    let currentPollingSpeed = 800;
    let loadedTimeframes = [];
    let loadedDrafts = [];

    // --- Dynamic Karaoke Live Visualizer ---
    const PREVIEW_WORDS = ["WAIT", "FOR", "THIS", "MOMENT!"];
    let previewWordIndex = 2;
    let currentSubtitleTheme = 'hormozi';

    function renderKaraokePreview() {
      const container = document.getElementById('karaokeLivePreview');
      if (!container) return;
      container.className = `text-center font-black tracking-wide text-sm sm:text-base transition-all select-none pt-1 theme-${currentSubtitleTheme}`;
      let html = '';
      PREVIEW_WORDS.forEach((word, idx) => {
        const isActive = idx === previewWordIndex;
        const cls = isActive ? 'word-active' : 'word-default';
        html += `<span class="karaoke-word ${cls}">${word}</span>`;
      });
      container.innerHTML = html;
      previewWordIndex = (previewWordIndex + 1) % PREVIEW_WORDS.length;
    }

    setInterval(renderKaraokePreview, 650);

    async function pasteVideoUrl() {
      try {
        const text = await navigator.clipboard.readText();
        if (text && text.trim()) {
          document.getElementById('videoUrlInput').value = text.trim();
        }
      } catch (err) {
        alert("Please use Ctrl+V to paste your video link.");
      }
    }

    function setClipCount(val) {
      document.getElementById('numClipsInput').value = val;
      document.querySelectorAll('.clip-count-btn').forEach(btn => {
        if (parseInt(btn.getAttribute('data-val')) === val) {
          btn.className = 'clip-count-btn flex-1 py-1 text-xs rounded-lg font-semibold bg-indigo-600 text-white';
        } else {
          btn.className = 'clip-count-btn flex-1 py-1 text-xs rounded-lg font-semibold text-zinc-400 hover:text-white';
        }
      });
    }

    async function selectSubtitleTheme(theme) {
      currentSubtitleTheme = theme;
      const themeSelect = document.getElementById('subtitleThemeSelect');
      if (themeSelect) themeSelect.value = theme;

      const badgeNames = {
        'hormozi': 'Hormozi Gold (#FFD700)',
        'mrbeast': 'MrBeast Lime (#33FF00)',
        'cyber': 'Cyber Cyan (#00FFFF)',
        'crimson': 'Crimson Red (#FF0000)',
        'classic_white': 'Classic White'
      };
      const badge = document.getElementById('activeStyleBadge');
      if (badge) badge.textContent = badgeNames[theme] || theme;

      document.querySelectorAll('.theme-pill').forEach(btn => {
        const t = btn.getAttribute('data-theme');
        if (t === theme) {
          btn.className = 'theme-pill px-1 py-1.5 rounded-lg text-center text-[10px] font-bold border transition-all bg-indigo-600/20 border-indigo-500 text-white shadow-sm ring-1 ring-indigo-500/50 cursor-pointer';
        } else {
          btn.className = 'theme-pill px-1 py-1.5 rounded-lg text-center text-[10px] font-bold border transition-all bg-zinc-900 border-zinc-800 text-zinc-400 hover:text-white hover:border-zinc-700 cursor-pointer';
        }
      });

      renderKaraokePreview();
      await changeSubtitleTheme(theme);
    }

    async function changeSubtitleTheme(theme) {
      try {
        await fetch('/api/set_theme', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({theme})
        });
      } catch (err) {
        console.error('Failed to set subtitle theme:', err);
      }
    }

    async function openDoctorModal() {
      const modal = document.getElementById('doctorModal');
      const loading = document.getElementById('doctorLoading');
      const content = document.getElementById('doctorContent');
      modal.classList.remove('hidden');
      loading.classList.remove('hidden');
      content.classList.add('hidden');

      try {
        const res = await fetch('/api/doctor');
        const data = await res.json();
        loading.classList.add('hidden');
        content.classList.remove('hidden');

        content.innerHTML = `
          <div class="p-3.5 rounded-xl bg-zinc-950 border border-zinc-800 space-y-2">
            <div class="flex justify-between items-center">
              <span class="text-zinc-400">Python Environment:</span>
              <span class="font-bold ${data.python_ok ? 'text-emerald-400' : 'text-red-400'}">${data.python_version || 'Unknown'} ${data.python_ok ? '✓' : '✗'}</span>
            </div>
            <div class="flex justify-between items-center">
              <span class="text-zinc-400">Operating System:</span>
              <span class="text-zinc-200 font-semibold">${data.os || 'Windows'}</span>
            </div>
            <div class="flex justify-between items-center">
              <span class="text-zinc-400">FFmpeg Subsystem:</span>
              <span class="font-bold ${data.ffmpeg_ok ? 'text-emerald-400' : 'text-red-400'}">${data.ffmpeg_ok ? 'Installed (libass Active) ✓' : 'Missing ✗'}</span>
            </div>
            <div class="flex justify-between items-center">
              <span class="text-zinc-400">Encoders & Acceleration:</span>
              <span class="text-amber-300 font-semibold">${(data.hw_accel && data.hw_accel.length > 0) ? data.hw_accel.join(', ') : 'Software CPU libx264'}</span>
            </div>
          </div>

          <div class="p-3.5 rounded-xl bg-zinc-950 border border-zinc-800 space-y-2">
            <div class="flex justify-between items-center">
              <span class="text-zinc-400">MediaPipe 9:16 Face Tracker:</span>
              <span class="font-bold ${data.mediapipe_ok ? 'text-emerald-400' : 'text-red-400'}">${data.mediapipe_ok ? 'Ready ✓' : 'Missing ✗'}</span>
            </div>
            <div class="flex justify-between items-center">
              <span class="text-zinc-400">Whisper AI / CTranslate2:</span>
              <span class="font-bold ${data.whisper_ok ? 'text-emerald-400' : 'text-red-400'}">${data.whisper_ok ? (data.cuda_gpus > 0 ? `CUDA Active (${data.cuda_gpus} GPU) ⚡` : 'CPU Int8 Mode ✓') : 'Missing ✗'}</span>
            </div>
            <div class="flex justify-between items-center">
              <span class="text-zinc-400">Local Ollama LLM:</span>
              <span class="font-semibold ${data.ollama_online ? 'text-emerald-400' : 'text-zinc-400'}">${data.ollama_online ? 'Online (LLM Scoring Active) 🧠' : 'Offline (Fast NLP Engine Active)'}</span>
            </div>
            <div class="flex justify-between items-center">
              <span class="text-zinc-400">Disk Space Free:</span>
              <span class="text-zinc-200 font-semibold">${data.free_disk_gb ? data.free_disk_gb + ' GB' : 'Adequate'}</span>
            </div>
          </div>
        `;
      } catch (e) {
        loading.classList.add('hidden');
        content.classList.remove('hidden');
        content.innerHTML = `<div class="p-4 bg-red-950/40 border border-red-800 text-red-200 rounded-xl">Diagnostics error: ${e.message}</div>`;
      }
    }

    function closeDoctorModal() {
      document.getElementById('doctorModal').classList.add('hidden');
    }

    async function scanVideo() {
      const url = document.getElementById('videoUrlInput').value.trim();
      const numClips = parseInt(document.getElementById('numClipsInput').value) || 3;
      const resume = document.getElementById('resumeCheckbox').checked;

      if (!url) {
        alert('Please enter a valid YouTube/Stream video link.');
        return;
      }

      const scanBtn = document.getElementById('scanBtn');
      scanBtn.disabled = true;
      scanBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i><span>Scanning...</span>';
      document.getElementById('progressSection').classList.remove('hidden');

      try {
        const res = await fetch('/api/scan', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: url, num_clips: numClips, resume: resume })
        });
        const data = await res.json();
        if (!res.ok) {
          alert(data.detail || 'Failed to start scanning');
          scanBtn.disabled = false;
          scanBtn.innerHTML = '<i class="fa-solid fa-radar"></i><span>Scan & Detect Hooks</span>';
          return;
        }

        setPollingSpeed(800);
      } catch (err) {
        alert('Error connecting to backend: ' + err.message);
        scanBtn.disabled = false;
        scanBtn.innerHTML = '<i class="fa-solid fa-radar"></i><span>Scan & Detect Hooks</span>';
      }
    }

    async function renderSelectedClips() {
      const selectedTfs = loadedTimeframes.filter(tf => tf.selected);
      if (selectedTfs.length === 0) {
        alert('Please check at least one moment to render.');
        return;
      }

      const renderBtn = document.getElementById('renderSelectedBtn');
      renderBtn.disabled = true;
      renderBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i><span>Rendering ' + selectedTfs.length + ' Clips...</span>';

      try {
        const res = await fetch('/api/render-selected', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ timeframes: selectedTfs })
        });
        const data = await res.json();
        if (!res.ok) {
          alert(data.detail || 'Failed to start rendering.');
          updateSelectedCount();
          return;
        }
        setPollingSpeed(800);
      } catch (err) {
        alert('Error starting render: ' + err.message);
        updateSelectedCount();
      }
    }

    function setPollingSpeed(intervalMs) {
      if (currentPollingSpeed === intervalMs && pollInterval) return;
      currentPollingSpeed = intervalMs;
      if (pollInterval) clearInterval(pollInterval);
      pollInterval = setInterval(pollStatus, intervalMs);
      pollStatus();
    }

    function startPolling() {
      setPollingSpeed(1500);
    }

    async function pollStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();

        const statusText = document.getElementById('statusText');
        const scanBtn = document.getElementById('scanBtn');

        if (data.subtitle_theme && data.subtitle_theme !== currentSubtitleTheme) {
          selectSubtitleTheme(data.subtitle_theme);
        }

        if (data.status === 'idle') {
          statusText.textContent = 'System Ready';
          scanBtn.disabled = false;
          scanBtn.innerHTML = '<i class="fa-solid fa-radar"></i><span>Scan & Detect Hooks</span>';
          if (currentPollingSpeed !== 2500) setPollingSpeed(2500);
        } else if (data.status === 'timeframes_ready') {
          statusText.textContent = 'Moments Ready (Select to Clip)';
          scanBtn.disabled = false;
          scanBtn.innerHTML = '<i class="fa-solid fa-radar"></i><span>Re-Scan Video</span>';
          if (currentPollingSpeed !== 2500) setPollingSpeed(2500);
        } else if (data.status === 'completed') {
          statusText.textContent = 'Clipping Finished';
          scanBtn.disabled = false;
          scanBtn.innerHTML = '<i class="fa-solid fa-rotate-right"></i><span>Start New Video</span>';
          if (currentPollingSpeed !== 2500) setPollingSpeed(2500);
        } else if (data.status === 'error') {
          statusText.textContent = 'Process Error';
          scanBtn.disabled = false;
          scanBtn.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i><span>Retry Scan</span>';
          if (currentPollingSpeed !== 2500) setPollingSpeed(2500);
        } else {
          statusText.textContent = 'Processing (' + data.status + ')';
          scanBtn.disabled = true;
          scanBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i><span>Processing...</span>';
          if (currentPollingSpeed !== 800) setPollingSpeed(800);
        }

        // Update resume label
        if (data.last_clipped_end_sec && data.last_clipped_end_sec > 0) {
          const h = Math.floor(data.last_clipped_end_sec / 3600);
          const m = Math.floor((data.last_clipped_end_sec % 3600) / 60);
          const s = Math.floor(data.last_clipped_end_sec % 60);
          document.getElementById('resumeTimeLabel').textContent = `${pad(h)}:${pad(m)}:${pad(s)} (Clip #${data.last_clipped_num + 1}+)`;
        }

        document.getElementById('stageMsg').textContent = data.stage_message;
        document.getElementById('progressPct').textContent = data.progress_pct + '%';
        document.getElementById('progressBar').style.width = data.progress_pct + '%';

        // Source Video Player
        const sourcePlayer = document.getElementById('sourcePlayer');
        const sourcePlaceholder = document.getElementById('sourcePlaceholder');
        const sourceDurationBadge = document.getElementById('sourceDurationBadge');

        if (data.source_ready) {
          if (sourcePlayer.classList.contains('hidden')) {
            sourcePlayer.classList.remove('hidden');
            sourcePlaceholder.classList.add('hidden');
            sourcePlayer.src = '/media/source?t=' + Date.now();
            sourcePlayer.load();
            initSourcePlayerListeners();
          }
          if (data.source_duration > 0) {
            sourceDurationBadge.classList.remove('hidden');
            const mins = Math.floor(data.source_duration / 60);
            const secs = Math.floor(data.source_duration % 60);
            sourceDurationBadge.textContent = 'Duration: ' + mins + 'm ' + secs + 's';
          }
        } else {
          sourcePlayer.classList.add('hidden');
          sourcePlaceholder.classList.remove('hidden');
          sourceDurationBadge.classList.add('hidden');

          // Active Download / Ingestion Loading Overlay
          if (data.download_info && data.download_info.active) {
            const info = data.download_info;
            sourcePlaceholder.innerHTML = `
              <div class="flex flex-col items-center space-y-4 p-6 w-full max-w-md animate-fadeIn">
                <div class="relative flex items-center justify-center">
                  <div class="w-16 h-16 rounded-full border-4 border-indigo-500/20 border-t-indigo-500 animate-spin"></div>
                  <i class="fa-solid fa-cloud-arrow-down text-xl text-indigo-400 absolute"></i>
                </div>
                
                <div class="text-center space-y-1">
                  <div class="text-sm font-bold text-white tracking-wide">${escapeHtml(info.status_text || 'Downloading Source Video...')}</div>
                  <div class="text-3xl font-black text-indigo-400 font-mono tracking-tight">${info.percent.toFixed(1)}%</div>
                </div>

                <div class="w-full bg-zinc-800 rounded-full h-3 overflow-hidden border border-zinc-700/80 shadow-inner">
                  <div class="bg-gradient-to-r from-indigo-500 via-violet-500 to-emerald-400 h-3 rounded-full transition-all duration-300" style="width: ${Math.max(3, info.percent)}%"></div>
                </div>

                <div class="flex items-center justify-between w-full text-xs text-zinc-400 font-mono px-1">
                  <span>${escapeHtml(info.size || 'Connecting...')}</span>
                  <span>${escapeHtml(info.speed || '')}</span>
                  <span>${info.eta ? 'ETA ' + escapeHtml(info.eta) : ''}</span>
                </div>
              </div>
            `;
          } else if (data.status === 'transcribing') {
            sourcePlaceholder.innerHTML = `
              <div class="flex flex-col items-center space-y-3 p-6 text-center">
                <div class="w-14 h-14 rounded-full border-4 border-violet-500/20 border-t-violet-400 animate-spin flex items-center justify-center">
                  <i class="fa-solid fa-waveform-lines text-violet-400 text-lg"></i>
                </div>
                <div class="text-sm font-bold text-zinc-100">Transcribing Speech & Syncing Timestamps...</div>
                <div class="text-xs text-zinc-400 max-w-xs">Whisper neural engine running. Source video will stream as soon as audio timestamps finish.</div>
              </div>
            `;
          } else {
            sourcePlaceholder.innerHTML = `
              <div class="text-center p-6 text-zinc-500 flex flex-col items-center space-y-3">
                <i class="fa-regular fa-circle-play text-4xl text-zinc-600"></i>
                <div class="text-xs max-w-xs">Source video will stream here as soon as download completes. Scrub or click any detected moment to preview.</div>
              </div>
            `;
          }
        }

        // Delete Source Video Button: COMPLETELY HIDDEN during active processing
        const delBtn = document.getElementById('deleteSourceBtn');
        const isProcessing = ['downloading', 'transcribing', 'detecting', 'clipping'].includes(data.status);
        if (isProcessing || !data.can_delete_source || !data.source_ready) {
          delBtn.classList.add('hidden');
        } else {
          delBtn.classList.remove('hidden');
        }

        // Terminal Logs
        const term = document.getElementById('terminalWindow');
        if (data.logs && data.logs.length !== lastLogCount) {
          term.innerHTML = data.logs.map(line => {
            let color = 'text-zinc-300';
            if (line.includes('✓')) color = 'text-emerald-400 font-semibold';
            if (line.includes('❌') || line.includes('Error')) color = 'text-red-400 font-semibold';
            if (line.includes('🚀') || line.includes('🎉') || line.includes('🔍') || line.includes('🎬')) color = 'text-indigo-400 font-bold';
            if (line.includes('Stage') || line.includes('---')) color = 'text-violet-300';
            return '<div class="' + color + '">' + escapeHtml(line) + '</div>';
          }).join('');
          term.scrollTop = term.scrollHeight;
          lastLogCount = data.logs.length;
        }

        renderTimeframes(data.timeframes);
        renderDraftClips(data.draft_clips);
        renderApprovedClips(data.approved_clips);

      } catch (err) {
        console.error('Polling error:', err);
      }
    }

    function renderTimeframes(tfs) {
      if (!tfs) return;
      const fingerprint = tfs.map(tf => `${tf.id}_${tf.start}_${tf.end}_${tf.title}`).join('|');
      if (fingerprint === lastTimeframesFingerprint) {
        return;
      }
      lastTimeframesFingerprint = fingerprint;
      loadedTimeframes = tfs.map(tf => ({ ...tf, selected: tf.selected !== undefined ? tf.selected : true }));

      const container = document.getElementById('timeframesContainer');
      if (loadedTimeframes.length === 0) {
        container.innerHTML = `
          <div class="text-xs text-zinc-500 py-8 text-center">
            Click <b>"Scan & Detect Hooks"</b> above to analyze the video and isolate hot moments.
          </div>`;
        updateSelectedCount();
        return;
      }

      container.innerHTML = loadedTimeframes.map((tf, index) => {
        const startMin = Math.floor(tf.start / 60);
        const startSec = Math.floor(tf.start % 60);
        const endMin = Math.floor(tf.end / 60);
        const endSec = Math.floor(tf.end % 60);
        const timeLabel = pad(startMin) + ':' + pad(startSec) + ' - ' + pad(endMin) + ':' + pad(endSec);

        let intensityColor = "bg-amber-950 text-amber-300 border-amber-800";
        if ((tf.intensity || '').includes('Extreme')) intensityColor = "bg-red-950 text-red-300 border-red-800";
        if ((tf.intensity || '').includes('Drama')) intensityColor = "bg-violet-950 text-violet-300 border-violet-800";
        if ((tf.intensity || '').includes('Intimate') || (tf.intensity || '').includes('Romance') || (tf.intensity || '').includes('Kiss') || (tf.intensity || '').includes('Rizz')) intensityColor = "bg-rose-950 text-rose-300 border-rose-800";

        const checkedAttr = tf.selected ? 'checked' : '';

        return `
          <div id="tf_card_${index}" class="group p-3.5 rounded-xl bg-zinc-950/80 border border-zinc-800 hover:border-indigo-500/80 transition-all flex flex-col space-y-2.5">
            <div class="flex items-center justify-between">
              <div class="flex items-center space-x-2.5">
                <input type="checkbox" id="tf_check_${index}" ${checkedAttr} onchange="toggleTimeframe(${index})" 
                  class="w-4 h-4 rounded text-indigo-600 focus:ring-indigo-500 bg-zinc-900 border-zinc-700 cursor-pointer">
                <span class="text-xs font-bold text-white">#${tf.id}</span>
                <span class="text-[11px] font-mono font-semibold text-zinc-300 bg-zinc-900 px-2 py-0.5 rounded border border-zinc-800">${timeLabel} (${tf.duration}s)</span>
                <span class="text-[10px] px-2 py-0.5 rounded-full border ${intensityColor} font-bold">${tf.intensity || 'Hot Moment 🔥'}</span>
                <span id="tf_status_badge_${index}" class="hidden text-[10px] px-2.5 py-0.5 rounded-full bg-indigo-600 text-white font-bold flex items-center shadow-lg shadow-indigo-600/30">
                  <span class="w-1.5 h-1.5 rounded-full bg-white mr-1.5 animate-ping"></span>Playing
                </span>
              </div>
              <div class="flex items-center space-x-1.5">
                <button onclick="regenerateTimeframeMeta(${index})" 
                  class="text-[11px] px-2 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-indigo-400 font-medium transition-all" title="Regenerate viral title & hashtags">
                  <i class="fa-solid fa-arrows-rotate mr-1"></i>Regen
                </button>
                <button id="tf_preview_btn_${index}" onclick="previewTimeframe(${index}, ${tf.start}, ${tf.end})" 
                  class="text-xs px-2.5 py-1 rounded-md bg-zinc-800 hover:bg-indigo-600 text-zinc-200 hover:text-white transition-all flex items-center space-x-1 shadow" title="Preview this moment in source player (auto-stops at end)">
                  <i class="fa-solid fa-play text-[10px] mr-1"></i>
                  <span>Preview</span>
                </button>
              </div>
            </div>

            <!-- Mini Live Progress Bar (Active when playing) -->
            <div class="hidden w-full bg-zinc-900/90 rounded-full h-1 overflow-hidden">
              <div id="tf_progress_fill_${index}" class="bg-gradient-to-r from-indigo-500 to-teal-400 h-full w-0 transition-all duration-150"></div>
            </div>

            <!-- Dynamic Viral Title & Niche Hashtags Preview -->
            <div class="pl-6.5 space-y-1">
              <div id="tf_title_${index}" class="text-xs font-bold text-indigo-300 hover:text-indigo-200 cursor-pointer" onclick="previewTimeframe(${index}, ${tf.start}, ${tf.end})" title="Click to play this moment">
                ${escapeHtml(tf.title || tf.hook)}
              </div>
              <div id="tf_tags_${index}" class="text-[11px] font-mono text-zinc-400">
                ${escapeHtml(tf.hashtags || '')}
              </div>
              <div class="text-[11px] text-zinc-500 italic truncate">
                "${escapeHtml(tf.hook)}"
              </div>
            </div>
          </div>
        `;
      }).join('');

      updateSelectedCount();
    }

    async function regenerateTimeframeMeta(index) {
      const tf = loadedTimeframes[index];
      if (!tf) return;

      tf.variation = (tf.variation || 0) + 1;
      try {
        const res = await fetch('/api/regenerate-metadata', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: tf.full_text || tf.hook, hook: tf.hook, id: tf.id, variation: tf.variation })
        });
        const data = await res.json();
        if (res.ok) {
          tf.title = data.title;
          tf.hashtags = data.hashtags;
          document.getElementById('tf_title_' + index).textContent = data.title;
          document.getElementById('tf_tags_' + index).textContent = data.hashtags;
        }
      } catch (e) {
        console.error(e);
      }
    }

    async function regenerateDraftMeta(clipId) {
      const titleInput = document.getElementById('title_' + clipId);
      const tagsInput = document.getElementById('tags_' + clipId);
      const draft = loadedDrafts.find(d => d.id === clipId);
      if (!draft) return;

      draft.variation = (draft.variation || 0) + 1;
      try {
        const res = await fetch('/api/regenerate-metadata', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: draft.full_text || draft.hook, hook: draft.hook, id: draft.id, variation: draft.variation })
        });
        const data = await res.json();
        if (res.ok) {
          if (titleInput) titleInput.value = data.title;
          if (tagsInput) tagsInput.value = data.hashtags;
          draft.title = data.title;
          draft.hashtags = data.hashtags;
        }
      } catch (e) {
        console.error(e);
      }
    }

    function toggleTimeframe(index) {
      const chk = document.getElementById('tf_check_' + index);
      if (chk && loadedTimeframes[index]) {
        loadedTimeframes[index].selected = chk.checked;
        updateSelectedCount();
      }
    }

    function selectAllTimeframes(selectVal) {
      loadedTimeframes.forEach((tf, index) => {
        tf.selected = selectVal;
        const chk = document.getElementById('tf_check_' + index);
        if (chk) chk.checked = selectVal;
      });
      updateSelectedCount();
    }

    function updateSelectedCount() {
      const selected = loadedTimeframes.filter(tf => tf.selected);
      const countBadge = document.getElementById('selectedCountBadge');
      const renderBtn = document.getElementById('renderSelectedBtn');
      const btnText = document.getElementById('renderBtnText');

      countBadge.textContent = selected.length;

      if (selected.length > 0) {
        renderBtn.disabled = false;
        renderBtn.classList.remove('opacity-40', 'cursor-not-allowed');
        btnText.textContent = `Render Selected (${selected.length} Clips)`;
      } else {
        renderBtn.disabled = true;
        renderBtn.classList.add('opacity-40', 'cursor-not-allowed');
        btnText.textContent = 'Select Moments Above To Render';
      }
    }

    // Timeframe Preview & Auto-Stop Controller
    let activePreviewIndex = -1;
    let activePreviewStart = null;
    let activePreviewEnd = null;

    function initSourcePlayerListeners() {
      const player = document.getElementById('sourcePlayer');
      if (!player || player._listenersAttached) return;
      player._listenersAttached = true;

      player.addEventListener('timeupdate', () => {
        const curTime = player.currentTime;

        // Auto-stop if playing an explicit timeframe and reached its end timestamp
        if (activePreviewIndex !== -1 && activePreviewEnd !== null) {
          if (curTime >= activePreviewEnd) {
            player.pause();
            const endedIndex = activePreviewIndex;
            activePreviewIndex = -1;
            activePreviewStart = null;
            activePreviewEnd = null;
            updateTimeframePlayingStatus();

            // Flash card green to confirm completed preview
            const card = document.getElementById('tf_card_' + endedIndex);
            if (card) {
              card.classList.add('border-emerald-500', 'ring-1', 'ring-emerald-500/50');
              setTimeout(() => {
                card.classList.remove('border-emerald-500', 'ring-1', 'ring-emerald-500/50');
              }, 1200);
            }
            return;
          }
        }

        updateTimeframePlayingStatus();
      });

      player.addEventListener('pause', () => {
        updateTimeframePlayingStatus();
      });

      player.addEventListener('play', () => {
        updateTimeframePlayingStatus();
      });

      player.addEventListener('ended', () => {
        activePreviewIndex = -1;
        activePreviewStart = null;
        activePreviewEnd = null;
        updateTimeframePlayingStatus();
      });
    }

    function previewTimeframe(index, startSec, endSec) {
      const player = document.getElementById('sourcePlayer');
      if (!player) return;

      initSourcePlayerListeners();

      // If already playing this timeframe and not paused, toggle pause
      if (activePreviewIndex === index && !player.paused) {
        player.pause();
        updateTimeframePlayingStatus();
        return;
      }

      // If paused at the end of this timeframe, restart from beginning
      activePreviewIndex = index;
      activePreviewStart = startSec;
      activePreviewEnd = endSec;

      if (player.currentTime >= endSec - 0.2 || player.currentTime < startSec - 0.5 || player.currentTime > endSec) {
        player.currentTime = startSec;
      }

      player.play().catch(e => console.log('Autoplay error:', e));
      updateTimeframePlayingStatus();

      // Scroll player into view smoothly
      player.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    function updateTimeframePlayingStatus() {
      const player = document.getElementById('sourcePlayer');
      const isPlaying = player && !player.paused;
      const curTime = player ? player.currentTime : 0;

      // Identify which timeframe is playing
      let currentIndex = activePreviewIndex;
      if (currentIndex === -1 && isPlaying && loadedTimeframes.length > 0) {
        // Fallback: check if curTime falls within any loaded timeframe
        currentIndex = loadedTimeframes.findIndex(tf => curTime >= tf.start && curTime <= tf.end);
      }

      loadedTimeframes.forEach((tf, idx) => {
        const card = document.getElementById('tf_card_' + idx);
        const badge = document.getElementById('tf_status_badge_' + idx);
        const btn = document.getElementById('tf_preview_btn_' + idx);
        const progressFill = document.getElementById('tf_progress_fill_' + idx);
        if (!card) return;

        const isThisPlaying = (idx === currentIndex) && isPlaying;

        if (isThisPlaying) {
          card.classList.add('border-indigo-500', 'shadow-lg', 'shadow-indigo-500/20', 'bg-indigo-950/30', 'ring-1', 'ring-indigo-500/50');
          card.classList.remove('border-zinc-800', 'bg-zinc-950/80');

          if (badge) {
            badge.classList.remove('hidden');
            const remaining = Math.max(0, tf.end - curTime).toFixed(1);
            badge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-white mr-1.5 animate-ping"></span>Playing (${remaining}s left)`;
          }

          if (btn) {
            btn.innerHTML = `<i class="fa-solid fa-pause text-[10px] mr-1"></i><span>Pause</span>`;
            btn.classList.add('bg-indigo-600', 'text-white');
            btn.classList.remove('bg-zinc-800', 'text-zinc-200');
          }

          if (progressFill) {
            const pct = Math.min(100, Math.max(0, ((curTime - tf.start) / Math.max(0.1, tf.end - tf.start)) * 100));
            progressFill.style.width = pct + '%';
            if (progressFill.parentElement) progressFill.parentElement.classList.remove('hidden');
          }
        } else {
          card.classList.remove('border-indigo-500', 'shadow-lg', 'shadow-indigo-500/20', 'bg-indigo-950/30', 'ring-1', 'ring-indigo-500/50');
          card.classList.add('border-zinc-800', 'bg-zinc-950/80');

          if (badge) {
            badge.classList.add('hidden');
          }

          if (btn) {
            btn.innerHTML = `<i class="fa-solid fa-play text-[10px] mr-1"></i><span>Preview</span>`;
            btn.classList.remove('bg-indigo-600', 'text-white');
            btn.classList.add('bg-zinc-800', 'text-zinc-200');
          }

          if (progressFill && progressFill.parentElement) {
            progressFill.parentElement.classList.add('hidden');
            progressFill.style.width = '0%';
          }
        }
      });
    }

    function seekSourceVideo(seconds) {
      const player = document.getElementById('sourcePlayer');
      if (player) {
        initSourcePlayerListeners();
        player.currentTime = seconds;
        player.play().catch(e => console.log(e));
        player.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    }

    function setCustomFromPlayer() {
      const player = document.getElementById('sourcePlayer');
      if (player && player.currentTime) {
        const cur = Math.round(player.currentTime * 10) / 10;
        document.getElementById('customStart').value = cur;
        document.getElementById('customEnd').value = Math.round((cur + 35) * 10) / 10;
      }
    }

    function addCustomTimeframe() {
      const startSec = parseFloat(document.getElementById('customStart').value) || 0.0;
      const endSec = parseFloat(document.getElementById('customEnd').value) || (startSec + 30.0);

      if (endSec <= startSec) {
        alert('End time must be greater than start time.');
        return;
      }

      const dur = Math.round((endSec - startSec) * 10) / 10;
      const newId = loadedTimeframes.length + 1;

      const newTf = {
        id: newId,
        start: startSec,
        end: endSec,
        duration: dur,
        hook: `Custom User Segment [${startSec}s - ${endSec}s]`,
        intensity: "User Selected 🎯",
        score: 100.0,
        title: `Custom Epic Moment #${newId} 🔥`,
        hashtags: "#Shorts #Viral #Gaming #Trending #Clips",
        variation: 0,
        selected: true
      };

      loadedTimeframes.push(newTf);
      lastTimeframesFingerprint = "";
      renderTimeframes(loadedTimeframes);
      alert(`Added Custom Moment #${newId} (${dur}s) to selection list!`);
    }

    function renderDraftClips(drafts) {
      const list = drafts || [];
      const fingerprint = list.map(d => `${d.id}_${d.approved}_${d.status}_${d.render_pct}_${d.render_stage}_${d.filename}_${d.size_mb}_${d.title}`).join('|');
      if (fingerprint === lastDraftsFingerprint) {
        return;
      }
      lastDraftsFingerprint = fingerprint;
      loadedDrafts = list.map(d => ({ ...d, checkedForBulk: !d.approved && (d.status === 'ready' || (!d.status && d.size_mb > 0)) }));

      const container = document.getElementById('draftClipsGrid');
      const badge = document.getElementById('draftCountBadge');
      badge.textContent = list.length + ' Drafts';

      if (list.length === 0) {
        container.innerHTML = `
          <div class="col-span-full py-12 text-center text-zinc-500 text-xs">
            No draft clips rendered yet. Scan a video above, select your favorite moments, and click <b>Render Selected Clips</b>.
          </div>`;
        updateBulkApproveBtn();
        return;
      }

      container.innerHTML = list.map((d, dIdx) => {
        const isReady = (d.status === 'ready' || (!d.status && d.size_mb > 0));
        const isRendering = (d.status === 'rendering');
        const isQueued = (d.status === 'queued');

        let approvedBadge = '';
        if (d.approved) {
          approvedBadge = `<span class="bg-emerald-950 border border-emerald-700 text-emerald-300 text-xs px-2.5 py-0.5 rounded-full font-bold">Approved ✓</span>`;
        } else if (isRendering) {
          approvedBadge = `<span class="bg-indigo-950 border border-indigo-700 text-indigo-300 text-xs px-2.5 py-0.5 rounded-full font-bold flex items-center shadow"><span class="w-1.5 h-1.5 rounded-full bg-indigo-400 mr-1.5 animate-ping"></span>Rendering ${d.render_pct || 0}%</span>`;
        } else if (isQueued) {
          approvedBadge = `<span class="bg-zinc-900 border border-zinc-800 text-zinc-400 text-xs px-2.5 py-0.5 rounded-full font-semibold">Queued ⏳</span>`;
        } else {
          approvedBadge = `<span class="bg-amber-950 border border-amber-700 text-amber-300 text-xs px-2.5 py-0.5 rounded-full font-semibold">Pending Review</span>`;
        }

        let approveBtn = '';
        if (d.approved) {
          approveBtn = `<button disabled class="w-full bg-zinc-800 text-zinc-500 text-xs py-2.5 rounded-xl font-semibold cursor-not-allowed">Already Approved</button>`;
        } else if (isRendering) {
          approveBtn = `<button disabled class="w-full bg-indigo-950/60 border border-indigo-800/80 text-indigo-300 text-xs py-2.5 rounded-xl font-semibold cursor-not-allowed flex items-center justify-center space-x-2">
            <i class="fa-solid fa-spinner fa-spin mr-1"></i>
            <span>Rendering (${d.render_pct || 0}%)...</span>
          </button>`;
        } else if (isQueued) {
          approveBtn = `<button disabled class="w-full bg-zinc-900 border border-zinc-800 text-zinc-500 text-xs py-2.5 rounded-xl font-semibold cursor-not-allowed flex items-center justify-center space-x-2">
            <i class="fa-regular fa-clock mr-1"></i>
            <span>Queued in Pipeline</span>
          </button>`;
        } else {
          approveBtn = `<button onclick="approveDraftClip(${d.id})" class="w-full bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white text-xs py-2.5 rounded-xl font-bold shadow-lg shadow-emerald-600/30 flex items-center justify-center space-x-2 transition-all active:scale-[0.98]">
            <i class="fa-solid fa-check"></i>
            <span>Approve & Move to Final</span>
          </button>`;
        }

        const checkAttr = (isReady && !d.approved && d.checkedForBulk) ? 'checked' : '';
        const disabledCheck = (!isReady || d.approved) ? 'disabled' : '';

        // Video container: placeholder loader overlay if rendering/queued, or real video if ready
        let videoBoxHtml = '';
        if (isRendering) {
          videoBoxHtml = `
            <div class="relative w-full aspect-[9/16] max-h-[380px] bg-zinc-950 rounded-xl overflow-hidden border border-indigo-500/50 shadow-lg shadow-indigo-500/20 flex flex-col items-center justify-center p-5 text-center">
              <div class="relative flex items-center justify-center mb-4">
                <div class="w-16 h-16 rounded-full border-4 border-indigo-500/20 border-t-indigo-500 animate-spin"></div>
                <i class="fa-solid fa-wand-magic-sparkles text-xl text-indigo-400 absolute animate-pulse"></i>
              </div>
              <div class="text-xs font-bold text-white mb-1 tracking-wide">Rendering Clip #${d.id}</div>
              <div class="text-[11px] text-indigo-300 font-medium mb-4 px-2 leading-tight">${escapeHtml(d.render_stage || 'Processing 1080x1920 60 FPS...')}</div>
              <div class="w-full bg-zinc-900 rounded-full h-2.5 overflow-hidden border border-zinc-800 mb-2 shadow-inner">
                <div class="bg-gradient-to-r from-indigo-500 via-violet-500 to-emerald-400 h-2.5 rounded-full transition-all duration-300" style="width: ${d.render_pct || 20}%"></div>
              </div>
              <div class="flex items-center justify-between w-full text-[10px] text-zinc-400 font-mono px-0.5">
                <span>1080x1920 60 FPS</span>
                <span class="font-bold text-indigo-300 text-xs">${d.render_pct || 20}%</span>
              </div>
            </div>
          `;
        } else if (isQueued) {
          videoBoxHtml = `
            <div class="relative w-full aspect-[9/16] max-h-[380px] bg-zinc-950/90 rounded-xl overflow-hidden border border-zinc-800/80 flex flex-col items-center justify-center p-5 text-center text-zinc-500">
              <div class="w-14 h-14 rounded-full bg-zinc-900/90 border border-zinc-800 flex items-center justify-center mb-3 text-zinc-500 shadow">
                <i class="fa-regular fa-clock text-xl"></i>
              </div>
              <div class="text-xs font-bold text-zinc-300 mb-1">Queued for Rendering</div>
              <div class="text-[10px] text-zinc-500 max-w-[190px] leading-relaxed">Will start automatically once previous clip finishes.</div>
            </div>
          `;
        } else {
          videoBoxHtml = `
            <div class="relative w-full aspect-[9/16] max-h-[380px] bg-black rounded-xl overflow-hidden border border-zinc-800 flex items-center justify-center">
              <video controls playsinline ${d.poster ? `poster="${d.poster}"` : ''} class="w-full h-full object-contain" preload="metadata">
                <source src="/media/draft/${d.filename}#t=0.001" type="video/mp4">
              </video>
            </div>
          `;
        }

        const sizeLabel = (d.size_mb && d.size_mb > 0) ? ` • ${d.size_mb} MB` : '';

        return `
          <div class="bg-zinc-950 border border-zinc-800 rounded-2xl p-4 shadow-xl flex flex-col space-y-3.5 ${isRendering ? 'ring-1 ring-indigo-500/50 border-indigo-500/40' : ''}">
            <div class="flex items-center justify-between">
              <div class="flex items-center space-x-2">
                <input type="checkbox" id="draft_chk_${d.id}" ${checkAttr} ${disabledCheck} onchange="toggleDraftCheck(${d.id})"
                  class="w-4 h-4 rounded text-emerald-600 focus:ring-emerald-500 bg-zinc-900 border-zinc-700 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed">
                <span class="text-xs font-bold text-zinc-200">Clip #${d.id} • ${d.duration.toFixed(1)}s${sizeLabel}</span>
              </div>
              ${approvedBadge}
            </div>

            ${videoBoxHtml}

            <div class="text-[11px] text-zinc-400 space-y-1">
              <div><b class="text-zinc-300">Hook:</b> "${escapeHtml(d.hook)}"</div>
              <div><b class="text-zinc-300">Framing:</b> ${d.framing}</div>
              <div><b class="text-zinc-300">Timestamps:</b> ${d.start.toFixed(1)}s - ${d.end.toFixed(1)}s</div>
            </div>

            <div class="space-y-2 pt-1 border-t border-zinc-900">
              <div class="flex items-center justify-between">
                <label class="text-[10px] font-semibold text-zinc-400 uppercase">Viral Title</label>
                <button onclick="regenerateDraftMeta(${d.id})" class="text-[10px] text-indigo-400 hover:text-indigo-300 font-medium">
                  <i class="fa-solid fa-arrows-rotate mr-1"></i>Regen
                </button>
              </div>
              <input type="text" id="title_${d.id}" value="${escapeHtml(d.title)}" 
                class="w-full mt-0.5 bg-zinc-900 border border-zinc-800 focus:border-indigo-500 rounded-lg px-2.5 py-1.5 text-xs text-white">

              <div>
                <label class="text-[10px] font-semibold text-zinc-400 uppercase">5 Niche Hashtags</label>
                <input type="text" id="tags_${d.id}" value="${escapeHtml(d.hashtags)}" 
                  class="w-full mt-0.5 bg-zinc-900 border border-zinc-800 focus:border-indigo-500 rounded-lg px-2.5 py-1.5 text-xs text-zinc-300 font-mono">
              </div>
            </div>

            <div class="pt-1">
              ${approveBtn}
            </div>
          </div>
        `;
      }).join('');

      updateBulkApproveBtn();
    }

    function toggleDraftCheck(clipId) {
      const chk = document.getElementById('draft_chk_' + clipId);
      const draft = loadedDrafts.find(d => d.id === clipId);
      if (chk && draft) {
        draft.checkedForBulk = chk.checked;
        updateBulkApproveBtn();
      }
    }

    function toggleAllDrafts(selectAll) {
      loadedDrafts.forEach(d => {
        const isReady = (d.status === 'ready' || (!d.status && d.size_mb > 0));
        if (!d.approved && isReady) {
          d.checkedForBulk = selectAll;
          const chk = document.getElementById('draft_chk_' + d.id);
          if (chk) chk.checked = selectAll;
        }
      });
      updateBulkApproveBtn();
    }

    function updateBulkApproveBtn() {
      const pendingAndChecked = loadedDrafts.filter(d => (d.status === 'ready' || (!d.status && d.size_mb > 0)) && !d.approved && d.checkedForBulk);
      const btn = document.getElementById('bulkApproveBtn');
      const text = document.getElementById('bulkApproveText');
      if (!btn || !text) return;

      if (pendingAndChecked.length > 0) {
        btn.disabled = false;
        btn.classList.remove('opacity-40', 'cursor-not-allowed');
        text.textContent = `Approve Selected (${pendingAndChecked.length})`;
      } else {
        btn.disabled = true;
        btn.classList.add('opacity-40', 'cursor-not-allowed');
        text.textContent = 'Approve Selected';
      }
    }

    async function bulkApproveDrafts() {
      const toApprove = loadedDrafts.filter(d => (d.status === 'ready' || (!d.status && d.size_mb > 0)) && !d.approved && d.checkedForBulk);
      if (toApprove.length === 0) return;
      if (toApprove.length === 0) return;

      const clipIds = toApprove.map(d => d.id);
      const metadataMap = {};

      clipIds.forEach(cid => {
        const titleInput = document.getElementById('title_' + cid);
        const tagsInput = document.getElementById('tags_' + cid);
        metadataMap[cid] = {
          title: titleInput ? titleInput.value : '',
          hashtags: tagsInput ? tagsInput.value : '',
        };
      });

      const btn = document.getElementById('bulkApproveBtn');
      btn.disabled = true;
      btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i><span>Approving...</span>';

      try {
        const res = await fetch('/api/bulk-approve', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ clip_ids: clipIds, metadata: metadataMap })
        });
        const data = await res.json();
        if (res.ok) {
          pollStatus();
        } else {
          alert(data.detail || 'Bulk approval failed.');
        }
      } catch (err) {
        alert('Error in bulk approval: ' + err.message);
      }
    }

    function renderApprovedClips(approved) {
      function getClipNum(item) {
        if (item.clip_num !== undefined) return item.clip_num;
        const m = (item.filename || item.id || '').match(/clip_(\d+)/);
        return m ? parseInt(m[1], 10) : 999999;
      }

      const list = (approved || []).slice().sort((a, b) => getClipNum(a) - getClipNum(b));
      const fingerprint = list.map(a => `${a.id}_${a.filename}_${a.size_mb}_${a.title}_${a.hashtags}_${a.poster}`).join('|');
      if (fingerprint === lastApprovedFingerprint) {
        return;
      }
      lastApprovedFingerprint = fingerprint;

      const container = document.getElementById('approvedClipsGrid');
      const badge = document.getElementById('approvedCountBadge');
      badge.textContent = list.length + ' Approved';

      if (list.length === 0) {
        container.innerHTML = `
          <div class="col-span-full py-8 text-center text-zinc-500 text-xs">
            No approved clips yet. Approve draft clips above to move them here.
          </div>`;
        return;
      }

      container.innerHTML = list.map(a => {
        const posterAttr = a.poster ? `poster="${a.poster}"` : '';
        return `
          <div class="bg-zinc-950 border border-emerald-950/60 rounded-2xl p-4 shadow-xl flex flex-col space-y-3.5">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-emerald-300">${a.filename} (${a.size_mb} MB)</span>
              <span class="text-[10px] bg-emerald-950 text-emerald-400 px-2 py-0.5 rounded border border-emerald-800 font-semibold">Final Clip</span>
            </div>

            <div class="relative w-full aspect-[9/16] max-h-[380px] bg-black rounded-xl overflow-hidden border border-zinc-800 flex items-center justify-center">
              <video controls playsinline ${posterAttr} class="w-full h-full object-contain" preload="metadata">
                <source src="/media/final/${a.filename}#t=0.001" type="video/mp4">
              </video>
            </div>

            <div class="bg-zinc-900/90 border border-zinc-800/80 rounded-xl p-3 text-xs space-y-1.5">
              <div class="font-bold text-zinc-100 text-sm leading-tight">${escapeHtml(a.title)}</div>
              <div class="text-indigo-400 font-mono text-[11px] font-medium leading-relaxed">${escapeHtml(a.hashtags)}</div>
              ${a.hook ? `<div class="text-[11px] text-zinc-300 italic pt-1 border-t border-zinc-800"><b class="text-zinc-400 not-italic">Hook:</b> "${escapeHtml(a.hook)}"</div>` : ''}
              <div class="text-[10px] text-zinc-400 font-mono pt-1 ${a.hook ? '' : 'border-t border-zinc-800'}">${escapeHtml(a.timestamps || '1080x1920 60 FPS')}</div>
            </div>

            <div class="flex space-x-2 pt-1">
              <a href="/media/final/${a.filename}" download="${a.filename}" 
                class="flex-1 text-center bg-zinc-800 hover:bg-zinc-700 text-white text-xs py-2 rounded-lg font-semibold transition-all">
                <i class="fa-solid fa-download mr-1"></i> Download MP4
              </a>
              <button onclick="copyMetadata('${escapeHtml(a.title)}', '${escapeHtml(a.hashtags)}')"
                class="px-3 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs py-2 rounded-lg transition-all" title="Copy Title & Tags">
                <i class="fa-regular fa-copy"></i>
              </button>
            </div>
          </div>
        `;
      }).join('');
    }

    async function approveDraftClip(clipId) {
      const titleInput = document.getElementById('title_' + clipId);
      const tagsInput = document.getElementById('tags_' + clipId);

      const title = titleInput ? titleInput.value : '';
      const hashtags = tagsInput ? tagsInput.value : '';

      try {
        const res = await fetch('/api/approve/' + clipId, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: title, hashtags: hashtags })
        });
        const data = await res.json();
        if (res.ok) {
          pollStatus();
        } else {
          alert(data.detail || 'Failed to approve clip.');
        }
      } catch (err) {
        alert('Error approving clip: ' + err.message);
      }
    }

    async function deleteSourceVideo() {
      if (!confirm('Are you sure you want to delete the source video file from temp_work/? (All draft and approved clips will be kept safely).')) {
        return;
      }
      try {
        const res = await fetch('/api/delete-source', { method: 'POST' });
        const data = await res.json();
        alert(data.message);
        pollStatus();
      } catch (err) {
        alert('Error deleting source video: ' + err.message);
      }
    }

    function copyMetadata(title, tags) {
      const text = title + '\\n\\n' + tags;
      navigator.clipboard.writeText(text);
      alert('Copied Title & Hashtags to clipboard!');
    }

    function pad(n) { return n < 10 ? '0' + n : n; }
    function escapeHtml(str) {
      if (!str) return '';
      return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    initSourcePlayerListeners();
    startPolling();
  </script>
</body>
</html>
"""

def main():
    candidate_ports = [8888, 7860, 9090, 8080]
    httpd = None
    chosen_port = 8888
    for p in candidate_ports:
        try:
            server_address = ("127.0.0.1", p)
            httpd = http.server.ThreadingHTTPServer(server_address, StudioRequestHandler)
            chosen_port = p
            break
        except Exception:
            continue

    if not httpd:
        print("[ERROR] Could not bind to any candidate port.")
        sys.exit(1)

    print("=" * 70)
    print("🌐 UNIVERSAL CLIPPER STUDIO WEB UI")
    print("=" * 70)
    print(f"Server running at: http://localhost:{chosen_port}  (or http://127.0.0.1:{chosen_port})")
    print("Press Ctrl+C to stop.")
    print("=" * 70)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        httpd.server_close()

if __name__ == "__main__":
    main()
