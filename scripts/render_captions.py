"""
Caption rendering module for Universal Video Clipper.
Generates viral karaoke-style .ass subtitles (2-4 words per phrase, uppercase, Arial Black,
thick black stroke, drop shadow, active word highlighted in #FFD700 yellow, MarginV=280 safe margin)
and burns them into 1080x1920 vertical video using FFmpeg.
"""

import argparse
import json
import os
import re
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


def format_ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs == 100:
        secs += 1
        cs = 0
    return f"{hrs:d}:{mins:02d}:{secs:02d}.{cs:02d}"


THEME_STYLES = {
    "hormozi": {
        "highlight_ass": r"{\c&H00D7FF&}",  # #FFD700 Gold / Yellow
        "default_ass": r"{\c&HFFFFFF&}",    # Pure White
        "primary_bgr": "&H00FFFFFF",
        "secondary_bgr": "&H0000D7FF",
        "font_size": 78,
        "outline": 6,
        "shadow": 3,
        "uppercase": True,
    },
    "mrbeast": {
        "highlight_ass": r"{\c&H00FF33&}",  # #33FF00 Electric Lime Green
        "default_ass": r"{\c&HFFFFFF&}",    # Pure White
        "primary_bgr": "&H00FFFFFF",
        "secondary_bgr": "&H0033FF00",
        "font_size": 80,
        "outline": 6,
        "shadow": 4,
        "uppercase": True,
    },
    "cyber": {
        "highlight_ass": r"{\c&HFFFF00&}",  # #00FFFF Neon Cyan
        "default_ass": r"{\c&HFFFFFF&}",    # Pure White
        "primary_bgr": "&H00FFFFFF",
        "secondary_bgr": "&H00FFFF00",
        "font_size": 76,
        "outline": 6,
        "shadow": 3,
        "uppercase": True,
    },
    "crimson": {
        "highlight_ass": r"{\c&H0000FF&}",  # #FF0000 Crimson Red
        "default_ass": r"{\c&HFFFFFF&}",    # Pure White
        "primary_bgr": "&H00FFFFFF",
        "secondary_bgr": "&H000000FF",
        "font_size": 78,
        "outline": 6,
        "shadow": 3,
        "uppercase": True,
    },
    "classic_white": {
        "highlight_ass": r"{\c&HFFFFFF&}",  # Pure White
        "default_ass": r"{\c&HE0E0E0&}",    # Soft Platinum
        "primary_bgr": "&H00FFFFFF",
        "secondary_bgr": "&H00E0E0E0",
        "font_size": 72,
        "outline": 4,
        "shadow": 2,
        "uppercase": False,
    },
}


