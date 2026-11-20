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
    from generate_metadata import generate_clip_metadata, generate_clip_metadata_full, generate_seo_description
except ImportError:
    def generate_clip_metadata(text, hook, idx=1, variation=0, video_title=""):
        return f"Viral Moment #{idx} 🤯", "#Shorts #Viral #Trending #HighStakes #FYP"
    def generate_clip_metadata_full(text, hook, idx=1, variation=0, video_title="", timestamps=""):
        return {
            "title": f"Viral Moment #{idx} 🤯",
            "hashtags": "#Shorts #Viral #Trending",
            "description": f"Watch this viral moment!\n\n#Shorts #Viral",
            "first_200": "Watch this viral moment!",
            "keywords": ["shorts", "viral", "trending"]
        }

try:
    from detect_hooks import calculate_intensity_score
except ImportError:
    def calculate_intensity_score(text, duration, words):
        return 85.0, "Custom Moment 🎯"

try:
    import youtube_uploader
except ImportError:
    youtube_uploader = None

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
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        with getattr(self, "lock", threading.RLock()):
            self.status = "idle"  # idle, downloading, transcribing, detecting, timeframes_ready, clipping, completed, error
            self.progress_pct = 0
            self.stage_message = "Ready to start"
            self.logs = []
            self.source_ready = False
            self.source_duration = 0.0
            self.source_filename = ""
            self.source_title = ""
            self.timeframes = []
            self.draft_clips = []
            self.approved_clips = []
            self.error_message = ""
            self.can_delete_source = False
            self.current_url = ""
            self.target_clips = 3
            self.subtitle_theme = "hormozi"
            self.subtitle_position = "bottom"
            self.subtitle_font = "Arial Black"
            self.subtitle_bold = True
            self.subtitle_italic = False
            self.subtitle_uppercase = True
            self.subtitle_format = "karaoke"
            self.last_clipped_num = 0
            self.last_clipped_end_sec = 0.0
            self.youtube_uploads = {}
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
                "source_title": getattr(self, "source_title", ""),
                "timeframes": self.timeframes,
                "draft_clips": self.draft_clips,
                "approved_clips": self.approved_clips,
                "error_message": self.error_message,
                "can_delete_source": self.can_delete_source,
                "current_url": self.current_url,
                "target_clips": self.target_clips,
                "subtitle_theme": getattr(self, "subtitle_theme", "hormozi"),
                "subtitle_position": getattr(self, "subtitle_position", "bottom"),
                "subtitle_font": getattr(self, "subtitle_font", "Arial Black"),
                "subtitle_bold": getattr(self, "subtitle_bold", True),
                "subtitle_italic": getattr(self, "subtitle_italic", False),
                "subtitle_uppercase": getattr(self, "subtitle_uppercase", True),
                "subtitle_format": getattr(self, "subtitle_format", "karaoke"),
                "download_info": dict(self.download_info),
                "youtube_uploads": dict(getattr(self, "youtube_uploads", {})),
                "last_clipped_num": max_num,
                "last_clipped_end_sec": max_end,
            }

state = AppState()
if (TEMP_DIR / "source_title.txt").exists():
    try:
        state.source_title = (TEMP_DIR / "source_title.txt").read_text(encoding="utf-8").strip()
    except Exception:
        pass

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

def save_clip_metadata_file(
    txt_path: Path,
    clip_num,
    filename: str,
    timestamps: str,
    hook: str,
    framing: str,
    title: str,
    hashtags: str,
    description: str,
    keywords
):
    """Writes standardized metadata with SEO-optimized description & keywords to disk."""
    kw_str = ", ".join(keywords) if isinstance(keywords, list) else str(keywords or "")
    content = (
        f"==================================================\n"
        f"APPROVED VIRAL CLIP #{clip_num}\n"
        f"==================================================\n"
        f"File: {filename}\n"
        f"Timestamps: {timestamps}\n"
        f"Opening Hook Line: \"{hook}\"\n"
        f"Framing: {framing}\n"
        f"Viral Title: {title}\n"
        f"Hashtags: {hashtags}\n"
        f"Keywords: {kw_str}\n\n"
        f"Description:\n"
        f"{description}\n"
        f"==================================================\n"
    )
    txt_path.write_text(content, encoding="utf-8")


