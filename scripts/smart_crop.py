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

try:
    import cv2
    import numpy as np
except ModuleNotFoundError:
    _venv_site = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".venv", "Lib", "site-packages"))
    if os.path.exists(_venv_site) and _venv_site not in sys.path:
        sys.path.insert(0, _venv_site)
        import cv2
        import numpy as np
    else:
        raise


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


def render_streamer_split(
    frame: np.ndarray,
    face_box: tuple | None,
    target_w: int = 1080,
    target_h: int = 1920,
    top_ratio: float = 0.36
) -> np.ndarray:
    """
    Renders Instagram/TikTok style Streamer Split-Screen:
    Top section (36%): Streamer's facecam zoomed in, stabilized, and centered.
    Bottom section (64%): Main gameplay action.
    Separated by a modern accent divider.
    """
    orig_h, orig_w = frame.shape[:2]
    top_h = int(round(target_h * top_ratio))
    bot_h = target_h - top_h
    out_frame = np.zeros((target_h, target_w, 3), dtype=np.uint8)

    # 1. Top Section: Streamer Facecam
    if face_box is not None:
        fx, fy, fw, fh = face_box
        cx = fx + fw / 2.0
        cy = fy + fh / 2.0

        top_aspect = target_w / top_h
        crop_fh = fh * 2.2
        crop_fw = crop_fh * top_aspect

        crop_x1 = max(0, int(round(cx - crop_fw / 2.0)))
        crop_x2 = min(orig_w, int(round(cx + crop_fw / 2.0)))
        crop_y1 = max(0, int(round(cy - crop_fh / 2.0)))
        crop_y2 = min(orig_h, int(round(cy + crop_fh / 2.0)))

        if (crop_x2 - crop_x1) > 10 and (crop_y2 - crop_y1) > 10:
            face_crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]
            top_img = cv2.resize(face_crop, (target_w, top_h), interpolation=cv2.INTER_LANCZOS4)
        else:
            top_img = cv2.resize(frame, (target_w, top_h), interpolation=cv2.INTER_LINEAR)
    else:
        # Fallback: blurred duplicate of the upper frame
        small = cv2.resize(frame, (target_w // 4, top_h // 4), interpolation=cv2.INTER_LINEAR)
        blurred_small = cv2.GaussianBlur(small, (25, 25), 0)
        top_img = cv2.resize(blurred_small, (target_w, top_h), interpolation=cv2.INTER_LINEAR)
        top_img = (top_img.astype(np.float32) * 0.75).astype(np.uint8)

    out_frame[0:top_h, 0:target_w] = top_img

    # 2. Bottom Section: Gameplay
    bot_aspect = target_w / bot_h
    gp_crop_w = int(round(orig_h * bot_aspect))
    if gp_crop_w <= orig_w:
        gp_x1 = (orig_w - gp_crop_w) // 2
        gp_crop = frame[0:orig_h, gp_x1:gp_x1 + gp_crop_w]
        bot_img = cv2.resize(gp_crop, (target_w, bot_h), interpolation=cv2.INTER_LANCZOS4)
    else:
        bot_img = cv2.resize(frame, (target_w, bot_h), interpolation=cv2.INTER_LANCZOS4)

    out_frame[top_h:target_h, 0:target_w] = bot_img

    # 3. Modern Accent Divider Line (4px thick with glowing gradient effect)
    divider_y = top_h
    cv2.line(out_frame, (0, divider_y - 2), (target_w, divider_y - 2), (30, 30, 35), 1)
    cv2.line(out_frame, (0, divider_y), (target_w, divider_y), (255, 215, 0), 2)  # Gold highlight
    cv2.line(out_frame, (0, divider_y + 2), (target_w, divider_y + 2), (30, 30, 35), 1)

    return out_frame


def is_scene_cut(
    prev_hsv: np.ndarray | None,
    curr_hsv: np.ndarray,
    prev_gray: np.ndarray | None,
    curr_gray: np.ndarray,
    corr_threshold: float = 0.65,
    diff_threshold: float = 32.0,
) -> bool:
    """
    Advanced dual-metric scene cut detector:
    1. Color HSV histogram correlation (detects angle, background, and palette changes).
    2. Mean absolute pixel intensity delta (detects lighting and object displacement).
    """
    if prev_hsv is None or prev_gray is None:
        return False

    # 1. Histogram correlation across Hue and Saturation
    hist_prev = cv2.calcHist([prev_hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
    cv2.normalize(hist_prev, hist_prev, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)

    hist_curr = cv2.calcHist([curr_hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
    cv2.normalize(hist_curr, hist_curr, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)

    corr = cv2.compareHist(hist_prev, hist_curr, cv2.HISTCMP_CORREL)

    # 2. Pixel difference
    diff = float(np.mean(cv2.absdiff(prev_gray, curr_gray)))

    # Scene cut occurs if correlation dropped significantly or high diff with low correlation
    return bool(corr < corr_threshold or (diff > diff_threshold and corr < 0.78))


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
    prev_hsv = None

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
            curr_hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            # Downsampled cut detection for high speed & accuracy
            curr_small_gray = cv2.resize(curr_gray, (160, 90), interpolation=cv2.INTER_AREA)
            curr_small_hsv = cv2.resize(curr_hsv, (160, 90), interpolation=cv2.INTER_AREA)
            cut_detected = is_scene_cut(prev_hsv, curr_small_hsv, prev_gray, curr_small_gray)
            prev_gray = curr_small_gray
            prev_hsv = curr_small_hsv

            detected_face_x = None
            detected_face_box = None

            if mode not in ("pillarbox", "blur-fill"):
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
                            valid_faces.append((cx, fw * fh, (fx, fy, fw, fh)))

                    if valid_faces:
                        if cut_detected:
                            # Scene cut: old camera position is obsolete!
                            # Snap directly to the most prominent/largest face in the new shot
                            primary_face = max(valid_faces, key=lambda f: f[1])
                            detected_face_x = primary_face[0]
                            detected_face_box = primary_face[2]
                        else:
                            # Continuity: stay locked on speaker closest to current camera center
                            closest = min(valid_faces, key=lambda f: abs(f[0] - current_center_x))
                            detected_face_x = closest[0]
                            detected_face_box = closest[2]

                        consecutive_face_frames += 1
                        no_face_frames = 0
                    else:
                        consecutive_face_frames = 0
                        no_face_frames += 1
                else:
                    consecutive_face_frames = 0
                    no_face_frames += 1

            if mode in ("pillarbox", "blur-fill"):
                final_frame = render_blurred_pillarbox(frame, target_w, target_h)
            elif mode == "split-streamer":
                final_frame = render_streamer_split(frame, detected_face_box, target_w, target_h)
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
    parser.add_argument("--mode", default="auto", choices=["auto", "crop-fullscreen", "pillarbox", "split-streamer", "blur-fill"], help="Framing mode")
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