def build_ass_header(theme_name: str = "hormozi") -> str:
    cfg = THEME_STYLES.get(theme_name.lower(), THEME_STYLES["hormozi"])
    fsize = cfg["font_size"]
    primary = cfg["primary_bgr"]
    secondary = cfg["secondary_bgr"]
    outline = cfg["outline"]
    shadow = cfg["shadow"]

    return f"""[Script Info]
Title: AutoClip-AI Subtitles ({theme_name})
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ViralKaraoke,Arial Black,{fsize},{primary},{secondary},&H00000000,&H90000000,-1,0,0,0,100,100,2,0,1,{outline},{shadow},2,60,60,280,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def chunk_words_into_phrases(words: list[dict], min_words: int = 2, max_words: int = 4) -> list[list[dict]]:
    phrases = []
    current_phrase = []

    for w in words:
        current_phrase.append(w)
        word_text = w.get("word", "")

        # Split on sentence ends, punctuation or when max_words reached
        has_break_punct = bool(re.search(r"[.!?,\n]$", word_text))
        if len(current_phrase) >= max_words or (len(current_phrase) >= min_words and has_break_punct):
            phrases.append(current_phrase)
            current_phrase = []

    if current_phrase:
        if phrases and len(current_phrase) < min_words:
            phrases[-1].extend(current_phrase)
        else:
            phrases.append(current_phrase)

    return phrases


def generate_karaoke_ass(
    transcript_path: str,
    output_ass_path: str,
    clip_start_sec: float = 0.0,
    clip_end_sec: float | None = None,
    theme: str = "hormozi",
) -> str:
    with open(transcript_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    cfg = THEME_STYLES.get(theme.lower(), THEME_STYLES["hormozi"])
    highlight_tag = cfg["highlight_ass"]
    default_tag = cfg["default_ass"]
    uppercase = cfg["uppercase"]

    raw_words = []
    for seg in data.get("segments", []):
        if "words" in seg and seg["words"]:
            for w in seg["words"]:
                raw_words.append(w)
        else:
            # Fallback if segment didn't have word timestamps
            seg_start = seg["start"]
            seg_end = seg["end"]
            tokens = seg["text"].split()
            if tokens:
                duration_per_token = (seg_end - seg_start) / len(tokens)
                for i, token in enumerate(tokens):
                    raw_words.append(
                        {
                            "word": token,
                            "start": seg_start + i * duration_per_token,
                            "end": seg_start + (i + 1) * duration_per_token,
                        }
                    )

    # Filter words within the clip window and make relative to clip_start_sec
    filtered_words = []
    for w in raw_words:
        w_text = w.get("word", "").strip()
        # Filter out sound effects, speaker markers and empty words
        if not w_text or w_text in [">>", "<<", ">", "<"] or (w_text.startswith("[") and w_text.endswith("]")):
            continue

        w_start = w["start"]
        w_end = w["end"]
        if clip_end_sec is not None and w_start >= clip_end_sec:
            continue
        if w_end <= clip_start_sec:
            continue

        rel_start = max(0.0, w_start - clip_start_sec)
        rel_end = (w_end - clip_start_sec) if clip_end_sec is None else min(clip_end_sec - clip_start_sec, w_end - clip_start_sec)

        clean_token = re.sub(r"[^\w\s\'\?!,\.-]", "", w_text).strip()
        if uppercase:
            clean_token = clean_token.upper()
        if not clean_token:
            continue

        if rel_end > rel_start:
            filtered_words.append(
                {
                    "word": clean_token,
                    "start": rel_start,
                    "end": rel_end,
                }
            )

    phrases = chunk_words_into_phrases(filtered_words, min_words=2, max_words=4)

    events = []

    for phrase in phrases:
        p_start = phrase[0]["start"]
        p_end = phrase[-1]["end"]

        for active_idx, active_word in enumerate(phrase):
            w_start = active_word["start"]
            w_end = active_word["end"]

            # If there's a gap between words in phrase, extend slightly
            if active_idx < len(phrase) - 1:
                next_start = phrase[active_idx + 1]["start"]
                if next_start > w_end:
                    w_end = next_start
            else:
                w_end = max(w_end, p_end)

            # Build line text with active word highlighted
            line_parts = []
            for idx, w in enumerate(phrase):
                clean_word = re.sub(r"[^\w\s\'\?!,\.-]", "", w["word"]).strip()
                if not clean_word:
                    continue
                if idx == active_idx:
                    line_parts.append(f"{highlight_tag}{clean_word}{default_tag}")
                else:
                    line_parts.append(f"{default_tag}{clean_word}")

            line_text = " ".join(line_parts)
            start_str = format_ass_time(w_start)
            end_str = format_ass_time(w_end)

            event_line = f"Dialogue: 0,{start_str},{end_str},ViralKaraoke,,0,0,0,,{line_text}"
            events.append(event_line)

    ass_content = build_ass_header(theme) + "\n".join(events) + "\n"

    os.makedirs(os.path.dirname(os.path.abspath(output_ass_path)), exist_ok=True)
    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    print(f"[render_captions] Generated .ass subtitles with {len(events)} karaoke events (theme: {theme}) at {output_ass_path}")
    return output_ass_path


def burn_captions_to_video(
    video_path: str,
    ass_path: str,
    output_path: str,
    crf: int = 16,
    audio_bitrate: str = "256k",
    fps: int = 60,
) -> str:
    video_path = os.path.abspath(video_path)
    ass_path = os.path.abspath(ass_path)
    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Windows-safe path escaping for ffmpeg libass filter
    escaped_ass = ass_path.replace("\\", "/").replace(":", r"\:")

    # High-Definition Visual Pipeline:
    # 1. Burn viral karaoke ASS captions
    # 2. Unsharp mask (5x5 matrix, luma 0.8) to sharpen fine textures and facial edges
    # 3. Dynamic color & contrast equalizer (+14% saturation, +6% contrast, +1% brightness) to remove dullness
    filter_chain = f"ass='{escaped_ass}',unsharp=5:5:0.8:5:5:0.0,eq=contrast=1.06:saturation=1.14:brightness=0.01"

    cmd = [
        FFMPEG_PATH,
        "-y",
        "-i",
        video_path,
        "-vf",
        filter_chain,
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        output_path,
    ]

    print(f"[render_captions] Burning subtitles with FFmpeg into {output_path}...")
    subprocess.run(cmd, check=True)
    print(f"[render_captions] Render complete: {output_path} ({os.path.getsize(output_path)} bytes)")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Render viral karaoke subtitles and burn to vertical video.")
    parser.add_argument("--video", required=True, help="Cropped 1080x1920 video path")
    parser.add_argument("--transcript", required=True, help="Transcript JSON with word timestamps")
    parser.add_argument("--output", default="./final_clips/clip_final.mp4", help="Output MP4 file")
    parser.add_argument("--start", type=float, default=0.0, help="Clip start timestamp relative to source")
    parser.add_argument("--end", type=float, default=None, help="Clip end timestamp relative to source")
    parser.add_argument("--fps", type=int, default=60, help="Target export FPS (e.g., 60)")
    parser.add_argument("--theme", default="hormozi", choices=["hormozi", "mrbeast", "cyber", "crimson", "classic_white"], help="Subtitle color & font theme preset")
    parser.add_argument("--ass-output", default="./temp_work/subtitles.ass", help="Intermediary ASS path")
    args = parser.parse_args()

    ass_file = generate_karaoke_ass(
        transcript_path=args.transcript,
        output_ass_path=args.ass_output,
        clip_start_sec=args.start,
        clip_end_sec=args.end,
        theme=args.theme,
    )

    burn_captions_to_video(
        video_path=args.video,
        ass_path=ass_file,
        output_path=args.output,
        fps=args.fps,
    )


if __name__ == "__main__":
    main()