def analyze_custom_timeframe(start_sec: float, end_sec: float) -> dict:
    dur = round(end_sec - start_sec, 1)
    transcript_path = TEMP_DIR / "transcript.json"

    full_text = ""
    words_in_range = []

    if transcript_path.exists():
        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                tdata = json.load(f)
            segs = tdata.get("segments", []) if isinstance(tdata, dict) else (tdata if isinstance(tdata, list) else [])

            matched_texts = []
            for s in segs:
                s_start = float(s.get("start", 0.0))
                s_end = float(s.get("end", 0.0))
                # Check overlap with [start_sec, end_sec]
                if s_end >= start_sec and s_start <= end_sec:
                    txt = s.get("text", "").strip()
                    if txt:
                        matched_texts.append(txt)
                    for w in s.get("words", []):
                        w_start = float(w.get("start", 0.0))
                        w_end = float(w.get("end", 0.0))
                        if w_end >= start_sec and w_start <= end_sec:
                            words_in_range.append(w)

            if matched_texts:
                full_text = " ".join(matched_texts).strip()
        except Exception as err:
            state.log(f"Warning reading transcript for custom timeframe: {err}")

    # Determine hook line
    if full_text:
        # Split sentences and find first punchy hook
        sentences = [s.strip() for s in re.split(r"(?<=[.?!])\s+", full_text) if len(s.strip()) > 3]
        if sentences:
            hook = sentences[0]
            if len(hook) > 85:
                hook = hook[:82] + "..."
        else:
            hook = full_text[:80] + ("..." if len(full_text) > 80 else "")
    else:
        # Visual / action moment without speech
        hook = f"Custom Highlight [{int(start_sec // 60):02d}:{int(start_sec % 60):02d} - {int(end_sec // 60):02d}:{int(end_sec % 60):02d}]"
        full_text = f"Custom highlighted moment from {start_sec:.1f}s to {end_sec:.1f}s."

    # Calculate intensity score & label
    score, intensity_lbl = calculate_intensity_score(full_text, dur, words_in_range)
    if not words_in_range:
        intensity_lbl = "Custom Moment 🎯"
        score = 90.0

    # Determine next available ID
    existing_ids = []
    for tf in state.timeframes:
        try:
            existing_ids.append(int(tf.get("id", 0)))
        except (ValueError, TypeError):
            pass
    for c in state.draft_clips:
        try:
            existing_ids.append(int(c.get("id", 0)))
        except (ValueError, TypeError):
            pass
    for c in state.approved_clips:
        try:
            existing_ids.append(int(c.get("clip_num", 0)))
        except (ValueError, TypeError):
            pass
    new_id = (max(existing_ids) + 1) if existing_ids else 1

    # Generate live metadata, title, hashtags and description
    source_title = getattr(state, "source_title", "") or ""
    meta_full = generate_clip_metadata_full(
        text=full_text,
        hook=hook,
        index=new_id,
        variation=0,
        video_title=source_title,
        timestamps=f"{start_sec:.1f}s - {end_sec:.1f}s ({dur:.1f}s)"
    )

    tf_obj = {
        "id": new_id,
        "start": round(start_sec, 2),
        "end": round(end_sec, 2),
        "duration": round(dur, 1),
        "hook": hook,
        "full_text": full_text,
        "score": score,
        "intensity": intensity_lbl,
        "title": meta_full.get("title", f"Moment #{new_id} 🔥"),
        "hashtags": meta_full.get("hashtags", "#Shorts #Viral #Trending"),
        "description": meta_full.get("description", ""),
        "keywords": meta_full.get("keywords", []),
        "variation": 0,
        "selected": True,
        "custom": True
    }
    return tf_obj


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
            description = ""
            keywords = []

            if not thumb_file.exists() or thumb_file.stat().st_size == 0:
                generate_thumbnail(mp4, thumb_file)

            if txt_file.exists():
                try:
                    content = txt_file.read_text(encoding="utf-8")
                    if "\nDescription:\n" in content:
                        parts = content.split("\nDescription:\n", 1)[1]
                        description = parts.split("==================================================", 1)[0].strip()
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
                        elif line.startswith("Keywords:"):
                            kw_str = line.split(":", 1)[1].strip()
                            keywords = [k.strip() for k in kw_str.split(",") if k.strip()]
                        elif line.startswith("Description:") and not description:
                            description = line.split(":", 1)[1].strip()
                except Exception:
                    pass

            if not description:
                try:
                    meta_full = generate_clip_metadata_full(
                        text=hook or title,
                        hook=hook,
                        index=clip_num,
                        variation=0,
                        video_title=getattr(state, "source_title", ""),
                        timestamps=timestamps
                    )
                    description = meta_full.get("description", "")
                    if not keywords:
                        keywords = meta_full.get("keywords", [])
                except Exception:
                    description = f"Watch this viral {title} highlight!\n\n{hashtags}"

            approved.append({
                "id": base_name,
                "clip_num": clip_num,
                "filename": mp4.name,
                "title": title,
                "hashtags": hashtags,
                "description": description,
                "first_200": description[:200] if description else "",
                "keywords": keywords,
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

        # Probe source video title for context-aware clipping & niche metadata
        source_title = ""
        title_file = TEMP_DIR / "source_title.txt"
        if url.startswith("http"):
            try:
                title_proc = subprocess.run(
                    [YTDLP_PATH, "--print", "title", "--no-warnings", url],
                    capture_output=True, text=True, timeout=8
                )
                if title_proc.returncode == 0 and title_proc.stdout.strip():
                    source_title = title_proc.stdout.strip()
            except Exception:
                pass
        if not source_title and title_file.exists():
            try:
                source_title = title_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        if not source_title:
            source_title = Path(url).stem.replace("_", " ").title()

        try:
            title_file.write_text(source_title, encoding="utf-8")
        except Exception:
            pass

        with state.lock:
            state.source_title = source_title

        state.log(f"🎬 Video Topic Detected: \"{source_title}\"")

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
            "--video-title", getattr(state, "source_title", ""),
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
            position_choice = getattr(state, "subtitle_position", "bottom")
            font_choice = getattr(state, "subtitle_font", "Arial Black")
            bold_choice = getattr(state, "subtitle_bold", True)
            italic_choice = getattr(state, "subtitle_italic", False)
            uppercase_choice = getattr(state, "subtitle_uppercase", True)
            format_choice = getattr(state, "subtitle_format", "karaoke")

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
                "--position", position_choice,
                "--font", font_choice,
                "--format", format_choice,
                "--fps", "60",
            ]
            if bold_choice:
                captions_cmd.append("--bold")
            else:
                captions_cmd.append("--no-bold")
            if italic_choice:
                captions_cmd.append("--italic")
            if uppercase_choice:
                captions_cmd.append("--uppercase")
            else:
                captions_cmd.append("--no-uppercase")
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
            payload = json.dumps(state.to_dict()).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)
            return

        if path == "/api/doctor":
            try:
                from doctor import run_diagnostics
                doc_data = run_diagnostics()
            except Exception as e:
                doc_data = {"error": str(e)}
            payload = json.dumps(doc_data).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)
            return

        if path == "/api/youtube/status":
            try:
                yt_status = youtube_uploader.get_status() if youtube_uploader else {"connected": False, "error": "YouTube uploader module unavailable"}
            except Exception as e:
                yt_status = {"connected": False, "error": str(e)}
            payload = json.dumps(yt_status).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)
            return

        if path.startswith("/api/youtube/upload-status"):
            query = parse_qs(parsed.query)
            key = query.get("key", [""])[0]
            with state.lock:
                up_info = state.youtube_uploads.get(key, {"status": "idle", "progress": 0.0})
            payload = json.dumps(up_info).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)
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
                theme = data.get("theme", getattr(state, "subtitle_theme", "hormozi"))
                position = data.get("position", getattr(state, "subtitle_position", "bottom"))
                font = data.get("font", getattr(state, "subtitle_font", "Arial Black"))
                bold = data.get("bold", getattr(state, "subtitle_bold", True))
                italic = data.get("italic", getattr(state, "subtitle_italic", False))
                uppercase = data.get("uppercase", getattr(state, "subtitle_uppercase", True))
                fmt = data.get("format", getattr(state, "subtitle_format", "karaoke"))
                with state.lock:
                    state.subtitle_theme = theme
                    state.subtitle_position = position
                    state.subtitle_font = font
                    state.subtitle_bold = bool(bold)
                    state.subtitle_italic = bool(italic)
                    state.subtitle_uppercase = bool(uppercase)
                    state.subtitle_format = fmt
                state.log(f"Subtitle style updated: {theme.upper()} | Format: {fmt.upper()} | Font: {font} (Bold: {bold}, Italic: {italic}, Caps: {uppercase}) | Pos: {position.upper()}")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": True,
                    "theme": theme,
                    "position": position,
                    "font": font,
                    "bold": bold,
                    "italic": italic,
                    "uppercase": uppercase,
                    "format": fmt,
                }).encode("utf-8"))
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

        # Step 2b: Delete or clear detected hot moments
        if path == "/api/clear-timeframes":
            try:
                data = json.loads(body) if body else {}
                target_id = data.get("id")
            except Exception:
                target_id = None

            with state.lock:
                if target_id is not None:
                    state.timeframes = [tf for tf in state.timeframes if tf.get("id") != target_id]
                    state.log(f"Removed moment #{target_id} from detected list.")
                else:
                    state.timeframes = []
                    hooks_path = TEMP_DIR / "viral_hooks.json"
                    if hooks_path.exists():
                        try:
                            hooks_path.unlink()
                        except Exception:
                            pass
                    if state.status == "timeframes_ready":
                        state.status = "idle"
                        state.stage_message = "Detected moments cleared. Ready to re-scan."
                    state.log("🗑 Cleared all detected hot moments.")

            resp_bytes = json.dumps({"success": True, "remaining": len(state.timeframes)}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        # Step 2c: Add and analyze custom timeframe
        if path == "/api/add-custom-timeframe":
            try:
                data = json.loads(body) if body else {}
                start_sec = float(data.get("start", 0.0))
                end_sec = float(data.get("end", 0.0))
            except Exception:
                start_sec = 0.0
                end_sec = 0.0

            if end_sec <= start_sec:
                self.send_response(HTTPStatus.BAD_REQUEST)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "End time must be greater than start time."}).encode("utf-8"))
                return

            try:
                tf_obj = analyze_custom_timeframe(start_sec, end_sec)
                with state.lock:
                    state.timeframes.append(tf_obj)
                    if state.status in ["idle", "timeframes_ready"]:
                        state.status = "timeframes_ready"
                        state.progress_pct = 100
                        state.stage_message = f"Found {len(state.timeframes)} moments! Select clips to render below, preview in player, or regenerate titles."
                    state.log(f"🎯 Analyzed & added custom moment #{tf_obj['id']} ({start_sec:.1f}s - {end_sec:.1f}s): {tf_obj['title']}")

                # Persist to viral_hooks.json so it stays on disk
                hooks_path = TEMP_DIR / "viral_hooks.json"
                try:
                    hdata = []
                    if hooks_path.exists():
                        with open(hooks_path, "r", encoding="utf-8") as f:
                            hdata = json.load(f)
                    if isinstance(hdata, list):
                        hdata.append(tf_obj)
                    elif isinstance(hdata, dict) and "segments" in hdata:
                        hdata["segments"].append(tf_obj)
                    else:
                        hdata = [tf_obj]
                    with open(hooks_path, "w", encoding="utf-8") as f:
                        json.dump(hdata, f, indent=2, ensure_ascii=False)
                except Exception:
                    pass

                resp_bytes = json.dumps({"success": True, "timeframe": tf_obj, "total": len(state.timeframes)}).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(resp_bytes)
            except Exception as e:
                self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # Step 3: Regenerate metadata for a clip on-demand (Drafts or Final Approved Clips)
        if path == "/api/regenerate-metadata":
            try:
                data = json.loads(body) if body else {}
                text = data.get("text", "")
                hook = data.get("hook", "")
                raw_id = data.get("id", 1)
                filename = data.get("filename", "")
                is_final = bool(data.get("is_final", False)) or bool(filename)
                timestamps = data.get("timestamps", "")
                variation = int(data.get("variation", 0)) + 1
                v_title = data.get("video_title") or getattr(state, "source_title", "")
                if not v_title and (TEMP_DIR / "source_title.txt").exists():
                    try:
                        v_title = (TEMP_DIR / "source_title.txt").read_text(encoding="utf-8").strip()
                    except Exception:
                        pass
            except Exception:
                text, hook, raw_id, filename, is_final, timestamps, variation, v_title = "", "", 1, "", False, "", 1, ""

            try:
                clip_idx = int(str(raw_id).replace("clip_", "")) if str(raw_id).replace("clip_", "").isdigit() else 1
            except Exception:
                clip_idx = 1

            meta_full = generate_clip_metadata_full(
                text=text or hook,
                hook=hook,
                index=clip_idx,
                variation=variation,
                video_title=v_title,
                timestamps=timestamps
            )

            if is_final:
                target_fname = filename or f"clip_{clip_idx}.mp4"
                base_name = Path(target_fname).stem
                txt_path = FINAL_DIR / f"{base_name}.txt"
                save_clip_metadata_file(
                    txt_path=txt_path,
                    clip_num=clip_idx,
                    filename=target_fname,
                    timestamps=timestamps,
                    hook=hook,
                    framing="1080x1920 60 FPS (Lanczos4 + Color Graded)",
                    title=meta_full["title"],
                    hashtags=meta_full["hashtags"],
                    description=meta_full["description"],
                    keywords=meta_full["keywords"]
                )
                refresh_approved_clips()
                state.log(f"✨ [YouTube SEO] Regenerated Title, Keywords & 200-char Description for {target_fname}.")

            resp_bytes = json.dumps({
                "title": meta_full["title"],
                "hashtags": meta_full["hashtags"],
                "description": meta_full["description"],
                "first_200": meta_full["first_200"],
                "keywords": meta_full["keywords"],
                "variation": variation
            }).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        # Save custom edited metadata for a final clip
        if path == "/api/save-clip-metadata":
            try:
                data = json.loads(body) if body else {}
                filename = data.get("filename", "")
                title = data.get("title", "")
                hashtags = data.get("hashtags", "")
                description = data.get("description", "")
                hook = data.get("hook", "")
                timestamps = data.get("timestamps", "")
                raw_keywords = data.get("keywords", [])
            except Exception as e:
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                return

            if not filename:
                self.send_error(HTTPStatus.BAD_REQUEST, "Filename is required")
                return

            base_name = Path(filename).stem
            txt_path = FINAL_DIR / f"{base_name}.txt"
            m = re.search(r"clip_(\d+)", base_name)
            clip_idx = int(m.group(1)) if m else 1

            save_clip_metadata_file(
                txt_path=txt_path,
                clip_num=clip_idx,
                filename=filename,
                timestamps=timestamps,
                hook=hook,
                framing="1080x1920 60 FPS (Lanczos4 + Color Graded)",
                title=title,
                hashtags=hashtags,
                description=description,
                keywords=raw_keywords
            )
            refresh_approved_clips()
            state.log(f"💾 Saved updated metadata for final_clips/{filename}.")

            resp_bytes = json.dumps({"success": True, "filename": filename}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(resp_bytes)
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

                # Generate full SEO description
                desc, kws = generate_seo_description(
                    text=draft_info.get("full_text", hook) if draft_info else hook,
                    hook=hook,
                    title=title,
                    video_title=getattr(state, "source_title", ""),
                    timestamps=timestamps
                )

                save_clip_metadata_file(
                    txt_path=txt_file,
                    clip_num=cid,
                    filename=f"clip_{cid}.mp4",
                    timestamps=timestamps,
                    hook=hook,
                    framing=framing,
                    title=title,
                    hashtags=hashtags,
                    description=desc,
                    keywords=kws
                )
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

            # Generate full SEO description
            desc, kws = generate_seo_description(
                text=draft_info.get("full_text", hook) if draft_info else hook,
                hook=hook,
                title=title,
                video_title=getattr(state, "source_title", ""),
                timestamps=timestamps
            )

            save_clip_metadata_file(
                txt_path=txt_file,
                clip_num=clip_id,
                filename=f"clip_{clip_id}.mp4",
                timestamps=timestamps,
                hook=hook,
                framing=framing,
                title=title,
                hashtags=hashtags,
                description=desc,
                keywords=kws
            )
            state.log(f"✓ Approved Clip #{clip_id} moved to final_clips/clip_{clip_id}.mp4 with SEO description.")

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

        # YouTube Studio: Save OAuth Client Secrets
        if path == "/api/youtube/save-secrets":
            try:
                data = json.loads(body) if body else {}
                secrets_content = data.get("client_secrets") or data
                success = youtube_uploader.save_client_secrets(secrets_content) if youtube_uploader else False
                st = youtube_uploader.get_status() if youtube_uploader else {}
                resp_bytes = json.dumps({"success": success, "status": st}).encode("utf-8")
                self.send_response(HTTPStatus.OK if success else HTTPStatus.BAD_REQUEST)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(resp_bytes)
                return
            except Exception as e:
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
                return

        # YouTube Studio: Connect (OAuth login / token save)
        if path == "/api/youtube/connect":
            try:
                data = json.loads(body) if body else {}
                if "token_json" in data:
                    success = youtube_uploader.save_token_json(data["token_json"]) if youtube_uploader else False
                    st = youtube_uploader.get_status() if youtube_uploader else {}
                    res_obj = {"success": success, "status": st}
                else:
                    res_obj = youtube_uploader.start_local_auth(port=8089) if youtube_uploader else {"error": "Module unavailable"}
                resp_bytes = json.dumps(res_obj).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(resp_bytes)
                return
            except Exception as e:
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
                return

        # YouTube Studio: Disconnect
        if path == "/api/youtube/disconnect":
            try:
                success = youtube_uploader.disconnect() if youtube_uploader else False
                st = youtube_uploader.get_status() if youtube_uploader else {}
                resp_bytes = json.dumps({"success": success, "status": st}).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(resp_bytes)
                return
            except Exception as e:
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
                return

        # YouTube Studio: Exchange code or callback URL
        if path == "/api/youtube/exchange-code":
            try:
                data = json.loads(body) if body else {}
                code_str = data.get("code") or data.get("url") or ""
                res_obj = youtube_uploader.exchange_auth_code(code_str) if youtube_uploader else {"error": "Module unavailable"}
                status_info = youtube_uploader.get_status() if youtube_uploader else {}
                res_obj["status"] = status_info
                resp_bytes = json.dumps(res_obj).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(resp_bytes)
                return
            except Exception as e:
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
                return

        # YouTube Studio: Direct Upload Short
        if path == "/api/youtube/upload":
            if not youtube_uploader:
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "YouTube Uploader module not available")
                return

            try:
                data = json.loads(body) if body else {}
                filename = data.get("filename", "")
                folder = data.get("folder", "final")
                title = data.get("title", "")
                description = data.get("description", "")
                raw_tags = data.get("tags", "")
                privacy = data.get("privacy", "public")
                category_id = data.get("category_id", "22")
            except Exception as e:
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid request JSON")
                return

            if isinstance(raw_tags, str):
                tags_list = [t.strip().lstrip("#") for t in re.split(r"[,\s]+", raw_tags) if t.strip()]
            else:
                tags_list = list(raw_tags)

            video_dir = FINAL_DIR if folder == "final" else DRAFT_DIR
            video_path = video_dir / filename
            if not video_path.exists():
                self.send_error(HTTPStatus.NOT_FOUND, f"Video file not found: {filename}")
                return

            upload_key = f"{folder}_{filename}"
            with state.lock:
                state.youtube_uploads[upload_key] = {
                    "status": "uploading",
                    "progress": 0.0,
                    "message": "Connecting to YouTube Studio...",
                    "url": None,
                    "error": None
                }

            def _upload_worker():
                def _prog(pct, msg):
                    with state.lock:
                        if upload_key in state.youtube_uploads:
                            state.youtube_uploads[upload_key]["progress"] = round(pct, 1)
                            state.youtube_uploads[upload_key]["message"] = msg

                state.log(f"🚀 [YouTube Studio] Publishing {filename} as Short ({privacy.upper()})...")
                res = youtube_uploader.upload_short(
                    video_path=str(video_path),
                    title=title,
                    description=description,
                    tags=tags_list,
                    category_id=category_id,
                    privacy_status=privacy,
                    progress_callback=_prog
                )
                with state.lock:
                    if "error" in res:
                        state.youtube_uploads[upload_key]["status"] = "error"
                        state.youtube_uploads[upload_key]["error"] = res["error"]
                        state.youtube_uploads[upload_key]["message"] = f"Failed: {res['error']}"
                        state.log(f"❌ [YouTube Studio] Upload error for {filename}: {res['error']}")
                    else:
                        state.youtube_uploads[upload_key]["status"] = "completed"
                        state.youtube_uploads[upload_key]["progress"] = 100.0
                        state.youtube_uploads[upload_key]["url"] = res.get("url")
                        state.youtube_uploads[upload_key]["watch_url"] = res.get("watch_url")
                        state.youtube_uploads[upload_key]["message"] = "Published successfully!"
                        state.log(f"✓ [YouTube Studio] Published {filename} to YouTube Shorts! URL: {res.get('url')}")

            t = threading.Thread(target=_upload_worker, daemon=True)
            t.start()

            resp_bytes = json.dumps({"status": "started", "upload_key": upload_key}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(resp_bytes)
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
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Montserrat:ital,wght@0,700;0,800;0,900;1,700;1,900&family=Roboto:ital,wght@0,700;0,900;1,700;1,900&display=swap" rel="stylesheet">
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
      transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1);
      font-family: inherit;
      font-style: inherit;
      font-weight: inherit;
    }
    .theme-hormozi .word-active {
      color: #FFD700 !important;
      -webkit-text-stroke: 2px #000;
      text-shadow: 0 4px 8px rgba(0,0,0,0.9);
      transform: scale(1.1);
    }
    .theme-hormozi .word-default {
      color: #FFFFFF !important;
      -webkit-text-stroke: 1.5px #000;
      text-shadow: 0 2px 4px rgba(0,0,0,0.8);
    }
    .theme-mrbeast .word-active {
      color: #33FF00 !important;
      -webkit-text-stroke: 2px #000;
      text-shadow: 0 4px 8px rgba(0,0,0,0.9);
      transform: scale(1.1);
    }
    .theme-mrbeast .word-default {
      color: #FFFFFF !important;
      -webkit-text-stroke: 1.5px #000;
      text-shadow: 0 2px 4px rgba(0,0,0,0.8);
    }
    .theme-cyber .word-active {
      color: #00FFFF !important;
      -webkit-text-stroke: 2px #000;
      text-shadow: 0 0 14px rgba(0,255,255,0.8), 0 4px 8px rgba(0,0,0,0.9);
      transform: scale(1.1);
    }
    .theme-cyber .word-default {
      color: #FFFFFF !important;
      -webkit-text-stroke: 1.5px #000;
      text-shadow: 0 2px 4px rgba(0,0,0,0.8);
    }
    .theme-crimson .word-active {
      color: #FF2233 !important;
      -webkit-text-stroke: 2px #000;
      text-shadow: 0 0 12px rgba(255,34,51,0.6), 0 4px 8px rgba(0,0,0,0.9);
      transform: scale(1.1);
    }
    .theme-crimson .word-default {
      color: #FFFFFF !important;
      -webkit-text-stroke: 1.5px #000;
      text-shadow: 0 2px 4px rgba(0,0,0,0.8);
    }
    .theme-sunset .word-active {
      color: #FF7700 !important;
      -webkit-text-stroke: 2px #000;
      text-shadow: 0 0 12px rgba(255,119,0,0.7), 0 4px 8px rgba(0,0,0,0.9);
      transform: scale(1.1);
    }
    .theme-sunset .word-default {
      color: #FFFFFF !important;
      -webkit-text-stroke: 1.5px #000;
      text-shadow: 0 2px 4px rgba(0,0,0,0.8);
    }
    .theme-bubblegum .word-active {
      color: #FF1493 !important;
      -webkit-text-stroke: 2px #000;
      text-shadow: 0 0 12px rgba(255,20,147,0.7), 0 4px 8px rgba(0,0,0,0.9);
      transform: scale(1.1);
    }
    .theme-bubblegum .word-default {
      color: #FFFFFF !important;
      -webkit-text-stroke: 1.5px #000;
      text-shadow: 0 2px 4px rgba(0,0,0,0.8);
    }
    .theme-violet .word-active {
      color: #BF00FF !important;
      -webkit-text-stroke: 2px #000;
      text-shadow: 0 0 12px rgba(191,0,255,0.7), 0 4px 8px rgba(0,0,0,0.9);
      transform: scale(1.1);
    }
    .theme-violet .word-default {
      color: #FFFFFF !important;
      -webkit-text-stroke: 1.5px #000;
      text-shadow: 0 2px 4px rgba(0,0,0,0.8);
    }
    .theme-classic_white .word-active {
      color: #FFFFFF !important;
      -webkit-text-stroke: 1.5px #000;
      text-shadow: 0 3px 6px rgba(0,0,0,0.9);
      transform: scale(1.05);
      text-decoration: underline;
    }
    .theme-classic_white .word-default {
      color: #D1D5DB !important;
      -webkit-text-stroke: 1.2px #000;
      text-shadow: 0 2px 4px rgba(0,0,0,0.7);
    }
    /* Format Preview Styles */
    .caption-box-pill {
      background: rgba(0, 0, 0, 0.78);
      padding: 5px 12px;
      border-radius: 10px;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.85);
      border: 1px solid rgba(255, 255, 255, 0.15);
      display: inline-block;
      backdrop-filter: blur(4px);
    }
    .single-word-pop {
      display: inline-block;
      transform: scale(1.18);
      letter-spacing: 0.04em;
      line-height: 1.1;
      animation: popIn 0.22s cubic-bezier(0.175, 0.885, 0.32, 1.275);
    }
    @keyframes popIn {
      0% { transform: scale(0.8); opacity: 0.5; }
      100% { transform: scale(1.18); opacity: 1; }
    }
    .line-block-preview {
      text-shadow: 0 2px 8px rgba(0,0,0,0.9);
      letter-spacing: 0.02em;
      line-height: 1.3;
      display: inline-block;
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
      <!-- YouTube Studio Connect Pill -->
      <button id="ytConnectBtn" onclick="openYouTubeModal()" class="px-3.5 py-1.5 rounded-full text-xs font-semibold border border-red-900/60 bg-red-950/40 text-red-300 hover:bg-red-900/60 hover:text-white transition-all flex items-center space-x-2 cursor-pointer shadow-sm">
        <i class="fa-brands fa-youtube text-red-500 text-sm"></i>
        <span id="ytBtnLabel">Connect YouTube Studio</span>
      </button>

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
  <main class="flex-1 max-w-[1700px] w-full mx-auto p-4 sm:p-6 space-y-6">

    <!-- TOP SECTION: Left 1/3 (Ingestion & Captions Studio) vs Right 2/3 (Source Player, Kernel Logs, Hot Moments) -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">

      <!-- LEFT 1/3 COLUMN (lg:col-span-4): Video Ingestion & Captions Style Studio -->
      <div class="lg:col-span-4 flex flex-col space-y-6">

        <!-- CARD 1: Video Ingestion & Pipeline Launcher -->
        <div class="bg-zinc-900 border border-zinc-800 rounded-2xl p-5 shadow-xl space-y-4">
          <div>
            <div class="flex items-center justify-between mb-2">
              <label class="text-xs font-bold text-zinc-300 uppercase tracking-wider flex items-center space-x-2">
                <i class="fa-brands fa-youtube text-red-500 text-sm"></i>
                <span>Source Video / Link</span>
              </label>
              <button type="button" onclick="pasteVideoUrl()" class="text-indigo-400 hover:text-indigo-300 font-medium text-xs flex items-center space-x-1 cursor-pointer">
                <i class="fa-regular fa-paste"></i>
                <span>Paste</span>
              </button>
            </div>
            <div class="relative">
              <input type="text" id="videoUrlInput" 
                placeholder="YouTube URL or local file path..." 
                value="https://www.youtube.com/watch?v=BGUTv2K_SWs"
                class="w-full bg-zinc-950 border border-zinc-700/80 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 rounded-xl px-3.5 py-2.5 text-xs text-white placeholder-zinc-500 transition-all font-mono">
            </div>
            <p class="text-[11px] text-zinc-500 mt-1.5 flex items-center">
              <i class="fa-solid fa-bolt text-amber-400 mr-1 text-[10px]"></i>YouTube, Podcasts, Streams & Local Files
            </p>
          </div>

          <!-- Controls: Clips Picker & Resume -->
          <div class="space-y-3 pt-1 border-t border-zinc-800/80">
            <div>
              <span class="text-[11px] font-bold text-zinc-400 uppercase tracking-wider block mb-1.5">Target Clips Count</span>
              <div class="flex items-center space-x-1.5 bg-zinc-950 border border-zinc-800 rounded-xl p-1">
                <button type="button" onclick="setClipCount(3)" class="clip-count-btn flex-1 py-1.5 text-xs rounded-lg font-bold bg-indigo-600 text-white cursor-pointer" data-val="3">3</button>
                <button type="button" onclick="setClipCount(5)" class="clip-count-btn flex-1 py-1.5 text-xs rounded-lg font-bold text-zinc-400 hover:text-white cursor-pointer" data-val="5">5</button>
                <button type="button" onclick="setClipCount(8)" class="clip-count-btn flex-1 py-1.5 text-xs rounded-lg font-bold text-zinc-400 hover:text-white cursor-pointer" data-val="8">8</button>
                <button type="button" onclick="setClipCount(12)" class="clip-count-btn flex-1 py-1.5 text-xs rounded-lg font-bold text-zinc-400 hover:text-white cursor-pointer" data-val="12">12</button>
                <div class="flex items-center px-1 border-l border-zinc-800">
                  <input type="number" id="numClipsInput" value="3" min="1" max="25" class="w-9 bg-transparent text-center text-xs text-white font-bold focus:outline-none" title="Custom count">
                </div>
              </div>
            </div>

            <!-- Resume Checkbox -->
            <label class="flex items-center space-x-2 text-xs text-zinc-300 cursor-pointer bg-zinc-950/80 p-2.5 rounded-xl border border-zinc-800/80 hover:border-zinc-700 transition-colors">
              <input type="checkbox" id="resumeCheckbox" class="w-4 h-4 rounded text-amber-500 focus:ring-amber-400 bg-zinc-900 border-zinc-700">
              <span class="text-[11px]">Resume from last clip (<b id="resumeTimeLabel" class="text-amber-400 font-mono">00:00</b>)</span>
            </label>

            <!-- Scan Button -->
            <button id="scanBtn" onclick="scanVideo()" 
              class="w-full bg-gradient-to-r from-indigo-600 via-indigo-500 to-violet-600 hover:from-indigo-500 hover:to-violet-500 text-white font-bold text-xs py-3 rounded-xl shadow-lg shadow-indigo-600/30 flex items-center justify-center space-x-2 transition-all active:scale-[0.98] cursor-pointer">
              <i class="fa-solid fa-radar text-indigo-200"></i>
              <span>Scan & Detect Viral Hooks</span>
            </button>
          </div>

          <!-- Live Progress Bar -->
          <div id="progressSection" class="pt-3 border-t border-zinc-800/80 hidden">
            <div class="flex justify-between items-center text-xs mb-1.5">
              <span id="stageMsg" class="text-zinc-300 font-medium text-[11px] truncate">Initializing pipeline...</span>
              <span id="progressPct" class="text-indigo-400 font-bold ml-2">0%</span>
            </div>
            <div class="w-full bg-zinc-800 rounded-full h-2 overflow-hidden">
              <div id="progressBar" class="bg-gradient-to-r from-indigo-500 to-violet-500 h-2 rounded-full transition-all duration-300" style="width: 0%"></div>
            </div>
          </div>
        </div>

        <!-- CARD 2: Captions Style Studio & Live 9:16 Mockup (Height-Symmetric flex-1) -->
        <div class="bg-zinc-900 border border-zinc-800 rounded-2xl p-5 shadow-xl flex-1 flex flex-col justify-between space-y-4">
          <div class="space-y-4">
            <div class="flex items-center justify-between border-b border-zinc-800/80 pb-3">
              <div class="flex items-center space-x-2">
                <div class="w-7 h-7 rounded-lg bg-amber-500/10 border border-amber-500/20 flex items-center justify-center">
                  <i class="fa-solid fa-closed-captioning text-amber-400 text-xs"></i>
                </div>
                <div>
                  <h3 class="text-xs font-bold text-zinc-100 uppercase tracking-wider">Captions Studio</h3>
                  <p class="text-[10px] text-zinc-400">8 Themes • 4 Formats • Custom Fonts</p>
                </div>
              </div>
              <span id="activeStyleBadge" class="text-[10px] font-mono font-bold px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/30 truncate max-w-[130px]">Hormozi Gold</span>
            </div>

            <!-- Hidden select for compatibility -->
            <select id="subtitleThemeSelect" class="hidden">
              <option value="hormozi" selected>Hormozi Gold</option>
              <option value="mrbeast">MrBeast Lime</option>
              <option value="cyber">Cyber Cyan</option>
              <option value="crimson">Crimson Red</option>
              <option value="sunset">Sunset Orange</option>
              <option value="bubblegum">Bubblegum Pink</option>
              <option value="violet">Electric Purple</option>
              <option value="classic_white">Classic White</option>
            </select>

            <!-- Caption Display Format (4 Formats) -->
            <div>
              <div class="flex items-center justify-between mb-1.5">
                <span class="text-[10px] font-bold text-zinc-400 uppercase tracking-wider">Caption Format</span>
                <span id="activeFormatLabel" class="text-[10px] font-mono text-amber-400 font-semibold">Karaoke Sync</span>
              </div>
              <div class="grid grid-cols-2 gap-1.5" id="formatPillsGroup">
                <button type="button" onclick="selectSubtitleFormat('karaoke')" data-format="karaoke"
                  class="format-pill p-2 rounded-xl border text-left transition-all bg-indigo-600/20 border-indigo-500 text-white shadow-sm ring-1 ring-indigo-500/40 cursor-pointer flex items-center space-x-2">
                  <i class="fa-solid fa-microphone-lines text-indigo-400 text-xs shrink-0"></i>
                  <div class="truncate">
                    <div class="text-[11px] font-bold leading-tight">Karaoke Sync</div>
                    <div class="text-[9px] text-zinc-400 font-normal leading-tight">Word-by-word</div>
                  </div>
                </button>

                <button type="button" onclick="selectSubtitleFormat('single_word')" data-format="single_word"
                  class="format-pill p-2 rounded-xl border text-left transition-all bg-zinc-950 border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-700 cursor-pointer flex items-center space-x-2">
                  <i class="fa-solid fa-bolt text-amber-400 text-xs shrink-0"></i>
                  <div class="truncate">
                    <div class="text-[11px] font-bold leading-tight">Single Word</div>
                    <div class="text-[9px] text-zinc-400 font-normal leading-tight">High-velocity pop</div>
                  </div>
                </button>

                <button type="button" onclick="selectSubtitleFormat('line_block')" data-format="line_block"
                  class="format-pill p-2 rounded-xl border text-left transition-all bg-zinc-950 border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-700 cursor-pointer flex items-center space-x-2">
                  <i class="fa-solid fa-align-center text-sky-400 text-xs shrink-0"></i>
                  <div class="truncate">
                    <div class="text-[11px] font-bold leading-tight">Line Block</div>
                    <div class="text-[9px] text-zinc-400 font-normal leading-tight">Clean steady full line</div>
                  </div>
                </button>

                <button type="button" onclick="selectSubtitleFormat('box_highlight')" data-format="box_highlight"
                  class="format-pill p-2 rounded-xl border text-left transition-all bg-zinc-950 border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-700 cursor-pointer flex items-center space-x-2">
                  <i class="fa-solid fa-square text-emerald-400 text-xs shrink-0"></i>
                  <div class="truncate">
                    <div class="text-[11px] font-bold leading-tight">Boxed Pill</div>
                    <div class="text-[9px] text-zinc-400 font-normal leading-tight">Dark badge background</div>
                  </div>
                </button>
              </div>
            </div>

            <!-- 8 Themes Grid (Compact 4x2 grid) -->
            <div>
              <span class="text-[10px] font-bold text-zinc-400 uppercase tracking-wider block mb-1.5">Color Aesthetic</span>
              <div class="grid grid-cols-4 gap-1.5" id="stylePillsGroup">
                <button type="button" onclick="selectSubtitleTheme('hormozi')" data-theme="hormozi"
                  class="theme-pill py-1.5 px-2 rounded-lg border text-left transition-all bg-indigo-600/20 border-indigo-500 text-white shadow-sm ring-1 ring-indigo-500/40 cursor-pointer flex items-center space-x-1.5 truncate">
                  <span class="w-2.5 h-2.5 rounded-full bg-[#FFD700] shrink-0 border border-black/30"></span>
                  <span class="text-[10px] font-bold truncate">Hormozi</span>
                </button>

                <button type="button" onclick="selectSubtitleTheme('mrbeast')" data-theme="mrbeast"
                  class="theme-pill py-1.5 px-2 rounded-lg border text-left transition-all bg-zinc-950 border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-700 cursor-pointer flex items-center space-x-1.5 truncate">
                  <span class="w-2.5 h-2.5 rounded-full bg-[#33FF00] shrink-0 border border-black/30"></span>
                  <span class="text-[10px] font-bold truncate">MrBeast</span>
                </button>

                <button type="button" onclick="selectSubtitleTheme('cyber')" data-theme="cyber"
                  class="theme-pill py-1.5 px-2 rounded-lg border text-left transition-all bg-zinc-950 border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-700 cursor-pointer flex items-center space-x-1.5 truncate">
                  <span class="w-2.5 h-2.5 rounded-full bg-[#00FFFF] shrink-0 border border-black/30"></span>
                  <span class="text-[10px] font-bold truncate">Cyber</span>
                </button>

                <button type="button" onclick="selectSubtitleTheme('crimson')" data-theme="crimson"
                  class="theme-pill py-1.5 px-2 rounded-lg border text-left transition-all bg-zinc-950 border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-700 cursor-pointer flex items-center space-x-1.5 truncate">
                  <span class="w-2.5 h-2.5 rounded-full bg-[#FF2233] shrink-0 border border-black/30"></span>
                  <span class="text-[10px] font-bold truncate">Crimson</span>
                </button>

                <button type="button" onclick="selectSubtitleTheme('sunset')" data-theme="sunset"
                  class="theme-pill py-1.5 px-2 rounded-lg border text-left transition-all bg-zinc-950 border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-700 cursor-pointer flex items-center space-x-1.5 truncate">
                  <span class="w-2.5 h-2.5 rounded-full bg-[#FF7700] shrink-0 border border-black/30"></span>
                  <span class="text-[10px] font-bold truncate">Sunset</span>
                </button>

                <button type="button" onclick="selectSubtitleTheme('bubblegum')" data-theme="bubblegum"
                  class="theme-pill py-1.5 px-2 rounded-lg border text-left transition-all bg-zinc-950 border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-700 cursor-pointer flex items-center space-x-1.5 truncate">
                  <span class="w-2.5 h-2.5 rounded-full bg-[#FF1493] shrink-0 border border-black/30"></span>
                  <span class="text-[10px] font-bold truncate">Pink</span>
                </button>

                <button type="button" onclick="selectSubtitleTheme('violet')" data-theme="violet"
                  class="theme-pill py-1.5 px-2 rounded-lg border text-left transition-all bg-zinc-950 border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-700 cursor-pointer flex items-center space-x-1.5 truncate">
                  <span class="w-2.5 h-2.5 rounded-full bg-[#BF00FF] shrink-0 border border-black/30"></span>
                  <span class="text-[10px] font-bold truncate">Violet</span>
                </button>

                <button type="button" onclick="selectSubtitleTheme('classic_white')" data-theme="classic_white"
                  class="theme-pill py-1.5 px-2 rounded-lg border text-left transition-all bg-zinc-950 border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-700 cursor-pointer flex items-center space-x-1.5 truncate">
                  <span class="w-2.5 h-2.5 rounded-full bg-white shrink-0 border border-black/30"></span>
                  <span class="text-[10px] font-bold truncate">Classic</span>
                </button>
              </div>
            </div>

            <!-- Font Family Selector (6 Viral Fonts) -->
            <div>
              <div class="flex items-center justify-between mb-1.5">
                <span class="text-[10px] font-bold text-zinc-400 uppercase tracking-wider">Font Family</span>
                <span id="activeFontLabel" class="text-[10px] font-mono text-indigo-400 font-semibold">Arial Black</span>
              </div>
              <div class="grid grid-cols-3 gap-1.5" id="fontPillsGroup">
                <button type="button" onclick="selectSubtitleFont('Arial Black')" data-font="Arial Black" 
                  class="font-pill py-1.5 px-2 rounded-lg border text-[11px] font-black transition-all bg-indigo-600/20 border-indigo-500 text-white shadow-sm ring-1 ring-indigo-500/40 cursor-pointer text-center truncate" style="font-family: 'Arial Black', sans-serif;">
                  Arial Black
                </button>
                <button type="button" onclick="selectSubtitleFont('Impact')" data-font="Impact" 
                  class="font-pill py-1.5 px-2 rounded-lg border text-[11px] font-bold transition-all bg-zinc-950 border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-700 cursor-pointer text-center truncate" style="font-family: Impact, sans-serif;">
                  Impact
                </button>
                <button type="button" onclick="selectSubtitleFont('Montserrat')" data-font="Montserrat" 
                  class="font-pill py-1.5 px-2 rounded-lg border text-[11px] font-extrabold transition-all bg-zinc-950 border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-700 cursor-pointer text-center truncate" style="font-family: 'Montserrat', sans-serif;">
                  Montserrat
                </button>
                <button type="button" onclick="selectSubtitleFont('Trebuchet MS')" data-font="Trebuchet MS" 
                  class="font-pill py-1.5 px-2 rounded-lg border text-[11px] font-bold transition-all bg-zinc-950 border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-700 cursor-pointer text-center truncate" style="font-family: 'Trebuchet MS', sans-serif;">
                  Trebuchet
                </button>
                <button type="button" onclick="selectSubtitleFont('Verdana')" data-font="Verdana" 
                  class="font-pill py-1.5 px-2 rounded-lg border text-[11px] font-bold transition-all bg-zinc-950 border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-700 cursor-pointer text-center truncate" style="font-family: Verdana, sans-serif;">
                  Verdana
                </button>
                <button type="button" onclick="selectSubtitleFont('Roboto')" data-font="Roboto" 
                  class="font-pill py-1.5 px-2 rounded-lg border text-[11px] font-black transition-all bg-zinc-950 border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-700 cursor-pointer text-center truncate" style="font-family: 'Roboto', sans-serif;">
                  Roboto
                </button>
              </div>
            </div>

            <!-- Font Formatting Toggles & Screen Placement -->
            <div class="grid grid-cols-2 gap-2 pt-1">
              <div>
                <span class="text-[10px] font-bold text-zinc-400 uppercase tracking-wider block mb-1">Formatting</span>
                <div class="flex items-center space-x-1 bg-zinc-950 border border-zinc-800 rounded-xl p-1">
                  <button type="button" id="fontBoldBtn" onclick="toggleSubtitleBold()" 
                    class="flex-1 py-1 text-xs rounded-lg font-black bg-indigo-600 text-white cursor-pointer transition-all shadow-sm" title="Toggle Bold">
                    <b>B</b>
                  </button>
                  <button type="button" id="fontItalicBtn" onclick="toggleSubtitleItalic()" 
                    class="flex-1 py-1 text-xs rounded-lg font-serif italic bg-zinc-950 border border-zinc-800 text-zinc-400 hover:text-white cursor-pointer transition-all" title="Toggle Italic">
                    <i>I</i>
                  </button>
                  <button type="button" id="fontCapsBtn" onclick="toggleSubtitleCaps()" 
                    class="flex-1 py-1 text-[10px] rounded-lg font-bold bg-indigo-600 text-white cursor-pointer transition-all shadow-sm" title="Toggle ALL CAPS / Natural Case">
                    AA
                  </button>
                </div>
              </div>

              <div>
                <span class="text-[10px] font-bold text-zinc-400 uppercase tracking-wider block mb-1">Position</span>
                <div class="flex items-center space-x-1 bg-zinc-950 border border-zinc-800 rounded-xl p-1">
                  <button type="button" onclick="setSubtitlePosition('bottom')" id="posBtn_bottom"
                    class="pos-btn flex-1 py-1 text-[11px] rounded-lg font-bold bg-indigo-600 text-white cursor-pointer">
                    Bottom
                  </button>
                  <button type="button" onclick="setSubtitlePosition('lower_third')" id="posBtn_lower_third"
                    class="pos-btn flex-1 py-1 text-[11px] rounded-lg font-bold text-zinc-400 hover:text-white cursor-pointer">
                    Low-3rd
                  </button>
                  <button type="button" onclick="setSubtitlePosition('center')" id="posBtn_center"
                    class="pos-btn flex-1 py-1 text-[11px] rounded-lg font-bold text-zinc-400 hover:text-white cursor-pointer">
                    Center
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- Authentic 9:16 Vertical Screen Preview Mockup (Aligned to bottom) -->
          <div class="pt-2 flex justify-center">
            <div class="relative w-full max-w-[270px] aspect-[9/13] bg-gradient-to-b from-zinc-950 via-zinc-900 to-black rounded-2xl border-2 border-zinc-800 shadow-2xl overflow-hidden flex flex-col justify-between p-3 select-none">
              <!-- Phone Header -->
              <div class="flex items-center justify-between text-[9px] text-zinc-500 font-mono border-b border-zinc-800/60 pb-1.5">
                <span class="flex items-center space-x-1">
                  <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                  <span class="text-zinc-300 font-semibold">1080x1920 9:16</span>
                </span>
                <span id="previewFormatTag" class="text-indigo-400 font-semibold font-mono">Karaoke Sync</span>
              </div>

              <!-- Animated Video Stage -->
              <div id="previewScreenContainer" class="flex-1 flex flex-col justify-end items-center relative py-3 transition-all">
                <div id="karaokeLivePreview" class="text-center font-black tracking-wide text-sm transition-all select-none theme-hormozi" style="font-family: 'Arial Black', sans-serif;">
                  <span class="karaoke-word word-default">WAIT</span>
                  <span class="karaoke-word word-default">FOR</span>
                  <span class="karaoke-word word-active">THIS</span>
                  <span class="karaoke-word word-default">MOMENT!</span>
                </div>
              </div>

              <!-- Phone Footer -->
              <div class="border-t border-zinc-800/60 pt-1.5 flex items-center justify-between text-[9px] text-zinc-400">
                <span id="previewPositionLabel" class="font-mono text-zinc-400">Bottom</span>
                <span class="text-amber-400 font-semibold flex items-center space-x-1">
                  <i class="fa-solid fa-sparkles text-[9px]"></i>
                  <span id="previewFormatFooter">Viral Retention</span>
                </span>
              </div>
            </div>
          </div>

        </div>

      </div>

      <!-- RIGHT 2/3 COLUMN (lg:col-span-8): Source Video Preview, Kernel Logs (Half-Height), Hot Moments -->
      <div class="lg:col-span-8 flex flex-col space-y-6">

        <!-- CARD 1: Source Video Preview (Height & Width kept intact) -->
        <div class="bg-zinc-900 border border-zinc-800 rounded-2xl p-5 shadow-xl flex flex-col">
          <div class="flex items-center justify-between mb-3">
            <div class="flex items-center space-x-2">
              <span class="w-2.5 h-2.5 rounded-full bg-indigo-500"></span>
              <h2 class="text-xs font-bold uppercase tracking-wider text-zinc-200">Source Video Preview</h2>
              <span id="sourceDurationBadge" class="text-[11px] bg-zinc-800 text-zinc-400 px-2 py-0.5 rounded-md hidden"></span>
              <span id="sourceTitleBadge" class="text-[11px] text-indigo-300 font-semibold truncate max-w-[280px] bg-indigo-950/40 px-2.5 py-0.5 rounded-md border border-indigo-800/40 hidden items-center space-x-1.5"><i class="fa-solid fa-film text-[10px] text-indigo-400"></i><span id="sourceTitleText"></span></span>
            </div>
            <!-- Delete Source Button -->
            <button id="deleteSourceBtn" onclick="deleteSourceVideo()" class="hidden text-xs px-2.5 py-1 rounded-lg border border-red-900/50 bg-red-950/30 text-red-400 hover:bg-red-900/40 hover:text-red-200 transition-all flex items-center space-x-1.5 cursor-pointer">
              <i class="fa-solid fa-trash-can text-[11px]"></i>
              <span>Delete Source</span>
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

        <!-- CARD 2: Kernel Process & Live Logs (Height is exactly half of Source Video Preview) -->
        <div class="bg-zinc-900 border border-zinc-800 rounded-2xl p-4 shadow-xl flex flex-col">
          <div class="flex items-center justify-between mb-2">
            <div class="flex items-center space-x-2">
              <i class="fa-solid fa-terminal text-emerald-400 text-xs"></i>
              <h3 class="text-xs font-bold uppercase tracking-wider text-zinc-200">Kernel Process & Live Logs</h3>
            </div>
            <div class="flex items-center space-x-2 text-[11px] text-zinc-500">
              <span class="inline-block w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
              <span>Live Console</span>
            </div>
          </div>

          <!-- Terminal Window: Height is half of aspect-video (~180px) -->
          <div id="terminalWindow" class="h-[180px] max-h-[180px] bg-black/95 border border-zinc-800 rounded-xl p-3 font-mono text-[11px] leading-relaxed text-zinc-300 overflow-y-auto space-y-1 terminal-glow">
            <div class="text-zinc-500">// Universal Clipper engine initialized. Ready.</div>
          </div>
        </div>

        <!-- CARD 3: Detected Hot Moments (Select To Clip - Height-Symmetric flex-1) -->
        <div class="bg-zinc-900 border border-zinc-800 rounded-2xl p-5 shadow-xl flex-1 flex flex-col justify-between">
          <div>
            <div class="flex items-center justify-between mb-4">
              <div>
                <h3 class="text-xs font-bold uppercase tracking-wider text-zinc-200 flex items-center space-x-2">
                  <i class="fa-solid fa-fire text-amber-500"></i>
                  <span>Detected Hot Moments (Select To Clip)</span>
                </h3>
                <p class="text-[11px] text-zinc-400 mt-0.5">Click any card to preview in player. Check the clips you want to render.</p>
              </div>
              <div class="flex items-center space-x-2">
                <button onclick="selectAllTimeframes(true)" class="text-xs text-indigo-400 hover:text-indigo-300 font-medium px-2 py-1 bg-zinc-800 rounded cursor-pointer transition-all">Select All</button>
                <button onclick="selectAllTimeframes(false)" class="text-xs text-zinc-400 hover:text-zinc-200 font-medium px-2 py-1 bg-zinc-800 rounded cursor-pointer transition-all">Uncheck All</button>
                <button onclick="deleteAllTimeframes()" class="text-xs text-red-400 hover:text-red-300 font-medium px-2.5 py-1 bg-red-950/40 border border-red-900/50 hover:bg-red-900/50 rounded cursor-pointer flex items-center space-x-1 transition-all shadow-sm" title="Delete all detected moments (vanish from list, re-scan to regenerate)">
                  <i class="fa-solid fa-trash-can text-[10px]"></i>
                  <span>Delete All</span>
                </button>
              </div>
            </div>

            <!-- Timeframes Container -->
            <div id="timeframesContainer" class="space-y-3 max-h-[290px] overflow-y-auto pr-1">
              <div class="text-xs text-zinc-500 py-8 text-center">
                Click <b>"Scan & Detect Viral Hooks"</b> above to analyze the video and isolate hot moments.
              </div>
            </div>
          </div>

          <div>
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
                    <label class="block text-[10px] text-zinc-400 mb-1">Start (Seconds or MM:SS)</label>
                    <input type="text" id="customStart" value="0.0" placeholder="e.g. 120.0 or 02:00" class="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1 text-xs text-white font-mono">
                  </div>
                  <div>
                    <label class="block text-[10px] text-zinc-400 mb-1">End (Seconds or MM:SS)</label>
                    <input type="text" id="customEnd" value="35.0" placeholder="e.g. 155.0 or 02:35" class="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1 text-xs text-white font-mono">
                  </div>
                  <div class="flex items-end">
                    <button onclick="setCustomFromPlayer()" class="w-full bg-zinc-800 hover:bg-zinc-700 text-zinc-200 py-1.5 px-2 rounded-lg text-[11px] font-medium cursor-pointer" title="Set start time to current player position">
                      Use Player Time
                    </button>
                  </div>
                  <div class="flex items-end">
                    <button id="btnAddCustomTf" onclick="addCustomTimeframe()" class="w-full bg-indigo-600 hover:bg-indigo-500 text-white py-1.5 px-2 rounded-lg text-[11px] font-bold cursor-pointer transition-all">
                      + Add To List
                    </button>
                  </div>
                </div>
              </details>
            </div>
          </div>

        </div>

      </div>

    </div>

    <!-- BOTTOM SECTION: 2 Partitions Side-by-Side (50% / 50%) with Min/Max Height, Scroll & Lazy Loading -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">

      <!-- PARTITION 1: Draft Clips Review (draft_clips/) -->
      <section class="bg-zinc-900 border border-zinc-800 rounded-2xl p-5 shadow-xl flex flex-col">
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4 pb-3 border-b border-zinc-800">
          <div>
            <h2 class="text-sm font-bold text-zinc-100 flex items-center space-x-2">
              <i class="fa-solid fa-layer-group text-indigo-400"></i>
              <span>Draft Clips Review (draft_clips/)</span>
            </h2>
            <p class="text-[11px] text-zinc-400 mt-0.5">Review 9:16 Shorts with burned captions. Edit metadata or bulk approve.</p>
          </div>

          <!-- Bulk Action Controls -->
          <div id="draftBulkControls" class="flex items-center space-x-2">
            <span id="draftCountBadge" class="text-xs bg-indigo-950 text-indigo-300 border border-indigo-800 px-2.5 py-1 rounded-full font-semibold">0 Drafts</span>
            <button onclick="toggleAllDrafts(true)" class="text-xs px-2.5 py-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg font-medium cursor-pointer">Select All</button>
            <button id="bulkApproveBtn" onclick="bulkApproveDrafts()" disabled
              class="text-xs px-3 py-1 bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-bold rounded-lg shadow-lg shadow-emerald-600/20 opacity-40 cursor-not-allowed flex items-center space-x-1.5 transition-all">
              <i class="fa-solid fa-check-double text-xs"></i>
              <span id="bulkApproveText">Approve Selected</span>
            </button>
          </div>
        </div>

        <!-- Scrollable partition with Min/Max Height & Lazy Loading -->
        <div class="min-h-[440px] max-h-[640px] overflow-y-auto pr-1">
          <div id="draftClipsGrid" class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div class="col-span-full py-12 text-center text-zinc-500 text-xs">
              No draft clips rendered yet. Scan a video above, select your favorite moments, and click <b>Render Selected Clips</b>.
            </div>
          </div>
        </div>
      </section>

      <!-- PARTITION 2: Approved Final Clips (final_clips/) -->
      <section class="bg-zinc-900 border border-zinc-800 rounded-2xl p-5 shadow-xl flex flex-col">
        <div class="flex items-center justify-between mb-4 pb-3 border-b border-zinc-800">
          <div>
            <h2 class="text-sm font-bold text-emerald-400 flex items-center space-x-2">
              <i class="fa-solid fa-circle-check"></i>
              <span>Approved Final Clips (final_clips/)</span>
            </h2>
            <p class="text-[11px] text-zinc-400 mt-0.5">Ready for YouTube Shorts, Reels, and TikTok with verified metadata.</p>
          </div>
          <span id="approvedCountBadge" class="text-xs bg-emerald-950 text-emerald-300 border border-emerald-800 px-2.5 py-1 rounded-full font-semibold">0 Approved</span>
        </div>

        <!-- Scrollable partition with Min/Max Height & Lazy Loading -->
        <div class="min-h-[440px] max-h-[640px] overflow-y-auto pr-1">
          <div id="approvedClipsGrid" class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div class="col-span-full py-12 text-center text-zinc-500 text-xs">
              No approved clips yet. Approve draft clips above to move them here.
            </div>
          </div>
        </div>
      </section>

    </div>

    <!-- System Diagnostics Doctor Modal -->
    <div id="doctorModal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
      <div class="bg-zinc-900 border border-zinc-700 rounded-2xl max-w-xl w-full p-6 shadow-2xl relative space-y-4">
        <div class="flex items-center justify-between border-b border-zinc-800 pb-3">
          <div class="flex items-center space-x-2.5">
            <i class="fa-solid fa-stethoscope text-indigo-400 text-lg"></i>
            <h3 class="text-base font-bold text-white">System Diagnostics & Environment Doctor</h3>
          </div>
          <button onclick="closeDoctorModal()" class="text-zinc-400 hover:text-white text-lg px-2 py-1 cursor-pointer">
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
          <button onclick="closeDoctorModal()" class="bg-zinc-800 hover:bg-zinc-700 text-white px-4 py-1.5 rounded-lg text-xs font-semibold cursor-pointer">Close</button>
        </div>
      </div>
    </div>

    <!-- YouTube Studio Connection Modal -->
    <div id="youtubeModal" class="fixed inset-0 bg-black/80 backdrop-blur-md z-50 flex items-center justify-center p-4 hidden">
      <div class="bg-zinc-900 border border-zinc-700 rounded-2xl max-w-xl w-full p-6 shadow-2xl relative space-y-5 max-h-[90vh] overflow-y-auto">
        <div class="flex items-center justify-between border-b border-zinc-800 pb-3">
          <div class="flex items-center space-x-2.5">
            <div class="w-8 h-8 rounded-lg bg-red-600/20 border border-red-500/30 flex items-center justify-center text-red-500">
              <i class="fa-brands fa-youtube text-lg"></i>
            </div>
            <div>
              <h3 class="text-base font-bold text-white">YouTube Studio Connector</h3>
              <p class="text-[11px] text-zinc-400">Directly publish 60 FPS Shorts with titles, hashtags & tags</p>
            </div>
          </div>
          <button onclick="closeYouTubeModal()" class="text-zinc-400 hover:text-white text-lg px-2 py-1 cursor-pointer">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <!-- Connected State Card -->
        <div id="ytConnectedSection" class="hidden space-y-4">
          <div class="p-4 rounded-xl bg-gradient-to-r from-red-950/40 via-zinc-900 to-zinc-900 border border-red-900/50 flex items-center space-x-4">
            <img id="ytChannelAvatar" src="" alt="Avatar" class="w-14 h-14 rounded-full border-2 border-red-500 shadow-md object-cover bg-zinc-800">
            <div class="flex-1 min-w-0">
              <div class="flex items-center space-x-2">
                <span id="ytChannelTitle" class="text-sm font-bold text-white truncate">Connected Channel</span>
                <span class="text-[10px] bg-emerald-950 text-emerald-400 border border-emerald-800 px-2 py-0.5 rounded-full font-bold">Active ✓</span>
              </div>
              <p id="ytChannelHandle" class="text-xs text-zinc-400 font-mono truncate">@handle</p>
              <div class="flex items-center space-x-3 mt-1.5 text-[11px] text-zinc-300">
                <span><i class="fa-solid fa-users text-red-400 mr-1"></i><b id="ytSubCount">0</b> Subscribers</span>
                <span><i class="fa-solid fa-video text-zinc-400 mr-1"></i><b id="ytVideoCount">0</b> Videos</span>
              </div>
            </div>
          </div>
          <div class="p-3 bg-zinc-950 rounded-xl border border-zinc-800 text-xs text-zinc-400 flex items-center justify-between">
            <div class="flex items-center space-x-2">
              <i class="fa-solid fa-shield-halved text-emerald-400"></i>
              <span>OAuth 2.0 Upload Scope Active</span>
            </div>
            <button onclick="disconnectYouTube()" class="text-red-400 hover:text-red-300 font-semibold text-xs px-2.5 py-1 rounded-lg bg-red-950/60 border border-red-900/40 hover:bg-red-900/50 transition-all cursor-pointer">
              <i class="fa-solid fa-arrow-right-from-bracket mr-1"></i> Disconnect
            </button>
          </div>
        </div>

        <!-- Disconnected Setup State -->
        <div id="ytDisconnectedSection" class="space-y-4">
          <div class="p-3.5 bg-red-950/30 border border-red-900/50 rounded-xl text-xs text-red-200 flex items-start space-x-3">
            <i class="fa-solid fa-circle-info text-red-400 text-sm mt-0.5"></i>
            <div class="space-y-1">
              <div class="font-bold text-white">Connect YouTube Data API v3</div>
              <p class="text-zinc-300 leading-relaxed">Publish your 1080x1920 60 FPS vertical clips straight to YouTube Shorts with viral tags and title in one click.</p>
            </div>
          </div>

          <!-- Step 1: OAuth Client Secrets File / Text -->
          <div class="p-4 bg-zinc-950 rounded-xl border border-zinc-800 space-y-3">
            <div class="flex items-center justify-between">
              <label class="text-xs font-bold text-zinc-200 uppercase tracking-wide flex items-center space-x-1.5">
                <span class="w-4 h-4 rounded-full bg-indigo-600 text-[10px] text-white flex items-center justify-center font-bold">1</span>
                <span>Google Cloud OAuth Credentials</span>
              </label>
              <span id="ytSecretsStatus" class="text-[10px] px-2 py-0.5 rounded-full font-mono font-bold bg-zinc-900 text-zinc-400 border border-zinc-800">Secrets Missing</span>
            </div>

            <!-- Drag & Drop / File Input -->
            <div class="border-2 border-dashed border-zinc-800 hover:border-indigo-500 rounded-xl p-3 text-center transition-all bg-zinc-900/50">
              <input type="file" id="ytSecretsFile" accept=".json" onchange="handleSecretsFileSelected(event)" class="hidden">
              <label for="ytSecretsFile" class="cursor-pointer block space-y-1">
                <i class="fa-solid fa-cloud-arrow-up text-zinc-400 text-lg"></i>
                <div class="text-xs font-semibold text-zinc-300">Click to upload <span class="text-indigo-400 font-mono">client_secrets.json</span></div>
                <div class="text-[10px] text-zinc-500">From Google Cloud Console &gt; Credentials &gt; OAuth 2.0 Client IDs</div>
              </label>
            </div>

            <!-- Or Paste JSON / Credentials -->
            <div class="space-y-1">
              <div class="flex items-center justify-between text-[11px] text-zinc-400">
                <span>Or paste Client ID & Client Secret / JSON:</span>
              </div>
              <textarea id="ytSecretsText" rows="3" placeholder='{"installed": {"client_id": "...", "client_secret": "..."}}' 
                class="w-full bg-zinc-900 border border-zinc-800 focus:border-indigo-500 rounded-lg p-2 text-xs font-mono text-zinc-200 placeholder-zinc-600"></textarea>
              <button onclick="saveOAuthSecrets()" class="w-full bg-zinc-800 hover:bg-zinc-700 text-white text-xs py-2 rounded-lg font-semibold transition-all cursor-pointer">
                <i class="fa-solid fa-floppy-disk mr-1"></i> Save Credentials
              </button>
            </div>

            <!-- Free 2-Min Setup Guide Accordion -->
            <details class="text-[11px] text-zinc-400 bg-zinc-900/70 p-2.5 rounded-lg border border-zinc-800/80 cursor-pointer">
              <summary class="font-bold text-zinc-300 hover:text-white select-none">
                <i class="fa-solid fa-circle-question text-indigo-400 mr-1"></i> How to get free Google Cloud credentials (2 minutes)
              </summary>
              <div class="mt-2.5 space-y-1.5 pl-3 border-l-2 border-indigo-500/50 text-zinc-300">
                <p>1. Open <a href="https://console.cloud.google.com" target="_blank" class="text-indigo-400 underline">Google Cloud Console</a> and create or select a project.</p>
                <p>2. In <b>APIs &amp; Services &gt; Library</b>, search and <b>Enable "YouTube Data API v3"</b>.</p>
                <p>3. In <b>OAuth consent screen</b>, select <i>External</i>, set App Name, and add your Google email to <b>Test users</b>.</p>
                <p>4. In <b>Credentials &gt; Create Credentials &gt; OAuth client ID</b>, select Application type: <b>Desktop app</b>.</p>
                <p>5. Click <b>Download JSON</b> (or copy Client ID & Secret) and upload or paste above!</p>
              </div>
            </details>
          </div>

          <!-- Step 2: Sign-in & Authorize -->
          <div class="p-4 bg-zinc-950 rounded-xl border border-zinc-800 space-y-3">
            <label class="text-xs font-bold text-zinc-200 uppercase tracking-wide flex items-center space-x-1.5">
              <span class="w-4 h-4 rounded-full bg-red-600 text-[10px] text-white flex items-center justify-center font-bold">2</span>
              <span>Authorize YouTube Studio Access</span>
            </label>
            <button id="ytAuthLaunchBtn" onclick="startYouTubeOAuthFlow()" class="w-full bg-gradient-to-r from-red-600 via-rose-600 to-red-600 hover:from-red-500 hover:to-rose-500 text-white text-xs py-2.5 rounded-xl font-bold shadow-lg shadow-red-600/30 flex items-center justify-center space-x-2 transition-all cursor-pointer">
              <i class="fa-brands fa-google text-sm"></i>
              <span>Sign in with Google &amp; Connect YouTube</span>
            </button>

            <!-- Direct Clickable Auth Link (if popup blocked or browser didn't pop up) -->
            <div id="ytAuthLinkBox" class="hidden space-y-2 p-3 bg-zinc-900 border border-indigo-700/60 rounded-xl text-center">
              <div class="text-xs text-zinc-300 font-medium">Browser didn't open automatically?</div>
              <a id="ytDirectAuthLink" href="#" target="_blank" class="inline-flex items-center space-x-2 px-4 py-2 bg-red-600 hover:bg-red-500 text-white font-bold text-xs rounded-lg shadow-md transition-all">
                <i class="fa-solid fa-arrow-up-right-from-square"></i>
                <span>Open Google Sign-In Window</span>
              </a>
              <p class="text-[10px] text-zinc-400">Sign in with your Google account, then return here.</p>
            </div>

            <!-- Manual Code or Callback URL Paste Fallback -->
            <div id="ytManualCodeSection" class="hidden pt-2 border-t border-zinc-900 space-y-2">
              <label class="text-[11px] font-semibold text-zinc-400">Paste Authorization Code or Redirect URL:</label>
              <div class="flex space-x-2">
                <input type="text" id="ytAuthCodeInput" placeholder="Paste 4/0A... or http://localhost:8089/?code=..." 
                  class="flex-1 bg-zinc-900 border border-zinc-800 focus:border-red-500 rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono">
                <button onclick="submitAuthCodeOrUrl()" class="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-bold transition-all cursor-pointer">
                  Verify &amp; Connect
                </button>
              </div>
            </div>

            <div id="ytAuthStatusMsg" class="hidden text-xs text-center p-2 rounded-lg bg-zinc-900 border border-zinc-800 text-zinc-300"></div>

            <!-- Alternative manual token paste -->
            <details class="text-[11px] text-zinc-400">
              <summary class="cursor-pointer text-zinc-500 hover:text-zinc-300 select-none">Or paste token.json directly</summary>
              <div class="mt-2 space-y-2">
                <textarea id="ytTokenJsonInput" rows="2" placeholder='{"token": "...", "refresh_token": "...", "client_id": "..."}' 
                  class="w-full bg-zinc-900 border border-zinc-800 rounded-lg p-2 text-xs font-mono text-zinc-200"></textarea>
                <button onclick="saveDirectTokenJson()" class="px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-xs text-white rounded-lg font-semibold cursor-pointer">
                  Save Token
                </button>
              </div>
            </details>
          </div>
        </div>

        <div class="border-t border-zinc-800 pt-3 flex justify-end">
          <button onclick="closeYouTubeModal()" class="bg-zinc-800 hover:bg-zinc-700 text-white px-4 py-1.5 rounded-lg text-xs font-semibold cursor-pointer">Done</button>
        </div>
      </div>
    </div>

    <!-- YouTube Short Publishing Modal -->
    <div id="publishModal" class="fixed inset-0 bg-black/80 backdrop-blur-md z-50 flex items-center justify-center p-4 hidden">
      <div class="bg-zinc-900 border border-zinc-700 rounded-2xl max-w-xl w-full p-6 shadow-2xl relative space-y-4 max-h-[92vh] overflow-y-auto">
        <div class="flex items-center justify-between border-b border-zinc-800 pb-3">
          <div class="flex items-center space-x-2.5">
            <div class="w-8 h-8 rounded-lg bg-red-600/20 border border-red-500/30 flex items-center justify-center text-red-500">
              <i class="fa-brands fa-youtube text-lg"></i>
            </div>
            <div>
              <h3 class="text-base font-bold text-white flex items-center space-x-2">
                <span>Publish to YouTube Shorts</span>
                <span class="text-[10px] bg-red-600 text-white px-2 py-0.5 rounded-full font-bold">1080x1920 60 FPS</span>
              </h3>
              <p id="publishChannelHint" class="text-[11px] text-zinc-400">Target Channel: Loading...</p>
            </div>
          </div>
          <button onclick="closePublishModal()" class="text-zinc-400 hover:text-white text-lg px-2 py-1 cursor-pointer">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <!-- Target Video File Banner -->
        <div class="p-3 bg-zinc-950 rounded-xl border border-zinc-800 flex items-center justify-between">
          <div class="flex items-center space-x-3">
            <div class="w-10 h-14 bg-black rounded-lg border border-zinc-800 flex items-center justify-center text-red-500 shadow-inner">
              <i class="fa-solid fa-play text-sm"></i>
            </div>
            <div>
              <div id="publishFilename" class="text-xs font-bold text-white font-mono">clip_1.mp4</div>
              <div id="publishDuration" class="text-[11px] text-zinc-400">1080x1920 60 FPS • Vertical Short</div>
            </div>
          </div>
          <span id="publishFolderBadge" class="text-[10px] px-2 py-1 rounded bg-zinc-900 border border-zinc-800 text-zinc-300 font-mono uppercase">final</span>
        </div>

        <!-- Metadata Form -->
        <div class="space-y-3.5 text-xs">
          <div>
            <div class="flex items-center justify-between mb-1">
              <label class="font-bold text-zinc-300 uppercase tracking-wide">Short Title (Max 100 chars)</label>
              <span id="publishTitleCount" class="text-[10px] font-mono text-zinc-500">0/100</span>
            </div>
            <input type="text" id="publishTitleInput" maxlength="100" oninput="updatePublishCounts()" 
              class="w-full bg-zinc-950 border border-zinc-800 focus:border-red-500 rounded-xl px-3 py-2 text-white font-medium text-xs">
          </div>

          <div>
            <div class="flex items-center justify-between mb-1">
              <label class="font-bold text-zinc-300 uppercase tracking-wide">Video Description & Keywords</label>
              <span id="publishDescCount" class="text-[10px] font-mono text-zinc-500">0/5000</span>
            </div>
            <textarea id="publishDescInput" rows="4" maxlength="5000" oninput="updatePublishCounts()" 
              class="w-full bg-zinc-950 border border-zinc-800 focus:border-red-500 rounded-xl p-3 text-zinc-200 text-xs leading-relaxed font-sans"></textarea>
          </div>

          <div>
            <label class="font-bold text-zinc-300 uppercase tracking-wide mb-1 block">Hashtags & Tags (Comma separated)</label>
            <input type="text" id="publishTagsInput" 
              class="w-full bg-zinc-950 border border-zinc-800 focus:border-red-500 rounded-xl px-3 py-2 text-zinc-300 font-mono text-xs">
          </div>

          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="font-bold text-zinc-300 uppercase tracking-wide mb-1 block">Privacy Setting</label>
              <select id="publishPrivacySelect" class="w-full bg-zinc-950 border border-zinc-800 focus:border-red-500 rounded-xl px-3 py-2 text-white text-xs cursor-pointer">
                <option value="public" selected>🌐 Public (Live on Shorts Feed)</option>
                <option value="unlisted">🔗 Unlisted (Anyone with link)</option>
                <option value="private">🔒 Private (Studio Draft)</option>
              </select>
            </div>
            <div>
              <label class="font-bold text-zinc-300 uppercase tracking-wide mb-1 block">Category</label>
              <select id="publishCategorySelect" class="w-full bg-zinc-950 border border-zinc-800 focus:border-red-500 rounded-xl px-3 py-2 text-white text-xs cursor-pointer">
                <option value="24" selected>Entertainment</option>
                <option value="20">Gaming</option>
                <option value="22">People & Blogs</option>
                <option value="23">Comedy</option>
                <option value="28">Science & Technology</option>
                <option value="27">Education</option>
              </select>
            </div>
          </div>
        </div>

        <!-- Progress Container (Shown during upload) -->
        <div id="publishProgressContainer" class="hidden p-4 rounded-xl bg-zinc-950 border border-red-950/80 space-y-2">
          <div class="flex items-center justify-between text-xs">
            <span id="publishProgressMsg" class="font-bold text-zinc-200 flex items-center space-x-1.5">
              <i class="fa-solid fa-spinner fa-spin text-red-500 mr-1"></i>
              <span>Uploading to YouTube Studio...</span>
            </span>
            <span id="publishProgressPct" class="font-mono text-red-400 font-bold">0%</span>
          </div>
          <div class="w-full bg-zinc-900 rounded-full h-2.5 overflow-hidden border border-zinc-800 shadow-inner">
            <div id="publishProgressBar" class="bg-gradient-to-r from-red-600 via-rose-500 to-emerald-400 h-2.5 rounded-full transition-all duration-300 w-0"></div>
          </div>
        </div>

        <!-- Success Result Container -->
        <div id="publishSuccessContainer" class="hidden p-4 rounded-xl bg-emerald-950/40 border border-emerald-800/80 space-y-3 text-center">
          <div class="w-12 h-12 rounded-full bg-emerald-600/20 text-emerald-400 flex items-center justify-center mx-auto text-xl border border-emerald-500/40">
            <i class="fa-solid fa-check"></i>
          </div>
          <div class="text-sm font-bold text-white">Short Published Successfully!</div>
          <p class="text-xs text-zinc-300">Your vertical short is now live in YouTube Studio.</p>
          <div class="pt-1 flex items-center justify-center space-x-2">
            <a id="publishViewShortBtn" href="#" target="_blank" class="px-4 py-2 rounded-xl bg-red-600 hover:bg-red-500 text-white font-bold text-xs shadow-lg shadow-red-600/30 flex items-center space-x-1.5">
              <i class="fa-brands fa-youtube"></i>
              <span>View Short on YouTube</span>
            </a>
          </div>
        </div>

        <!-- Actions -->
        <div class="border-t border-zinc-800 pt-3 flex items-center justify-between">
          <button type="button" onclick="closePublishModal()" class="bg-zinc-800 hover:bg-zinc-700 text-zinc-300 hover:text-white px-4 py-2 rounded-xl text-xs font-semibold cursor-pointer">
            Close
          </button>
          <button id="publishSubmitBtn" type="button" onclick="submitPublishToYouTube()" class="bg-gradient-to-r from-red-600 via-rose-600 to-red-600 hover:from-red-500 hover:to-rose-500 text-white px-5 py-2.5 rounded-xl text-xs font-bold shadow-lg shadow-red-600/30 flex items-center space-x-2 transition-all active:scale-[0.98] cursor-pointer">
            <i class="fa-brands fa-youtube text-sm"></i>
            <span>Upload Short to YouTube</span>
          </button>
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
    let currentSubtitleFont = 'Arial Black';
    let currentSubtitleBold = true;
    let currentSubtitleItalic = false;
    let currentSubtitleUppercase = true;
    let currentCaptionFormat = 'karaoke';

    function renderKaraokePreview() {
      const container = document.getElementById('karaokeLivePreview');
      if (!container) return;
      container.className = `text-center tracking-wide text-sm sm:text-base transition-all select-none pt-1 theme-${currentSubtitleTheme}`;
      container.style.fontFamily = `'${currentSubtitleFont}', Impact, sans-serif`;
      container.style.fontWeight = currentSubtitleBold ? '900' : '500';
      container.style.fontStyle = currentSubtitleItalic ? 'italic' : 'normal';

      const words = currentSubtitleUppercase 
        ? ["WAIT", "FOR", "THIS", "MOMENT!"] 
        : ["Wait", "for", "this", "moment!"];

      let html = '';
      if (currentCaptionFormat === 'single_word') {
        const activeWord = words[previewWordIndex % words.length];
        html = `<div class="single-word-pop word-active text-lg sm:text-xl font-black">${activeWord}</div>`;
      } else if (currentCaptionFormat === 'line_block') {
        html = `<div class="line-block-preview px-2 text-white font-extrabold tracking-wide">${words.join(' ')}</div>`;
      } else if (currentCaptionFormat === 'box_highlight') {
        let inner = '';
        words.forEach((word, idx) => {
          const isActive = idx === previewWordIndex;
          const cls = isActive ? 'word-active' : 'word-default';
          inner += `<span class="karaoke-word ${cls}">${word}</span>`;
        });
        html = `<div class="caption-box-pill">${inner}</div>`;
      } else {
        words.forEach((word, idx) => {
          const isActive = idx === previewWordIndex;
          const cls = isActive ? 'word-active' : 'word-default';
          html += `<span class="karaoke-word ${cls}">${word}</span>`;
        });
      }

      container.innerHTML = html;
      previewWordIndex = (previewWordIndex + 1) % words.length;
    }

    setInterval(renderKaraokePreview, 650);

    function selectSubtitleFormat(fmt) {
      currentCaptionFormat = fmt;
      const label = document.getElementById('activeFormatLabel');
      const formatNames = {
        'karaoke': 'Karaoke Sync',
        'single_word': 'Single Word Pop',
        'line_block': 'Line Block',
        'box_highlight': 'Boxed Pill'
      };
      if (label) label.textContent = formatNames[fmt] || fmt;

      const tag = document.getElementById('previewFormatTag');
      if (tag) tag.textContent = formatNames[fmt] || fmt;

      const footer = document.getElementById('previewFormatFooter');
      if (footer) footer.textContent = fmt === 'single_word' ? 'High CTR' : (fmt === 'box_highlight' ? 'Pro Badge' : (fmt === 'line_block' ? 'Clean Minimal' : 'Viral Retention'));

      document.querySelectorAll('.format-pill').forEach(btn => {
        const f = btn.getAttribute('data-format');
        if (f === fmt) {
          btn.className = 'format-pill p-2 rounded-xl border text-left transition-all bg-indigo-600/20 border-indigo-500 text-white shadow-sm ring-1 ring-indigo-500/40 cursor-pointer flex items-center space-x-2';
        } else {
          btn.className = 'format-pill p-2 rounded-xl border text-left transition-all bg-zinc-950 border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-700 cursor-pointer flex items-center space-x-2';
        }
      });

      renderKaraokePreview();
      sendThemeConfig();
    }

    function selectSubtitleFont(fontName) {
      currentSubtitleFont = fontName;
      const label = document.getElementById('activeFontLabel');
      if (label) label.textContent = fontName;

      document.querySelectorAll('.font-pill').forEach(btn => {
        const f = btn.getAttribute('data-font');
        if (f === fontName) {
          btn.className = 'font-pill py-1.5 px-2 rounded-lg border text-[11px] font-black transition-all bg-indigo-600/20 border-indigo-500 text-white shadow-sm ring-1 ring-indigo-500/40 cursor-pointer text-center truncate';
        } else {
          btn.className = 'font-pill py-1.5 px-2 rounded-lg border text-[11px] font-bold transition-all bg-zinc-950 border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-700 cursor-pointer text-center truncate';
        }
      });

      renderKaraokePreview();
      sendThemeConfig();
    }

    function toggleSubtitleBold() {
      currentSubtitleBold = !currentSubtitleBold;
      const btn = document.getElementById('fontBoldBtn');
      if (btn) {
        if (currentSubtitleBold) {
          btn.className = 'flex-1 py-1 text-xs rounded-lg font-black bg-indigo-600 text-white cursor-pointer transition-all shadow-sm';
        } else {
          btn.className = 'flex-1 py-1 text-xs rounded-lg font-bold bg-zinc-950 border border-zinc-800 text-zinc-400 hover:text-white cursor-pointer transition-all';
        }
      }
      renderKaraokePreview();
      sendThemeConfig();
    }

    function toggleSubtitleItalic() {
      currentSubtitleItalic = !currentSubtitleItalic;
      const btn = document.getElementById('fontItalicBtn');
      if (btn) {
        if (currentSubtitleItalic) {
          btn.className = 'flex-1 py-1 text-xs rounded-lg font-serif italic bg-indigo-600 text-white cursor-pointer transition-all shadow-sm';
        } else {
          btn.className = 'flex-1 py-1 text-xs rounded-lg font-serif italic bg-zinc-950 border border-zinc-800 text-zinc-400 hover:text-white cursor-pointer transition-all';
        }
      }
      renderKaraokePreview();
      sendThemeConfig();
    }

    function toggleSubtitleCaps() {
      currentSubtitleUppercase = !currentSubtitleUppercase;
      const btn = document.getElementById('fontCapsBtn');
      if (btn) {
        if (currentSubtitleUppercase) {
          btn.className = 'flex-1 py-1 text-[10px] rounded-lg font-bold bg-indigo-600 text-white cursor-pointer transition-all shadow-sm';
        } else {
          btn.className = 'flex-1 py-1 text-[10px] rounded-lg font-normal bg-zinc-950 border border-zinc-800 text-zinc-400 hover:text-white cursor-pointer transition-all';
        }
      }
      renderKaraokePreview();
      sendThemeConfig();
    }

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
          btn.className = 'clip-count-btn px-3 py-1.5 text-xs rounded-lg font-bold bg-indigo-600 text-white cursor-pointer';
        } else {
          btn.className = 'clip-count-btn px-3 py-1.5 text-xs rounded-lg font-bold text-zinc-400 hover:text-white cursor-pointer';
        }
      });
    }

    let currentSubtitlePosition = 'bottom';

    function setSubtitlePosition(pos) {
      currentSubtitlePosition = pos;
      
      document.querySelectorAll('.pos-btn').forEach(btn => {
        btn.className = 'pos-btn flex-1 py-1 text-[11px] rounded-lg font-bold text-zinc-400 hover:text-white cursor-pointer';
      });
      const activeBtn = document.getElementById('posBtn_' + pos);
      if (activeBtn) {
        activeBtn.className = 'pos-btn flex-1 py-1 text-[11px] rounded-lg font-bold bg-indigo-600 text-white cursor-pointer';
      }

      const container = document.getElementById('previewScreenContainer');
      const label = document.getElementById('previewPositionLabel');
      if (container) {
        if (pos === 'center') {
          container.className = 'flex-1 flex flex-col justify-center items-center relative py-3 transition-all';
          if (label) label.textContent = 'Position: Center';
        } else if (pos === 'lower_third') {
          container.className = 'flex-1 flex flex-col justify-end pb-8 items-center relative py-3 transition-all';
          if (label) label.textContent = 'Position: Lower-Third';
        } else {
          container.className = 'flex-1 flex flex-col justify-end items-center relative py-3 transition-all';
          if (label) label.textContent = 'Position: Bottom';
        }
      }

      sendThemeConfig();
    }

    async function selectSubtitleTheme(theme) {
      currentSubtitleTheme = theme;
      const themeSelect = document.getElementById('subtitleThemeSelect');
      if (themeSelect) themeSelect.value = theme;

      const badgeNames = {
        'hormozi': 'Hormozi Gold (#FFD700)',
        'mrbeast': 'MrBeast Lime (#33FF00)',
        'cyber': 'Cyber Cyan (#00FFFF)',
        'crimson': 'Crimson Red (#FF2233)',
        'sunset': 'Sunset Orange (#FF7700)',
        'bubblegum': 'Bubblegum Pink (#FF1493)',
        'violet': 'Electric Violet (#BF00FF)',
        'classic_white': 'Classic White (#FFFFFF)'
      };
      const badge = document.getElementById('activeStyleBadge');
      if (badge) badge.textContent = badgeNames[theme] || theme;

      document.querySelectorAll('.theme-pill').forEach(btn => {
        const t = btn.getAttribute('data-theme');
        if (t === theme) {
          btn.className = 'theme-pill py-1.5 px-2 rounded-lg border text-left transition-all bg-indigo-600/20 border-indigo-500 text-white shadow-sm ring-1 ring-indigo-500/40 cursor-pointer flex items-center space-x-1.5 truncate';
        } else {
          btn.className = 'theme-pill py-1.5 px-2 rounded-lg border text-left transition-all bg-zinc-950 border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-700 cursor-pointer flex items-center space-x-1.5 truncate';
        }
      });

      renderKaraokePreview();
      await sendThemeConfig();
    }

    async function sendThemeConfig() {
      try {
        await fetch('/api/set_theme', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            theme: currentSubtitleTheme,
            position: currentSubtitlePosition,
            font: currentSubtitleFont,
            bold: currentSubtitleBold,
            italic: currentSubtitleItalic,
            uppercase: currentSubtitleUppercase,
            format: currentCaptionFormat,
          })
        });
      } catch (err) {
        console.error('Failed to sync theme config:', err);
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

    // ==========================================
    // YouTube Studio Connector & Shorts Publisher
    // ==========================================
    let currentYouTubeStatus = { connected: false };
    let currentPublishPayload = null;
    let publishPollTimer = null;

    async function checkYouTubeStatus() {
      try {
        const res = await fetch('/api/youtube/status');
        const data = await res.json();
        currentYouTubeStatus = data;
        updateYouTubeUI(data);
      } catch (e) {
        console.error('Error fetching YouTube status:', e);
      }
    }

    function updateYouTubeUI(st) {
      const btn = document.getElementById('ytConnectBtn');
      const label = document.getElementById('ytBtnLabel');
      const connSec = document.getElementById('ytConnectedSection');
      const disconnSec = document.getElementById('ytDisconnectedSection');
      const secretsStatus = document.getElementById('ytSecretsStatus');
      const pubHint = document.getElementById('publishChannelHint');

      if (st && st.connected && st.channel && !st.channel.error) {
        const ch = st.channel;
        if (btn) {
          btn.className = 'px-3.5 py-1.5 rounded-full text-xs font-semibold border border-emerald-700/60 bg-emerald-950/40 text-emerald-300 hover:bg-emerald-900/60 transition-all flex items-center space-x-2 cursor-pointer shadow-sm';
        }
        if (label) {
          label.innerHTML = `
            <span class="w-2 h-2 rounded-full bg-emerald-400 inline-block mr-1"></span>
            <span>${escapeHtml(ch.title || 'YouTube Connected')}</span>
          `;
        }

        if (connSec) connSec.classList.remove('hidden');
        if (disconnSec) disconnSec.classList.add('hidden');

        const avatarEl = document.getElementById('ytChannelAvatar');
        if (avatarEl) {
          if (ch.avatar) {
            avatarEl.src = ch.avatar;
            avatarEl.classList.remove('hidden');
          } else {
            avatarEl.src = 'https://www.youtube.com/s/desktop/f6b21697/img/favicon_144x144.png';
          }
        }
        const titleEl = document.getElementById('ytChannelTitle');
        if (titleEl) titleEl.textContent = ch.title || 'YouTube Studio Channel';
        const handleEl = document.getElementById('ytChannelHandle');
        if (handleEl) handleEl.textContent = ch.custom_url || (ch.id ? `Channel ID: ${ch.id}` : '@channel');
        const subEl = document.getElementById('ytSubCount');
        if (subEl) subEl.textContent = Number(ch.subscribers || 0).toLocaleString();
        const vidEl = document.getElementById('ytVideoCount');
        if (vidEl) vidEl.textContent = Number(ch.videos || 0).toLocaleString();

        if (pubHint) pubHint.textContent = `Target Channel: ${ch.title} (${ch.custom_url || ''})`;
      } else {
        if (btn) {
          btn.className = 'px-3.5 py-1.5 rounded-full text-xs font-semibold border border-red-900/60 bg-red-950/40 text-red-300 hover:bg-red-900/60 hover:text-white transition-all flex items-center space-x-2 cursor-pointer shadow-sm';
        }
        if (label) {
          label.textContent = 'Connect YouTube Studio';
        }

        if (connSec) connSec.classList.add('hidden');
        if (disconnSec) disconnSec.classList.remove('hidden');

        if (secretsStatus) {
          if (st && st.has_client_secrets) {
            secretsStatus.className = 'text-[10px] px-2 py-0.5 rounded-full font-mono font-bold bg-emerald-950 text-emerald-300 border border-emerald-800';
            secretsStatus.textContent = 'Secrets Configured ✓';
          } else {
            secretsStatus.className = 'text-[10px] px-2 py-0.5 rounded-full font-mono font-bold bg-zinc-900 text-zinc-400 border border-zinc-800';
            secretsStatus.textContent = 'Secrets Missing';
          }
        }

        if (pubHint) pubHint.innerHTML = `<span class="text-amber-400 font-bold">⚠️ YouTube not connected.</span> <a href="javascript:void(0)" onclick="closePublishModal(); openYouTubeModal();" class="text-indigo-400 underline">Connect Account</a>`;
      }
    }

    function openYouTubeModal() {
      checkYouTubeStatus();
      document.getElementById('youtubeModal').classList.remove('hidden');
    }

    function closeYouTubeModal() {
      document.getElementById('youtubeModal').classList.add('hidden');
    }

    function handleSecretsFileSelected(event) {
      const file = event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = async function(e) {
        try {
          const content = e.target.result;
          document.getElementById('ytSecretsText').value = content;
          await saveOAuthSecretsData(content);
        } catch (err) {
          alert('Error reading client secrets file: ' + err.message);
        }
      };
      reader.readAsText(file);
    }

    async function saveOAuthSecrets() {
      const text = document.getElementById('ytSecretsText').value.trim();
      if (!text) {
        alert('Please paste client_secrets.json content or upload the JSON file.');
        return;
      }
      await saveOAuthSecretsData(text);
    }

    async function saveOAuthSecretsData(jsonString) {
      try {
        let parsed;
        let str = (jsonString || '').trim();
        // Auto-extract first valid JSON object in case of accidental wrapping/trailing chars
        const firstBrace = str.indexOf('{');
        const lastBrace = str.lastIndexOf('}');
        if (firstBrace !== -1 && lastBrace > firstBrace) {
          str = str.substring(firstBrace, lastBrace + 1);
        }
        try {
          parsed = JSON.parse(str);
        } catch (err) {
          alert('Invalid JSON formatting: ' + err.message + '\\nPlease check your credentials syntax.');
          return false;
        }

        const res = await fetch('/api/youtube/save-secrets', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ client_secrets: parsed })
        });
        const data = await res.json();
        if (res.ok && data.success) {
          alert('✓ Google Cloud OAuth credentials saved successfully! Now click Step 2 to sign in.');
          checkYouTubeStatus();
          return true;
        } else {
          alert('Failed to save client secrets: ' + (data.error || 'Unknown error'));
          return false;
        }
      } catch (e) {
        alert('Network error saving secrets: ' + e.message);
        return false;
      }
    }

    async function startYouTubeOAuthFlow() {
      const btn = document.getElementById('ytAuthLaunchBtn');
      const msg = document.getElementById('ytAuthStatusMsg');
      const linkBox = document.getElementById('ytAuthLinkBox');
      const directLink = document.getElementById('ytDirectAuthLink');
      const manualSec = document.getElementById('ytManualCodeSection');

      if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i><span>Opening Google Sign-in...</span>';
      }
      if (msg) {
        msg.classList.remove('hidden');
        msg.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin text-red-500 mr-1.5"></i> Awaiting Google sign-in in your browser...';
      }

      try {
        const res = await fetch('/api/youtube/connect', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        const data = await res.json();
        if (data.error) {
          if (msg) msg.innerHTML = `<span class="text-red-400 font-bold">Error: ${escapeHtml(data.error)}</span>`;
          if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="fa-brands fa-google text-sm mr-1"></i><span>Retry Sign in with Google</span>';
          }
          return;
        }

        // Show direct link button and manual code box immediately if auth_url returned
        if (data.auth_url) {
          if (directLink) directLink.href = data.auth_url;
          if (linkBox) linkBox.classList.remove('hidden');
          if (manualSec) manualSec.classList.remove('hidden');

          // Attempt to open in a new tab/window directly from browser
          try {
            window.open(data.auth_url, '_blank');
          } catch (winErr) {
            console.warn('Popup blocked by browser:', winErr);
          }
        }

        // Poll for connection completion
        let attempts = 0;
        const pollConn = setInterval(async () => {
          attempts++;
          const checkRes = await fetch('/api/youtube/status');
          const checkData = await checkRes.json();
          if (checkData.connected) {
            clearInterval(pollConn);
            if (msg) msg.innerHTML = '<span class="text-emerald-400 font-bold">✓ Successfully connected to YouTube Studio!</span>';
            if (btn) {
              btn.disabled = false;
              btn.innerHTML = '<i class="fa-brands fa-google text-sm mr-1"></i><span>Connected</span>';
            }
            if (linkBox) linkBox.classList.add('hidden');
            if (manualSec) manualSec.classList.add('hidden');
            updateYouTubeUI(checkData);
          } else if (attempts >= 60) { // 120 seconds timeout
            clearInterval(pollConn);
            if (msg) msg.innerHTML = '<span class="text-amber-400">Authentication timed out. If browser did not redirect, copy the code/URL from your address bar and paste it in the box above.</span>';
            if (btn) {
              btn.disabled = false;
              btn.innerHTML = '<i class="fa-brands fa-google text-sm mr-1"></i><span>Sign in with Google & Connect YouTube</span>';
            }
          }
        }, 2000);
      } catch (e) {
        if (msg) msg.innerHTML = `<span class="text-red-400">Connection error: ${e.message}</span>`;
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = '<i class="fa-brands fa-google text-sm mr-1"></i><span>Sign in with Google & Connect YouTube</span>';
        }
      }
    }

    async function submitAuthCodeOrUrl() {
      const input = document.getElementById('ytAuthCodeInput');
      const val = input ? input.value.trim() : '';
      const msg = document.getElementById('ytAuthStatusMsg');
      const linkBox = document.getElementById('ytAuthLinkBox');
      const manualSec = document.getElementById('ytManualCodeSection');

      if (!val) {
        alert('Please paste the authorization code or redirect URL from your browser address bar.');
        return;
      }

      if (msg) {
        msg.classList.remove('hidden');
        msg.innerHTML = '<i class="fa-solid fa-spinner fa-spin text-red-500 mr-1.5"></i> Exchanging authorization code with YouTube...';
      }

      try {
        const res = await fetch('/api/youtube/exchange-code', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: val })
        });
        const data = await res.json();
        if (data.success && data.status && data.status.connected) {
          if (msg) msg.innerHTML = '<span class="text-emerald-400 font-bold">✓ Successfully connected to YouTube Studio!</span>';
          alert('✓ YouTube Studio connected successfully!');
          if (linkBox) linkBox.classList.add('hidden');
          if (manualSec) manualSec.classList.add('hidden');
          checkYouTubeStatus();
        } else {
          const errMsg = data.error || data.status?.error || 'Verification failed';
          if (msg) msg.innerHTML = `<span class="text-red-400 font-bold">Error: ${escapeHtml(errMsg)}</span>`;
          alert('Connection failed: ' + errMsg);
        }
      } catch (e) {
        if (msg) msg.innerHTML = `<span class="text-red-400">Network error: ${escapeHtml(e.message)}</span>`;
        alert('Network error: ' + e.message);
      }
    }

    async function saveDirectTokenJson() {
      const rawText = document.getElementById('ytTokenJsonInput').value.trim();
      if (!rawText) {
        alert('Please paste valid credentials JSON');
        return;
      }

      // Check if user accidentally pasted client_secrets instead of token.json
      if (rawText.includes('"installed"') || (rawText.includes('"client_id"') && rawText.includes('"client_secret"') && !rawText.includes('"refresh_token"'))) {
        const ok = await saveOAuthSecretsData(rawText);
        if (ok) {
          alert('✓ Detected OAuth Client Credentials! Saved to client_secrets.json.\\n\\nNow click "Sign in with Google & Connect YouTube" to finish connecting.');
        }
        return;
      }

      // Extract JSON substring if user copied extra characters
      let text = rawText;
      const firstBrace = text.indexOf('{');
      const lastBrace = text.lastIndexOf('}');
      if (firstBrace !== -1 && lastBrace > firstBrace) {
        text = text.substring(firstBrace, lastBrace + 1);
      }

      try {
        const tokenObj = JSON.parse(text);
        const res = await fetch('/api/youtube/connect', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token_json: tokenObj })
        });
        const data = await res.json();
        if (data.success && data.status && data.status.connected) {
          alert('✓ Token saved and YouTube Studio connected!');
          checkYouTubeStatus();
        } else {
          alert('Invalid token or connection failed: ' + (data.status?.error || 'Unknown error'));
        }
      } catch (e) {
        alert('Error parsing token JSON: ' + e.message + '\\n\\nTip: If you downloaded client_secret_*.json from Google Cloud Console, paste it in "Step 1: Save Credentials", not here.');
      }
    }

    async function disconnectYouTube() {
      if (!confirm('Are you sure you want to disconnect YouTube Studio?')) return;
      try {
        const res = await fetch('/api/youtube/disconnect', { method: 'POST' });
        const data = await res.json();
        alert('YouTube Studio disconnected.');
        checkYouTubeStatus();
      } catch (e) {
        alert('Error disconnecting: ' + e.message);
      }
    }

    // ==========================================
    // Publish Short Modal & Upload Actions
    // ==========================================
    function openPublishModal(filename, title, tags, hook, folder, timestamps, customDesc = '') {
      if (!currentYouTubeStatus.connected) {
        if (confirm('YouTube Studio is not connected yet! Would you like to connect your channel now?')) {
          openYouTubeModal();
          return;
        }
      }

      currentPublishPayload = {
        filename: filename,
        folder: folder || 'final',
        hook: hook || '',
        timestamps: timestamps || ''
      };

      document.getElementById('publishFilename').textContent = filename;
      document.getElementById('publishFolderBadge').textContent = folder || 'final';
      document.getElementById('publishDuration').textContent = timestamps ? `1080x1920 60 FPS • ${timestamps}` : '1080x1920 60 FPS • Vertical Short';

      // Auto-format title with #Shorts
      let cleanTitle = (title || 'Viral Highlight').trim();
      if (!cleanTitle.toLowerCase().includes('#shorts')) {
        if (cleanTitle.length <= 90) cleanTitle += ' #Shorts';
      }
      document.getElementById('publishTitleInput').value = cleanTitle.substring(0, 100);

      // Description: use full research SEO description if provided, otherwise compose
      let cleanTags = (tags || '#Shorts #Viral #Trending').trim();
      if (customDesc && customDesc.trim()) {
        document.getElementById('publishDescInput').value = customDesc.trim().substring(0, 5000);
      } else {
        let parentTitle = window.__sourceTitle || '';
        let desc = `${cleanTitle}\n\n`;
        if (hook) desc += `🔥 Hook: "${hook}"\n`;
        if (parentTitle) desc += `📺 Source Video: ${parentTitle}\n`;
        if (timestamps) desc += `⏱️ Clip Range: ${timestamps}\n`;
        desc += `\nSubtitles: 60 FPS Animated Karaoke Subtitles\nResolution: 1080x1920 (9:16 Shorts / Reels / TikTok)\n\n${cleanTags}`;
        document.getElementById('publishDescInput').value = desc.substring(0, 5000);
      }

      // Clean tags field (comma separated)
      const tagTokens = cleanTags.split(/[#,\\s]+/).filter(Boolean);
      document.getElementById('publishTagsInput').value = tagTokens.join(', ');

      // Reset state views
      document.getElementById('publishProgressContainer').classList.add('hidden');
      document.getElementById('publishSuccessContainer').classList.add('hidden');
      const submitBtn = document.getElementById('publishSubmitBtn');
      submitBtn.disabled = false;
      submitBtn.classList.remove('opacity-40', 'cursor-not-allowed');
      submitBtn.innerHTML = '<i class="fa-brands fa-youtube text-sm mr-1"></i><span>Upload Short to YouTube</span>';

      updatePublishCounts();
      updateYouTubeUI(currentYouTubeStatus);
      document.getElementById('publishModal').classList.remove('hidden');
    }

    function openPublishModalWithData(filename, title, desc, tags, hook, folder, timestamps) {
      openPublishModal(filename, title, tags, hook, folder, timestamps, desc);
    }

    function closePublishModal() {
      if (publishPollTimer) {
        clearInterval(publishPollTimer);
        publishPollTimer = null;
      }
      document.getElementById('publishModal').classList.add('hidden');
    }

    function updatePublishCounts() {
      const tInput = document.getElementById('publishTitleInput');
      const dInput = document.getElementById('publishDescInput');
      const tCount = document.getElementById('publishTitleCount');
      const dCount = document.getElementById('publishDescCount');

      if (tInput && tCount) {
        tCount.textContent = `${tInput.value.length}/100`;
        tCount.className = tInput.value.length > 90 ? 'text-[10px] font-mono text-amber-400 font-bold' : 'text-[10px] font-mono text-zinc-500';
      }
      if (dInput && dCount) {
        dCount.textContent = `${dInput.value.length}/5000`;
      }
    }

    async function submitPublishToYouTube() {
      if (!currentPublishPayload) return;
      if (!currentYouTubeStatus.connected) {
        alert('Please connect your YouTube Studio account first!');
        openYouTubeModal();
        return;
      }

      const title = document.getElementById('publishTitleInput').value.trim();
      const description = document.getElementById('publishDescInput').value.trim();
      const tagsRaw = document.getElementById('publishTagsInput').value.trim();
      const privacy = document.getElementById('publishPrivacySelect').value;
      const categoryId = document.getElementById('publishCategorySelect').value;

      if (!title) {
        alert('Please provide a title for the YouTube Short.');
        return;
      }

      const submitBtn = document.getElementById('publishSubmitBtn');
      const progBox = document.getElementById('publishProgressContainer');
      const progBar = document.getElementById('publishProgressBar');
      const progMsg = document.getElementById('publishProgressMsg');
      const progPct = document.getElementById('publishProgressPct');
      const succBox = document.getElementById('publishSuccessContainer');

      submitBtn.disabled = true;
      submitBtn.classList.add('opacity-40', 'cursor-not-allowed');
      submitBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i><span>Publishing...</span>';

      progBox.classList.remove('hidden');
      succBox.classList.add('hidden');
      progBar.style.width = '5%';
      progPct.textContent = '5%';
      progMsg.innerHTML = '<i class="fa-solid fa-spinner fa-spin text-red-500 mr-1.5"></i> Initializing resumable 60 FPS upload...';

      try {
        const payload = {
          filename: currentPublishPayload.filename,
          folder: currentPublishPayload.folder,
          title: title,
          description: description,
          tags: tagsRaw,
          privacy: privacy,
          category_id: categoryId
        };

        const res = await fetch('/api/youtube/upload', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail || 'Upload request failed');
        }

        const uploadKey = data.upload_key;
        if (publishPollTimer) clearInterval(publishPollTimer);

        publishPollTimer = setInterval(async () => {
          try {
            const stRes = await fetch(`/api/youtube/upload-status?key=${encodeURIComponent(uploadKey)}`);
            const stData = await stRes.json();

            if (stData.status === 'uploading') {
              const p = Math.max(5, Math.min(99, stData.progress || 10));
              progBar.style.width = `${p}%`;
              progPct.textContent = `${p.toFixed(0)}%`;
              progMsg.innerHTML = `<i class="fa-solid fa-cloud-arrow-up text-red-500 mr-1.5 animate-pulse"></i> ${escapeHtml(stData.message || 'Uploading chunks to YouTube...')}`;
            } else if (stData.status === 'completed') {
              clearInterval(publishPollTimer);
              publishPollTimer = null;
              progBar.style.width = '100%';
              progPct.textContent = '100%';
              progBox.classList.add('hidden');
              succBox.classList.remove('hidden');

              const viewBtn = document.getElementById('publishViewShortBtn');
              if (viewBtn && stData.url) {
                viewBtn.href = stData.url;
              }

              submitBtn.disabled = false;
              submitBtn.classList.remove('opacity-40', 'cursor-not-allowed');
              submitBtn.innerHTML = '<i class="fa-solid fa-check mr-1"></i><span>Uploaded ✓</span>';
            } else if (stData.status === 'error') {
              clearInterval(publishPollTimer);
              publishPollTimer = null;
              progBox.classList.add('hidden');
              submitBtn.disabled = false;
              submitBtn.classList.remove('opacity-40', 'cursor-not-allowed');
              submitBtn.innerHTML = '<i class="fa-brands fa-youtube text-sm mr-1"></i><span>Retry Upload</span>';
              alert('YouTube Upload Error: ' + (stData.error || stData.message));
            }
          } catch (pollErr) {
            console.error('Upload poll error:', pollErr);
          }
        }, 1200);

      } catch (err) {
        progBox.classList.add('hidden');
        submitBtn.disabled = false;
        submitBtn.classList.remove('opacity-40', 'cursor-not-allowed');
        submitBtn.innerHTML = '<i class="fa-brands fa-youtube text-sm mr-1"></i><span>Upload Short to YouTube</span>';
        alert('Failed to start YouTube upload: ' + err.message);
      }
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
        if (data.subtitle_position && data.subtitle_position !== currentSubtitlePosition) {
          setSubtitlePosition(data.subtitle_position);
        }
        if (data.subtitle_font && data.subtitle_font !== currentSubtitleFont && !window.__fontInitialized) {
          window.__fontInitialized = true;
          selectSubtitleFont(data.subtitle_font);
        }
        if (data.subtitle_format && data.subtitle_format !== currentCaptionFormat && !window.__formatInitialized) {
          window.__formatInitialized = true;
          selectSubtitleFormat(data.subtitle_format);
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

        // Display detected video title
        if (data.source_title) {
          window.__sourceTitle = data.source_title;
          const badge = document.getElementById('sourceTitleBadge');
          const titleText = document.getElementById('sourceTitleText');
          if (badge && titleText) {
            titleText.textContent = data.source_title;
            badge.title = data.source_title;
            badge.classList.remove('hidden');
            badge.classList.add('flex');
          }
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
                <button onclick="deleteSingleTimeframe(${index}, ${tf.id})" 
                  class="text-xs px-2 py-1 rounded-md bg-zinc-800 hover:bg-red-950/80 hover:text-red-400 text-zinc-400 transition-all cursor-pointer border border-transparent hover:border-red-900/40" title="Delete this moment">
                  <i class="fa-solid fa-trash-can text-[10px]"></i>
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
          body: JSON.stringify({ 
            text: tf.full_text || tf.hook, 
            hook: tf.hook, 
            id: tf.id, 
            variation: tf.variation,
            video_title: window.__sourceTitle || ''
          })
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
          body: JSON.stringify({ 
            text: draft.full_text || draft.hook, 
            hook: draft.hook, 
            id: draft.id, 
            variation: draft.variation,
            video_title: window.__sourceTitle || ''
          })
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

    async function deleteAllTimeframes() {
      if (loadedTimeframes.length === 0) return;
      if (!confirm("Are you sure you want to delete all detected moments? They will vanish from the list. You can re-scan and regenerate them anytime.")) {
        return;
      }
      try {
        const res = await fetch('/api/clear-timeframes', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        if (res.ok) {
          loadedTimeframes = [];
          lastTimeframesFingerprint = "";
          renderTimeframes([]);
          updateSelectedCount();
        }
      } catch (err) {
        console.error('Failed to clear timeframes:', err);
      }
    }

    async function deleteSingleTimeframe(index, tfId) {
      try {
        const res = await fetch('/api/clear-timeframes', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: tfId })
        });
        if (res.ok) {
          loadedTimeframes = loadedTimeframes.filter(tf => tf.id !== tfId);
          lastTimeframesFingerprint = "";
          renderTimeframes(loadedTimeframes);
          updateSelectedCount();
        }
      } catch (err) {
        console.error('Failed to delete timeframe:', err);
      }
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

    function parseTimeToSeconds(input) {
      if (typeof input === 'number') return input;
      const str = String(input || '').trim();
      if (!str) return 0;
      if (str.includes(':')) {
        const parts = str.split(':').map(p => parseFloat(p) || 0);
        if (parts.length === 3) {
          return parts[0] * 3600 + parts[1] * 60 + parts[2];
        } else if (parts.length === 2) {
          return parts[0] * 60 + parts[1];
        }
      }
      return parseFloat(str) || 0;
    }

    async function addCustomTimeframe() {
      const startInput = document.getElementById('customStart');
      const endInput = document.getElementById('customEnd');
      const btn = document.getElementById('btnAddCustomTf');

      const startSec = parseTimeToSeconds(startInput.value);
      const endSec = parseTimeToSeconds(endInput.value);

      if (endSec <= startSec) {
        alert('End time must be greater than start time.');
        return;
      }

      if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i> Analyzing...';
      }

      try {
        const res = await fetch('/api/add-custom-timeframe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ start: startSec, end: endSec })
        });
        const data = await res.json();
        if (res.ok && data.success && data.timeframe) {
          const newTf = data.timeframe;

          // Check if already in loadedTimeframes
          const exists = loadedTimeframes.some(tf => tf.id === newTf.id);
          if (!exists) {
            loadedTimeframes.push(newTf);
          } else {
            // Replace if exists
            loadedTimeframes = loadedTimeframes.map(tf => tf.id === newTf.id ? newTf : tf);
          }

          lastTimeframesFingerprint = "";
          renderTimeframes(loadedTimeframes);
          updateSelectedCount();

          // Scroll to the newly added card in Detected Hot Moments
          setTimeout(() => {
            const newIndex = loadedTimeframes.length - 1;
            const newCard = document.getElementById(`tf_card_${newIndex}`);
            if (newCard) {
              newCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
              newCard.classList.add('ring-2', 'ring-indigo-500', 'bg-indigo-950/60');
              setTimeout(() => {
                newCard.classList.remove('ring-2', 'ring-indigo-500', 'bg-indigo-950/60');
              }, 2500);
            }
          }, 200);

          alert(`✓ Analyzed & added Moment #${newTf.id} (${newTf.duration}s) to Detected Hot Moments!\\n\\nHook: "${newTf.hook}"\\nTitle: ${newTf.title}`);
        } else {
          alert('Failed to add custom timeframe: ' + (data.error || 'Server error'));
        }
      } catch (err) {
        alert('Network error analyzing custom timeframe: ' + err.message);
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = '+ Add To List';
        }
      }
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
            <div class="relative w-full aspect-[9/16] max-h-[360px] bg-black rounded-xl overflow-hidden border border-zinc-800 flex items-center justify-center">
              <video controls playsinline preload="none" ${d.poster ? `poster="${d.poster}"` : ''} class="w-full h-full object-contain lazy-media" data-src="/media/draft/${d.filename}#t=0.001">
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
                <button onclick="regenerateDraftMeta(${d.id})" class="text-[10px] text-indigo-400 hover:text-indigo-300 font-medium cursor-pointer">
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

            <div class="pt-1 flex flex-col space-y-2">
              ${approveBtn}
              ${(isReady && !d.approved) ? `
                <button onclick="openPublishModal('${d.filename}', document.getElementById('title_${d.id}') ? document.getElementById('title_${d.id}').value : '', document.getElementById('tags_${d.id}') ? document.getElementById('tags_${d.id}').value : '', '${escapeHtml(d.hook || '')}', 'draft', '${d.start.toFixed(1)}s - ${d.end.toFixed(1)}s')"
                  class="w-full bg-zinc-900 hover:bg-red-950/60 border border-zinc-800 hover:border-red-700/60 text-zinc-300 hover:text-red-300 text-xs py-2 rounded-xl font-semibold flex items-center justify-center space-x-2 transition-all cursor-pointer">
                  <i class="fa-brands fa-youtube text-red-500 text-sm"></i>
                  <span>Publish Draft to YouTube</span>
                </button>
              ` : ''}
            </div>
          </div>
        `;
      }).join('');

      updateBulkApproveBtn();
      initLazyMediaObserver();
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
        const m = (item.filename || item.id || '').match(/clip_(\\d+)/);
        return m ? parseInt(m[1], 10) : 999999;
      }

      const list = (approved || []).slice().sort((a, b) => getClipNum(a) - getClipNum(b));
      window.__approvedClips = list;
      const fingerprint = list.map(a => `${a.id}_${a.filename}_${a.size_mb}_${a.title}_${a.hashtags}_${(a.description || '').length}_${a.poster}`).join('|');
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
        const descText = a.description || '';
        return `
          <div id="card_${a.id}" class="bg-zinc-950 border border-emerald-950/60 hover:border-emerald-700/60 rounded-2xl p-4 shadow-xl flex flex-col space-y-3.5 transition-all">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-emerald-300 font-mono">${a.filename} (${a.size_mb} MB)</span>
              <span class="text-[10px] bg-emerald-950 text-emerald-400 px-2 py-0.5 rounded border border-emerald-800 font-semibold">Final Clip</span>
            </div>

            <div class="relative w-full aspect-[9/16] max-h-[360px] bg-black rounded-xl overflow-hidden border border-zinc-800 flex items-center justify-center">
              <video controls playsinline preload="none" ${posterAttr} class="w-full h-full object-contain lazy-media" data-src="/media/final/${a.filename}#t=0.001">
              </video>
            </div>

            <!-- Dynamic Metadata Studio Box -->
            <div class="bg-zinc-900/90 border border-zinc-800/80 rounded-xl p-3.5 text-xs space-y-3">
              <!-- Viral Title & Live YouTube Regen Button -->
              <div>
                <div class="flex items-center justify-between mb-1">
                  <label class="text-[10px] font-bold text-zinc-300 uppercase tracking-wide flex items-center space-x-1">
                    <i class="fa-solid fa-heading text-indigo-400"></i>
                    <span>Viral Title</span>
                  </label>
                  <button id="regen_btn_${a.id}" onclick="regenerateFinalClipMetadata('${a.filename}', '${a.id}', '${escapeHtml(a.hook || '')}', '${escapeHtml(a.timestamps || '')}')" 
                    class="text-[11px] px-2.5 py-1 rounded-lg bg-indigo-950/80 hover:bg-indigo-900/90 text-indigo-300 hover:text-white font-bold transition-all border border-indigo-700/60 flex items-center space-x-1 cursor-pointer shadow-sm" title="Research YouTube live autocomplete & regenerate CTR title, keywords, and description">
                    <i class="fa-solid fa-wand-magic-sparkles text-[10px] text-indigo-400"></i>
                    <span>Regen SEO Metadata</span>
                  </button>
                </div>
                <input type="text" id="final_title_${a.id}" value="${escapeHtml(a.title)}" 
                  class="w-full bg-zinc-950 border border-zinc-800 focus:border-indigo-500 rounded-lg px-2.5 py-1.5 text-xs font-bold text-white shadow-inner">
              </div>

              <!-- SEO Description Box with Keywords in First 200 Characters -->
              <div>
                <div class="flex items-center justify-between mb-1">
                  <div class="flex items-center space-x-1.5">
                    <span class="text-[10px] font-bold text-emerald-400 uppercase tracking-wide flex items-center">
                      <i class="fa-solid fa-bolt text-emerald-400 mr-1"></i>
                      <span>Description (Keywords in First 200 Chars)</span>
                    </span>
                    <span class="text-[9px] bg-emerald-950/80 text-emerald-300 border border-emerald-800 px-1.5 py-0.5 rounded font-mono font-bold">SEO Indexed</span>
                  </div>
                  <span id="desc_len_${a.id}" class="text-[10px] font-mono text-zinc-500">${descText.length} chars</span>
                </div>
                <textarea id="final_desc_${a.id}" rows="4" 
                  oninput="updateFinalDescLen('${a.id}')"
                  class="w-full bg-zinc-950 border border-zinc-800 focus:border-emerald-500 rounded-lg p-2.5 text-[11px] text-zinc-200 leading-relaxed font-sans shadow-inner" 
                  placeholder="SEO description with keywords in the first 200 characters...">${escapeHtml(descText)}</textarea>
              </div>

              <!-- Hashtags -->
              <div>
                <label class="text-[10px] font-bold text-zinc-400 uppercase tracking-wide mb-1 block">5 Niche Trending Hashtags</label>
                <input type="text" id="final_tags_${a.id}" value="${escapeHtml(a.hashtags || '')}" 
                  class="w-full bg-zinc-950 border border-zinc-800 focus:border-indigo-500 rounded-lg px-2.5 py-1.5 text-xs text-indigo-300 font-mono shadow-inner">
              </div>

              ${a.hook ? `<div class="text-[11px] text-zinc-400 italic pt-1 border-t border-zinc-800"><b class="text-zinc-500 not-italic">Opening Hook:</b> "${escapeHtml(a.hook)}"</div>` : ''}
              <div class="text-[10px] text-zinc-500 font-mono pt-0.5">${escapeHtml(a.timestamps || '1080x1920 60 FPS')}</div>
            </div>

            <!-- Action Buttons -->
            <div class="flex flex-col space-y-2 pt-1">
              <button onclick="publishFinalClipWithCurrentData('${a.filename}', '${a.id}', '${escapeHtml(a.hook || '')}', '${escapeHtml(a.timestamps || '')}')" 
                class="w-full bg-gradient-to-r from-red-600 via-rose-600 to-red-600 hover:from-red-500 hover:to-rose-500 text-white text-xs py-2.5 rounded-xl font-bold shadow-lg shadow-red-600/30 flex items-center justify-center space-x-2 transition-all active:scale-[0.98] cursor-pointer">
                <i class="fa-brands fa-youtube text-sm"></i>
                <span>Publish to YouTube Studio</span>
              </button>
              <div class="flex space-x-2">
                <button onclick="saveFinalClipMetadata('${a.filename}', '${a.id}', '${escapeHtml(a.hook || '')}', '${escapeHtml(a.timestamps || '')}')"
                  class="flex-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 hover:text-white text-xs py-2 rounded-lg font-semibold transition-all flex items-center justify-center space-x-1.5 cursor-pointer shadow">
                  <i class="fa-solid fa-floppy-disk text-indigo-400"></i>
                  <span>Save Metadata</span>
                </button>
                <a href="/media/final/${a.filename}" download="${a.filename}" 
                  class="flex-1 text-center bg-zinc-800 hover:bg-zinc-700 text-white text-xs py-2 rounded-lg font-semibold transition-all flex items-center justify-center space-x-1">
                  <i class="fa-solid fa-download mr-1"></i>
                  <span>Download MP4</span>
                </a>
                <button onclick="copyFullMetadata('${a.id}')"
                  class="px-3 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs py-2 rounded-lg transition-all cursor-pointer" title="Copy Title, Description & Hashtags">
                  <i class="fa-regular fa-copy"></i>
                </button>
              </div>
            </div>
          </div>
        `;
      }).join('');

      initLazyMediaObserver();
    }

    const finalClipVariations = {};

    function updateFinalDescLen(cardId) {
      const descEl = document.getElementById('final_desc_' + cardId);
      const lenEl = document.getElementById('desc_len_' + cardId);
      if (descEl && lenEl) {
        lenEl.textContent = `${descEl.value.length} chars`;
      }
    }

    async function regenerateFinalClipMetadata(filename, cardId, hook, timestamps) {
      const btn = document.getElementById('regen_btn_' + cardId);
      const titleInput = document.getElementById('final_title_' + cardId);
      const descInput = document.getElementById('final_desc_' + cardId);
      const tagsInput = document.getElementById('final_tags_' + cardId);

      finalClipVariations[filename] = (finalClipVariations[filename] || 0) + 1;

      if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin text-[10px]"></i><span>Researching...</span>';
      }

      try {
        const res = await fetch('/api/regenerate-metadata', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            is_final: true,
            filename: filename,
            id: cardId,
            hook: hook,
            text: hook,
            timestamps: timestamps,
            variation: finalClipVariations[filename],
            video_title: window.__sourceTitle || ''
          })
        });
        const data = await res.json();
        if (res.ok) {
          if (titleInput) titleInput.value = data.title;
          if (descInput) descInput.value = data.description;
          if (tagsInput) tagsInput.value = data.hashtags;
          updateFinalDescLen(cardId);

          const card = document.getElementById('card_' + cardId);
          if (card) {
            card.classList.add('ring-2', 'ring-emerald-500/60');
            setTimeout(() => card.classList.remove('ring-2', 'ring-emerald-500/60'), 1500);
          }

          if (btn) {
            btn.innerHTML = '<i class="fa-solid fa-check text-emerald-400 text-[10px]"></i><span>Updated ✓</span>';
            setTimeout(() => {
              btn.disabled = false;
              btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles text-[10px] text-indigo-400"></i><span>Regen SEO Metadata</span>';
            }, 1500);
          }
        } else {
          alert('Failed to regenerate metadata: ' + (data.detail || 'Server error'));
          if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles text-[10px] text-indigo-400"></i><span>Regen SEO Metadata</span>';
          }
        }
      } catch (err) {
        alert('Error connecting to YouTube research endpoint: ' + err.message);
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles text-[10px] text-indigo-400"></i><span>Regen SEO Metadata</span>';
        }
      }
    }

    async function saveFinalClipMetadata(filename, cardId, hook, timestamps) {
      const titleInput = document.getElementById('final_title_' + cardId);
      const descInput = document.getElementById('final_desc_' + cardId);
      const tagsInput = document.getElementById('final_tags_' + cardId);

      const title = titleInput ? titleInput.value : '';
      const desc = descInput ? descInput.value : '';
      const tags = tagsInput ? tagsInput.value : '';

      try {
        const res = await fetch('/api/save-clip-metadata', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            filename: filename,
            title: title,
            description: desc,
            hashtags: tags,
            hook: hook,
            timestamps: timestamps
          })
        });
        const data = await res.json();
        if (res.ok && data.success) {
          alert(`✓ Saved updated metadata for ${filename}!`);
        } else {
          alert('Failed to save metadata: ' + (data.error || 'Server error'));
        }
      } catch (e) {
        alert('Network error saving metadata: ' + e.message);
      }
    }

    function copyFullMetadata(cardId) {
      const title = (document.getElementById('final_title_' + cardId) || {}).value || '';
      const desc = (document.getElementById('final_desc_' + cardId) || {}).value || '';
      const tags = (document.getElementById('final_tags_' + cardId) || {}).value || '';

      const fullText = `TITLE:\n${title}\n\nDESCRIPTION:\n${desc}\n\nHASHTAGS:\n${tags}`;
      navigator.clipboard.writeText(fullText);
      alert('✓ Copied Title, SEO Description & Hashtags to clipboard!');
    }

    function publishFinalClipWithCurrentData(filename, cardId, hook, timestamps) {
      const title = (document.getElementById('final_title_' + cardId) || {}).value || '';
      const desc = (document.getElementById('final_desc_' + cardId) || {}).value || '';
      const tags = (document.getElementById('final_tags_' + cardId) || {}).value || '';

      openPublishModalWithData(filename, title, desc, tags, hook, 'final', timestamps);
    }

    let lazyMediaObserver = null;
    function initLazyMediaObserver() {
      if (!('IntersectionObserver' in window)) {
        document.querySelectorAll('video.lazy-media:not([data-loaded])').forEach(v => {
          const src = v.getAttribute('data-src');
          if (src) {
            v.innerHTML = `<source src="${src}" type="video/mp4">`;
            v.setAttribute('data-loaded', 'true');
          }
        });
        return;
      }

      if (lazyMediaObserver) {
        lazyMediaObserver.disconnect();
      }

      lazyMediaObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
          if (entry.isIntersecting) {
            const video = entry.target;
            if (!video.hasAttribute('data-loaded')) {
              const src = video.getAttribute('data-src');
              if (src) {
                video.innerHTML = `<source src="${src}" type="video/mp4">`;
                video.setAttribute('data-loaded', 'true');
              }
            }
          }
        });
      }, { rootMargin: '150px 0px' });

      document.querySelectorAll('video.lazy-media:not([data-loaded])').forEach(el => {
        lazyMediaObserver.observe(el);
      });
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
    checkYouTubeStatus();
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
