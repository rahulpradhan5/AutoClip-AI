# AutoClip-AI: Local Video Clipper & Shorts Studio

Autonomous, 100% private video clipper that converts long videos, livestreams, and podcasts into 9:16 vertical Shorts and Reels with AI face tracking, karaoke subtitles, and high-retention hook detection.

---

## Key Features

- **MediaPipe AI Face Tracking**: Smoothly tracks active subjects and crops 16:9 widescreen video into vertical 9:16.
- **Whisper AI Subtitles**: Word-by-word synchronized karaoke captions.
- **Intelligent Hook Detector**: Detects high-tension, high-curiosity 25–55 second clips.
- **Niche Hashtags & Titles**: Dynamic title and 5 niche-matched hashtag generator.
- **Web Studio UI**: Built-in interactive local dashboard (http://localhost:8888) with range scrubbing, auto-stopping previews, and bulk approvals.
- **100% Local & Free**: Runs on your own hardware without external API subscriptions.

---

## Installation & Setup

### Windows
1. Install Python 3.10+ and ensure Add to PATH is checked.
2. Install FFmpeg:
   `cmd
   winget install Gyan.FFmpeg
   `
3. Clone repository and install dependencies:
   `cmd
   git clone https://github.com/rahulpradhan5/AutoClip-AI.git
   cd AutoClip-AI
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   `
4. Run the Studio:
   Double-click 
un.bat or run:
   `cmd
   python app.py
   `
   Open http://localhost:8888 in your browser.

---

### macOS
1. Install Homebrew (if not already installed) and FFmpeg:
   `ash
   brew install ffmpeg python
   `
2. Clone repository and install dependencies:
   `ash
   git clone https://github.com/rahulpradhan5/AutoClip-AI.git
   cd AutoClip-AI
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   `
3. Run the Studio:
   `ash
   python3 app.py
   `
   Open http://localhost:8888 in your browser.

---

### Linux (Ubuntu / Debian / Fedora)
1. Install system dependencies:
   `ash
   sudo apt update && sudo apt install -y ffmpeg python3-venv python3-pip
   `
2. Clone repository and install dependencies:
   `ash
   git clone https://github.com/rahulpradhan5/AutoClip-AI.git
   cd AutoClip-AI
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   `
3. Run the Studio:
   `ash
   python3 app.py
   `
   Open http://localhost:8888 in your browser.

---

## Project Structure

`
AutoClip-AI/
├── app.py                     # Web Studio server & interactive UI
├── requirements.txt           # Project dependencies
├── run.bat                    # One-click Windows launcher
├── launch_ui.bat              # Browser launcher
├── scripts/
│   ├── detect_hooks.py        # Hook tension & curiosity gap detection
│   ├── smart_crop.py          # MediaPipe face tracking & 9:16 cropping
│   ├── render_captions.py     # Karaoke caption burning & 60 FPS grading
│   ├── generate_metadata.py   # Title & niche hashtag generator
│   └── transcribe.py          # Faster-Whisper audio transcription
├── temp_work/                 # Temporary working files (ignored by git)
├── draft_clips/               # Rendered draft review clips (ignored by git)
└── final_clips/               # Approved final clips (ignored by git)
`

---

## License

This project is licensed under the MIT License - see the LICENSE file for details.
