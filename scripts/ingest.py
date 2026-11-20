"""
Ingestion module for Universal Video Clipper.
Downloads YouTube VODs / livestreams or processes local media files into a standard 1080p MP4 container.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


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


def get_media_info(filepath: str) -> dict:
    ffprobe_path = find_binary(
        "ffprobe",
        [
            r"C:\Users\prahu\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffprobe.exe",
            r"C:\Users\prahu\AppData\Local\Microsoft\WinGet\Links\ffprobe.exe",
        ],
    )
    cmd = [
        ffprobe_path,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        filepath,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def download_youtube(url: str, output_path: str, max_retries: int = 3) -> str:
    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    cmd = [
        YTDLP_PATH,
        "--format",
        "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "--merge-output-format",
        "mp4",
        "--extractor-args",
        "youtube:player_client=android,web",
        "--retries",
        "5",
        "--fragment-retries",
        "5",
        "--retry-sleep",
        "exp=1:5",
        "--ffmpeg-location",
        os.path.dirname(FFMPEG_PATH),
        "-o",
        output_path,
        "--force-overwrites",
        url,
    ]

    for attempt in range(1, max_retries + 1):
        print(f"[ingest] Downloading (attempt {attempt}/{max_retries}): {url}")
        res = subprocess.run(cmd)
        if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            print(f"[ingest] Successfully downloaded to {output_path}")
            return output_path
        time.sleep(2 * attempt)

    raise RuntimeError(f"Failed to download video from {url} after {max_retries} attempts.")


def process_local_file(input_path: str, output_path: str) -> str:
    input_path = os.path.abspath(input_path)
    output_path = os.path.abspath(output_path)

    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Source file not found: {input_path}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Remux or copy to standard MP4 container
    if input_path == output_path:
        return output_path

    cmd = [
        FFMPEG_PATH,
        "-y",
        "-i",
        input_path,
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-preset",
        "fast",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        output_path,
    ]
    print(f"[ingest] Remuxing local file: {input_path} -> {output_path}")
    subprocess.run(cmd, check=True)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Ingest video from YouTube or local path.")
    parser.add_argument("--input", required=True, help="YouTube URL or local video file path")
    parser.add_argument("--output", default="./temp_work/source.mp4", help="Destination MP4 path")
    args = parser.parse_args()

    input_val = args.input.strip()
    output_dest = os.path.abspath(args.output)

    is_url = input_val.startswith("http://") or input_val.startswith("https://") or "youtube.com" in input_val or "youtu.be" in input_val

    if is_url:
        result_file = download_youtube(input_val, output_dest)
    else:
        result_file = process_local_file(input_val, output_dest)

    info = get_media_info(result_file)
    duration = float(info.get("format", {}).get("duration", 0.0))
    video_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    width = video_stream.get("width")
    height = video_stream.get("height")

    manifest = {
        "status": "success",
        "filepath": result_file,
        "duration": duration,
        "resolution": f"{width}x{height}",
        "size_bytes": os.path.getsize(result_file),
    }

    manifest_path = os.path.join(os.path.dirname(result_file), "ingest_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"[ingest] Ingestion complete: {json.dumps(manifest, indent=2)}")


if __name__ == "__main__":
    main()
