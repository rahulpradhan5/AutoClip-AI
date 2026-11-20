"""
Smart Framing Engine for Universal Video Clipper.
Production-grade 9:16 vertical video framing with:
1. Cinematic Full-Screen 9:16 Crop with MediaPipe face detection and gimbal-smooth EMA tracking.
2. Deadzone & scene-cut hysteresis to eliminate micro-jitter and glitching.
3. Smooth crossfade fallback to 1080x1920 blurred-background pillarbox when no subject is detected.
4. ZERO screen partitioning or arbitrary horizontal divider lines in standard / auto modes.
"""

import argparse
import os
import shutil
import subprocess
import sys
import cv2
import numpy as np


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


def render_blurred_pillarbox(frame: np.ndarray, target_w: int = 1080, target_h: int = 1920) -> np.ndarray:
    """
    Renders 16:9 video centered within 9:16 frame with darkened, blurred background.
    Preserves exact aspect ratio of the source video without distortion.
    """
    orig_h, orig_w = frame.shape[:2]

    # Background: Scale frame to cover full 1080x1920 canvas
    scale_bg = max(target_w / orig_w, target_h / orig_h)
    bg_w = int(round(orig_w * scale_bg))
    bg_h = int(round(orig_h * scale_bg))
    bg_resized = cv2.resize(frame, (bg_w, bg_h), interpolation=cv2.INTER_LINEAR)

    x_start = max(0, (bg_w - target_w) // 2)
    y_start = max(0, (bg_h - target_h) // 2)
    bg_cropped = bg_resized[y_start : y_start + target_h, x_start : x_start + target_w]

    # Two-pass Gaussian blur on downsampled buffer for performance & smooth aesthetic
    small_bg = cv2.resize(bg_cropped, (target_w // 4, target_h // 4), interpolation=cv2.INTER_LINEAR)
    blurred_small = cv2.GaussianBlur(small_bg, (31, 31), 0)
    blurred_bg = cv2.resize(blurred_small, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    blurred_bg = (blurred_bg.astype(np.float32) * 0.65).astype(np.uint8)

    # Foreground: Place full video centered vertically
    fg_w = target_w
    fg_h = int(round(orig_h * (target_w / orig_w)))
    fg_resized = cv2.resize(frame, (fg_w, fg_h), interpolation=cv2.INTER_AREA)

    y_fg_start = max(0, (target_h - fg_h) // 2)
    y_fg_end = min(target_h, y_fg_start + fg_h)
    actual_h = y_fg_end - y_fg_start

    blurred_bg[y_fg_start:y_fg_end, 0:target_w] = fg_resized[0:actual_h, 0:target_w]
    return blurred_bg


def is_scene_cut(prev_gray: np.ndarray | None, curr_gray: np.ndarray, threshold: float = 35.0) -> bool:
    """Detects hard scene cuts by computing mean absolute pixel difference."""
    if prev_gray is None:
        return False
    diff = cv2.absdiff(prev_gray, curr_gray)
    return float(np.mean(diff)) > threshold


def smart_crop_video(
    input_path: str,
    output_path: str,
    start_sec: float = 0.0,
    end_sec: float | None = None,
    ema_alpha: float = 0.05,
    no_face_timeout_sec: float = 1.5,
    mode: str = "auto",  # auto, crop-fullscreen, pillarbox
) -> str:
    input_path = os.path.abspath(input_path)
    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Source video not found: {input_path}")

    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError(f"Unable to open video: {input_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    start_frame = max(0, int(start_sec * fps))
    end_frame = total_frames if end_sec is None else min(total_frames, int(end_sec * fps))
    frames_to_process = max(1, end_frame - start_frame)
    clip_duration = frames_to_process / fps

    print(f"[smart_crop] Video: {orig_w}x{orig_h} @ {fps:.2f}fps")
    print(f"[smart_crop] Processing: {start_sec:.2f}s -> {end_frame/fps:.2f}s ({clip_duration:.2f}s, {frames_to_process} frames, mode={mode})")

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    target_w = 1080
    target_h = 1920

    # 9:16 crop width in source frame
    crop_w = int(round(orig_h * (9.0 / 16.0)))
    if crop_w % 2 != 0:
        crop_w -= 1
    crop_w = min(crop_w, orig_w)

    min_center_x = crop_w / 2.0
    max_center_x = orig_w - (crop_w / 2.0)
    current_center_x = orig_w / 2.0

    # Deadzone threshold: ignore tiny movements smaller than 2% of width to avoid micro-jitter
    deadzone = orig_w * 0.020

    # Transition state between crop (0.0) and blurred pillarbox (1.0)
    transition_state = 0.0
    transition_speed = 1.0 / max(1.0, 0.75 * fps)  # 0.75s smooth crossfade
    no_face_frames = 0
    consecutive_face_frames = 0
    max_no_face_frames = int(no_face_timeout_sec * fps)
    required_face_frames = int(0.4 * fps)  # Require 0.4s of confident detection to return from pillarbox

    prev_gray = None

    ffmpeg_cmd = [
        FFMPEG_PATH,
        "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{target_w}x{target_h}",
        "-pix_fmt", "bgr24",
        "-r", str(fps),
        "-i", "-",
        "-ss", str(start_sec),
        "-t", str(clip_duration),
        "-i", input_path,
        "-map", "0:v:0",
        "-map", "1:a:0?",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "16",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "256k",
        "-shortest",
        output_path,
    ]

    import mediapipe as mp
    mp_face_detection = mp.solutions.face_detection
    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    with mp_face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5) as face_detector:
        frame_idx = start_frame
        while frame_idx < end_frame:
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            curr_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # Downsampled cut detection for speed
            curr_small_gray = cv2.resize(curr_gray, (160, 90), interpolation=cv2.INTER_AREA)
            cut_detected = is_scene_cut(prev_gray, curr_small_gray)
            prev_gray = curr_small_gray

            detected_face_x = None

            if mode != "pillarbox":
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = face_detector.process(rgb_frame)

                if results.detections:
                    valid_faces = []
                    for det in results.detections:
                        bbox = det.location_data.relative_bounding_box
                        fw = int(bbox.width * orig_w)
                        fh = int(bbox.height * orig_h)
                        fx = int(bbox.xmin * orig_w)
                        fy = int(bbox.ymin * orig_h)
                        # Filter out tiny noise detections (< 5% frame height)
                        if fh >= orig_h * 0.05 and fw >= orig_w * 0.03:
                            cx = fx + fw / 2.0
                            valid_faces.append((cx, fw * fh))

                    if valid_faces:
                        # If multiple faces, prefer the one closest to current camera center (continuity)
                        closest = min(valid_faces, key=lambda f: abs(f[0] - current_center_x))
                        detected_face_x = closest[0]
                        consecutive_face_frames += 1
                        no_face_frames = 0
                    else:
                        consecutive_face_frames = 0
                        no_face_frames += 1
                else:
                    consecutive_face_frames = 0
                    no_face_frames += 1

            if mode == "pillarbox":
                final_frame = render_blurred_pillarbox(frame, target_w, target_h)
            else:
                # Mode is auto or crop-fullscreen
                if detected_face_x is not None:
                    target_center_x = np.clip(detected_face_x, min_center_x, max_center_x)

                    if cut_detected:
                        # Instant snap on hard scene cut
                        current_center_x = target_center_x
                    else:
                        # Deadzone check to eliminate jitter
                        diff = abs(target_center_x - current_center_x)
                        if diff > deadzone:
                            # Smooth exponential moving average
                            current_center_x = ema_alpha * target_center_x + (1.0 - ema_alpha) * current_center_x
                            current_center_x = np.clip(current_center_x, min_center_x, max_center_x)
                else:
                    # No face in this frame - maintain current position smoothly
                    if cut_detected:
                        current_center_x = orig_w / 2.0

                # Determine transition to/from blurred pillarbox
                if no_face_frames >= max_no_face_frames:
                    target_transition = 1.0  # Transition to blurred pillarbox
                elif consecutive_face_frames >= required_face_frames:
                    target_transition = 0.0  # Transition back to full 9:16 crop
                else:
                    # Maintain current state to prevent flickering
                    target_transition = 1.0 if transition_state > 0.5 else 0.0

                if transition_state < target_transition:
                    transition_state = min(target_transition, transition_state + transition_speed)
                elif transition_state > target_transition:
                    transition_state = max(target_transition, transition_state - transition_speed)

                # Render 9:16 Crop
                x1 = int(round(current_center_x - crop_w / 2.0))
                x1 = max(0, min(orig_w - crop_w, x1))
                crop_slice = frame[0:orig_h, x1 : x1 + crop_w]
                frame_crop = cv2.resize(crop_slice, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)

                if transition_state > 0.001:
                    frame_pillarbox = render_blurred_pillarbox(frame, target_w, target_h)
                    if transition_state >= 0.999:
                        final_frame = frame_pillarbox
                    else:
                        # Smooth alpha blend crossfade
                        final_frame = cv2.addWeighted(
                            frame_crop, 1.0 - transition_state, frame_pillarbox, transition_state, 0
                        )
                else:
                    final_frame = frame_crop

            try:
                proc.stdin.write(final_frame.tobytes())
            except (BrokenPipeError, OSError):
                break

            frame_idx += 1
            if (frame_idx - start_frame) % 150 == 0:
                pct = int(((frame_idx - start_frame) / frames_to_process) * 100)
                print(f"[smart_crop] Progress: {pct}%")

    cap.release()
    if proc.stdin:
        try:
            proc.stdin.close()
        except Exception:
            pass
    proc.wait()

    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg smart crop failed with exit code: {proc.returncode}")

    print(f"[smart_crop] Render complete: {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Smart Framing Engine - Clean 9:16 Crop & Blurred Pillarbox.")
    parser.add_argument("--input", required=True, help="Input video file path")
    parser.add_argument("--output", default="./temp_work/crop_segment.mp4", help="Output 9:16 video path")
    parser.add_argument("--start", type=float, default=0.0, help="Start time in seconds")
    parser.add_argument("--end", type=float, default=None, help="End time in seconds")
    parser.add_argument("--ema-alpha", type=float, default=0.05, help="EMA smoothing factor (0.03-0.08)")
    parser.add_argument("--no-face-timeout", type=float, default=1.5, help="Seconds without face before blurred pillarbox")
    parser.add_argument("--mode", default="auto", choices=["auto", "crop-fullscreen", "pillarbox"], help="Framing mode")
    args = parser.parse_args()

    smart_crop_video(
        args.input,
        args.output,
        start_sec=args.start,
        end_sec=args.end,
        ema_alpha=args.ema_alpha,
        no_face_timeout_sec=args.no_face_timeout,
        mode=args.mode,
    )


if __name__ == "__main__":
    main()
