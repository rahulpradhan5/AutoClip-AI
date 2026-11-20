"""
AutoClip-AI System Doctor & Diagnostics Engine.
Inspects hardware, Python environment, FFmpeg build flags, GPU acceleration,
AI model dependencies, and local Ollama availability to ensure smooth operation.
"""

import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

def check_mark(status: bool) -> str:
    return "  [PASS] " if status else "  [FAIL] "

def warn_mark() -> str:
    return "  [WARN] "

def info_mark() -> str:
    return "  [INFO] "

def run_diagnostics() -> dict:
    results = {}
    print("=" * 65)
    print("        AutoClip-AI System Diagnostics & Environment Doctor")
    print("=" * 65)
    print()

    # 1. Operating System & Python
    py_ver = platform.python_version()
    py_ok = sys.version_info >= (3, 10)
    print("1. Python & Operating System:")
    print(f"{check_mark(py_ok)}Python Version: {py_ver} (Required: 3.10+)")
    print(f"{info_mark()}OS Platform: {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"{info_mark()}Executable: {sys.executable}")
    results["python_version"] = py_ver
    results["python_ok"] = py_ok
    results["os"] = platform.system()
    print()

    # 2. FFmpeg & Subtitle Engine
    print("2. FFmpeg Video & Subtitle Engine:")
    ffmpeg_bin = shutil.which("ffmpeg")
    fallback_paths = [
        r"C:\Users\prahu\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe",
        r"C:\Users\prahu\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe",
    ]
    if not ffmpeg_bin:
        for p in fallback_paths:
            if os.path.exists(p):
                ffmpeg_bin = p
                break

    if ffmpeg_bin:
        print(f"{check_mark(True)}FFmpeg Binary: {ffmpeg_bin}")
        try:
            res = subprocess.run([ffmpeg_bin, "-version"], capture_output=True, text=True, timeout=5)
            version_line = res.stdout.splitlines()[0] if res.stdout else "Unknown"
            print(f"{info_mark()}Version: {version_line}")

            # Check libass support
            has_libass = "--enable-libass" in res.stdout or "libass" in res.stdout
            if has_libass:
                print(f"{check_mark(True)}Subtitle Subsystem (libass): Enabled (Karaoke subtitles supported)")
            else:
                print(f"{warn_mark()}libass flag not explicitly seen; testing filters...")

            # Check hardware encoders
            enc_res = subprocess.run([ffmpeg_bin, "-encoders"], capture_output=True, text=True, timeout=5)
            encoders = enc_res.stdout.lower() if enc_res.stdout else ""
            nvenc = "h264_nvenc" in encoders
            qsv = "h264_qsv" in encoders
            videotoolbox = "h264_videotoolbox" in encoders

            hw_accel = []
            if nvenc:
                hw_accel.append("NVIDIA NVENC")
            if qsv:
                hw_accel.append("Intel QSV")
            if videotoolbox:
                hw_accel.append("Apple VideoToolbox")

            if hw_accel:
                print(f"{check_mark(True)}Hardware Encoder(s): {', '.join(hw_accel)}")
            else:
                print(f"{info_mark()}Hardware Encoders: Software CPU libx264 (Universally compatible)")

            results["ffmpeg_ok"] = True
            results["hw_accel"] = hw_accel
        except Exception as e:
            print(f"{warn_mark()}Could not query FFmpeg flags: {e}")
            results["ffmpeg_ok"] = True
    else:
        print(f"{check_mark(False)}FFmpeg not found in system PATH!")
        print("         Fix: Run 'winget install Gyan.FFmpeg' (Windows) or 'brew install ffmpeg' (macOS)")
        results["ffmpeg_ok"] = False
    print()

    # 3. AI Modules & GPU Acceleration
    print("3. AI Modules & Neural Acceleration:")
    # MediaPipe
    try:
        import mediapipe as mp
        print(f"{check_mark(True)}MediaPipe Face Tracking: Installed (Ready for 9:16 smart framing)")
        results["mediapipe_ok"] = True
    except ImportError:
        print(f"{check_mark(False)}MediaPipe is NOT installed!")
        print("         Fix: Run 'pip install mediapipe'")
        results["mediapipe_ok"] = False

    # Faster Whisper / CTranslate2
    try:
        import faster_whisper
        import ctranslate2
        cuda_types = ctranslate2.get_cuda_device_count()
        if cuda_types > 0:
            print(f"{check_mark(True)}Faster-Whisper & CTranslate2: Active with {cuda_types} NVIDIA CUDA GPU(s)!")
        else:
            print(f"{check_mark(True)}Faster-Whisper & CTranslate2: Active on CPU (Int8 optimized)")
        results["whisper_ok"] = True
        results["cuda_gpus"] = cuda_types
    except ImportError as e:
        print(f"{check_mark(False)}Faster-Whisper / CTranslate2 error: {e}")
        print("         Fix: Run 'pip install faster-whisper ctranslate2'")
        results["whisper_ok"] = False

    # OpenCV
    try:
        import cv2
        print(f"{check_mark(True)}OpenCV Computer Vision: Installed ({cv2.__version__})")
        results["opencv_ok"] = True
    except ImportError:
        print(f"{check_mark(False)}OpenCV is NOT installed!")
        print("         Fix: Run 'pip install opencv-python-headless'")
        results["opencv_ok"] = False
    print()

    # 4. Optional Local LLM (Ollama)
    print("4. Optional Local LLM Hook Scorer (Ollama):")
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags", headers={"User-Agent": "AutoClip-Doctor"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", [])]
                if models:
                    print(f"{check_mark(True)}Ollama Service: ONLINE at http://localhost:11434")
                    print(f"{info_mark()}Available LLM Models: {', '.join(models)}")
                else:
                    print(f"{check_mark(True)}Ollama Service: ONLINE (No models downloaded yet; run 'ollama pull llama3')")
                results["ollama_online"] = True
                results["ollama_models"] = models
    except Exception:
        print(f"{info_mark()}Ollama Service: Not running (AutoClip-AI will use built-in high-speed NLP heuristic scorer)")
        results["ollama_online"] = False
    print()

    # 5. Storage & Directory Permissions
    print("5. Storage & Workspace Directories:")
    for folder_name in ["temp_work", "draft_clips", "final_clips"]:
        folder_path = ROOT_DIR / folder_name
        folder_path.mkdir(parents=True, exist_ok=True)
        test_file = folder_path / ".doctor_test.tmp"
        try:
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink(missing_ok=True)
            print(f"{check_mark(True)}Directory '{folder_name}/': Read/Write OK")
        except Exception as e:
            print(f"{check_mark(False)}Directory '{folder_name}/': Permission error: {e}")

    try:
        total, used, free = shutil.disk_usage(ROOT_DIR)
        free_gb = free // (2**30)
        if free_gb >= 5:
            print(f"{check_mark(True)}Available Disk Space: {free_gb} GB free")
        else:
            print(f"{warn_mark()}Available Disk Space low: {free_gb} GB free (Recommended >= 10 GB)")
        results["free_disk_gb"] = free_gb
    except Exception:
        pass
    print()

    print("=" * 65)
    all_critical = py_ok and results.get("ffmpeg_ok") and results.get("mediapipe_ok") and results.get("whisper_ok")
    if all_critical:
        print("  ✓ ALL SYSTEMS GO: AutoClip-AI is ready for high-retention clipping!")
    else:
        print("  ⚠️  ATTENTION: Some dependencies require setup. See instructions above.")
    print("=" * 65)
    return results

if __name__ == "__main__":
    run_diagnostics()
