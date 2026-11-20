# 🎬 AutoClip-AI: Local AI Video Clipper & Shorts Studio

Autonomous, 100% private, local video clipper that converts long videos, podcasts, and livestreams into viral 9:16 vertical Shorts and Reels with AI face tracking, karaoke subtitles, and high-retention hook detection.

---

## ⚡ Key Features

- 🎯 **MediaPipe AI Face Tracking**: Dynamically tracks speakers and centers active subjects from 16:9 widescreen to 9:16 vertical using smooth Kalman-filtered framing.
- 🗣️ **Whisper AI Karaoke Subtitles**: Generates precise word-by-word synchronized animated subtitles.
- 🧠 **Intelligent Hook Detector**: Analyzes speech transcripts for emotional peaks, curiosity gaps, and cliffhangers to extract viral 25–55 second segments.
- 🏷️ **Viral Metadata Generator**: Automatically creates high-CTR titles and 5 niche-targeted hashtags (`#Shorts`, `#Gaming`, etc.).
- 💻 **Interactive Web Studio UI**: Built-in dashboard (`http://localhost:8888`) with range scrubbing, real-time clipping progress, draft video previews, and one-click approvals.
- 🔒 **100% Local & Free**: Runs entirely on your own GPU/CPU without expensive API fees or cloud subscriptions.

---

## 🚀 Quick Start (One-Click Launch)

If you are on Windows and have completed the one-time setup below, you can launch the studio anytime by simply double-clicking:
- **`run.bat`** or **`launch_ui.bat`**

This will automatically launch the backend server and open the web studio at **http://localhost:8888** in your browser.

---

## 📦 Installation & Setup

### 🪟 Windows Setup

#### Step 1: Install Python
Download and install [Python 3.10+](https://www.python.org/downloads/).  
⚠️ **Important**: Make sure to check the box **"Add Python to PATH"** during installation.

#### Step 2: Install FFmpeg
Open **Command Prompt** (cmd) and run:
```cmd
winget install Gyan.FFmpeg
```
*(Alternatively, install FFmpeg via Chocolatey or download it from [ffmpeg.org](https://ffmpeg.org/) and add it to your system PATH).*

#### Step 3: Clone the Repository
```cmd
git clone https://github.com/rahulpradhan5/AutoClip-AI.git
cd AutoClip-AI
```

#### Step 4: Create Virtual Environment & Install Dependencies
```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

#### Step 5: Launch the Studio
Double-click **`run.bat`** (or **`launch_ui.bat`**), or run:
```cmd
python app.py
```
Open **http://localhost:8888** in your browser.

---

### 🍎 macOS Setup

#### Step 1: Install Dependencies via Homebrew
Open **Terminal** and run:
```bash
brew install ffmpeg python
```

#### Step 2: Clone the Repository
```bash
git clone https://github.com/rahulpradhan5/AutoClip-AI.git
cd AutoClip-AI
```

#### Step 3: Create Virtual Environment & Install Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### Step 4: Launch the Studio
```bash
python3 app.py
```
Open **http://localhost:8888** in your browser.

---

### 🐧 Linux (Ubuntu / Debian / Fedora)

#### Step 1: Install System Dependencies
Open **Terminal** and run:

**Ubuntu / Debian:**
```bash
sudo apt update && sudo apt install -y ffmpeg python3-venv python3-pip git
```

**Fedora:**
```bash
sudo dnf install -y ffmpeg python3 python3-pip git
```

#### Step 2: Clone the Repository
```bash
git clone https://github.com/rahulpradhan5/AutoClip-AI.git
cd AutoClip-AI
```

#### Step 3: Create Virtual Environment & Install Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### Step 4: Launch the Studio
```bash
python3 app.py
```
Open **http://localhost:8888** in your browser.

---

## 📂 Project Architecture & File Guide

```text
AutoClip-AI/
├── app.py                     # Web Studio server & interactive UI
├── run.bat                    # One-click Windows launcher (starts app + browser)
├── launch_ui.bat              # Alternate one-click Windows launcher
├── requirements.txt           # Python library dependencies
├── scripts/
│   ├── detect_hooks.py        # Hook tension & curiosity gap detection
│   ├── smart_crop.py          # MediaPipe face tracking & 9:16 vertical crop
│   ├── render_captions.py     # Karaoke caption burning & 60 FPS video render
│   ├── generate_metadata.py   # AI title & 5 niche hashtag generator
│   ├── transcribe.py          # Whisper AI audio transcription
│   ├── ingest.py              # Video ingestion & stream downloader
│   ├── extract_stream_section.py # Fast stream cutter
│   ├── search_gta_hooks.py    # Gaming dialogue & action hook analyzer
│   └── find_action_hooks.py   # Visual/audio excitement spike finder
├── temp_work/                 # Intermediate processing scratchpad (empty in git)
├── draft_clips/               # Rendered review drafts with UI previews (empty in git)
└── final_clips/               # Approved 9:16 clips ready for publishing (empty in git)
```

### 🛠️ What Each File Does:

#### 🚀 One-Click Launchers & Core Server
- **`run.bat`**: The fastest way to start on Windows. Automatically opens `http://localhost:8888` in your default browser and runs the Python server inside `.venv`.
- **`launch_ui.bat`**: Windows batch helper that displays startup logs and launches the browser UI.
- **`app.py`**: The local web studio backend. Serves the interactive user interface, live clipping progress meters, video player with scrub controls, and approval workflow.
- **`requirements.txt`**: Lists all required Python packages (`faster-whisper`, `mediapipe`, `opencv-python-headless`, `yt-dlp`, `ctranslate2`, `tqdm`).

#### 🧠 AI Processing Scripts (`scripts/`)
- **`scripts/detect_hooks.py`**: Scans the transcribed text for high-retention moments using NLP pattern matching (curiosity gaps, question hooks, storytelling build-ups, and punchlines).
- **`scripts/smart_crop.py`**: Uses Google MediaPipe Face Detection to locate subjects in 16:9 widescreen footage and applies smooth horizontal camera panning to keep the speaker centered in 9:16.
- **`scripts/render_captions.py`**: Burns word-by-word animated karaoke subtitles into the video with custom font styling, high-contrast outlines, and exports smooth 60 FPS vertical video.
- **`scripts/generate_metadata.py`**: Generates high-click-through-rate titles and 5 targeted hashtags for YouTube Shorts, TikTok, and Instagram Reels.
- **`scripts/transcribe.py`**: Runs OpenAI's Whisper model locally via CTranslate2 for ultra-fast, word-level audio transcription without cloud latency.
- **`scripts/ingest.py`**: Downloads and extracts streams from YouTube or prepares local video files for processing.

#### 📁 Storage Directories
- **`draft_clips/`**: Where freshly generated clips are saved. The UI shows preview cards, video loaders, and progress bars here for review.
- **`final_clips/`**: Approved clips are moved here in clean ascending order, accompanied by `.txt` files containing your ready-to-copy titles and hashtags.
- **`temp_work/`**: Isolated scratch directory for audio extraction, timestamp caches, and face-tracking coordinates. Automatically cleaned up.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE) - free to use, modify, and distribute.
