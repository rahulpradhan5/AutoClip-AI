"""
Stream Section Extractor.
Uses yt-dlp to obtain direct stream URLs and FFmpeg to fetch precise time windows
from long livestreams without downloading the entire multi-gigabyte recording.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys


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


def get_stream_urls(youtube_url: str) -> tuple[str, str]:
    cmd = [
        YTDLP_PATH,
        "-g",
        "-f",
        "bestvideo[height<=720]+bestaudio/bestvideo+bestaudio/best",
        youtube_url,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    lines = [line.strip() for line in res.stdout.strip().split("\n") if line.strip()]
    if len(lines) >= 2:
        return lines[0], lines[1]
    elif len(lines) == 1:
        return lines[0], lines[0]
    raise RuntimeError(f"Unable to extract stream URLs: {res.stderr}")


def extract_section(video_url: str, audio_url: str, start_sec: float, duration_sec: float, output_path: str):
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    cmd = [
        FFMPEG_PATH,
        "-y",
        "-ss",
        str(start_sec),
        "-i",
        video_url,
        "-ss",
        str(start_sec),
        "-i",
        audio_url,
        "-t",
        str(duration_sec),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        output_path,
    ]
    print(f"[extract_section] Extracting {duration_sec}s from {start_sec}s to {output_path}...")
    subprocess.run(cmd, check=True)
    print(f"[extract_section] Saved {output_path} ({os.path.getsize(output_path)} bytes)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--start", type=float, default=600.0)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--output", default="temp_work/section_test.mp4")
    args = parser.parse_args()

    v_url, a_url = get_stream_urls(args.url)
    extract_section(v_url, a_url, args.start, args.duration, args.output)
