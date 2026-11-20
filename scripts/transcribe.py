"""
Transcription module for Universal Video Clipper.
Uses faster-whisper with word-level timestamps, automatic CUDA detection with CPU int8 fallback,
and multi-language/code-switching support (English, Hindi, Hinglish).
"""

import argparse
import json
import os
import sys
import time


def get_optimal_device_config():
    try:
        import ctranslate2
        cuda_devices = ctranslate2.get_cuda_device_count()
        if cuda_devices > 0:
            # Verify CUDA runtime is actually usable (cublas64_12.dll etc must exist)
            import ctypes, ctypes.util
            cublas = ctypes.util.find_library("cublas64_12") or ctypes.util.find_library("cublas")
            if cublas:
                return "cuda", "float16"
            else:
                print("[transcribe] CUDA device found but cublas64_12.dll missing — forcing CPU mode")
    except Exception:
        pass
    return "cpu", "int8"


def load_model(model_size: str = "base"):
    from faster_whisper import WhisperModel

    device, compute_type = get_optimal_device_config()
    print(f"[transcribe] Attempting to initialize model '{model_size}' on {device} ({compute_type})...")

    try:
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
        return model, device, compute_type
    except Exception as e:
        if device == "cuda":
            print(f"[transcribe] CUDA initialization failed ({e}). Falling back to CPU (int8)...")
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            return model, "cpu", "int8"
        raise e


def transcribe_video(
    input_path: str,
    output_path: str,
    model_size: str = "base",
    language: str | None = None,
) -> dict:
    input_path = os.path.abspath(input_path)
    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    model, device, compute_type = load_model(model_size)

    print(f"[transcribe] Starting transcription for {input_path} (word_timestamps=True)...")
    start_t = time.time()

    # Pre-decode audio to numpy array to avoid av.open() API incompatibility
    # (faster-whisper 1.2.1 calls av.open(..., metadata_errors=...) which changed in av 19+)
    import numpy as np
    audio_input = input_path
    if input_path.lower().endswith(".wav"):
        try:
            import wave, struct
            print(f"[transcribe] Pre-decoding WAV to numpy array to bypass av compatibility issue...")
            with wave.open(input_path, "rb") as wf:
                n_channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                n_frames = wf.getnframes()
                raw = wf.readframes(n_frames)
            if sampwidth == 2:
                samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            elif sampwidth == 4:
                samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
            else:
                samples = np.frombuffer(raw, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0
            if n_channels > 1:
                samples = samples.reshape(-1, n_channels).mean(axis=1)
            audio_input = samples
            print(f"[transcribe] Decoded {len(samples)} samples ({len(samples)/16000:.1f}s at 16kHz)")
        except Exception as e:
            print(f"[transcribe] WAV pre-decode failed ({e}), falling back to file path")
            audio_input = input_path

    # Transcribe with word timestamps & VAD filtering
    segments_gen, info = model.transcribe(
        audio_input,
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        language=language if language and language != "auto" else None,
    )

    detected_language = info.language
    lang_probability = round(info.language_probability, 3)
    duration = round(info.duration, 2)
    print(f"[transcribe] Detected language: {detected_language} (prob: {lang_probability}), duration: {duration}s")

    structured_segments = []
    all_words = []

    for seg_idx, segment in enumerate(segments_gen):
        seg_words = []
        if segment.words:
            for w in segment.words:
                word_entry = {
                    "word": w.word.strip(),
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "probability": round(w.probability, 3),
                }
                seg_words.append(word_entry)
                all_words.append(word_entry)

        structured_segments.append(
            {
                "id": seg_idx,
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "text": segment.text.strip(),
                "words": seg_words,
            }
        )

    elapsed = round(time.time() - start_t, 2)

    result_data = {
        "source_file": input_path,
        "device_used": device,
        "compute_type": compute_type,
        "model_size": model_size,
        "language": detected_language,
        "language_probability": lang_probability,
        "duration": duration,
        "transcription_time_sec": elapsed,
        "total_segments": len(structured_segments),
        "total_words": len(all_words),
        "segments": structured_segments,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=2, ensure_ascii=False)

    print(f"[transcribe] Transcription written to {output_path} in {elapsed}s")
    return result_data


def main():
    parser = argparse.ArgumentParser(description="Transcribe video with faster-whisper and word-level timestamps.")
    parser.add_argument("--input", required=True, help="Input video or audio file path")
    parser.add_argument("--output", default="./temp_work/transcript.json", help="Path to save transcript JSON")
    parser.add_argument("--model", default="base", choices=["tiny", "base", "small", "medium", "large-v3"], help="Whisper model size")
    parser.add_argument("--language", default=None, help="Language code (e.g., 'en', 'hi') or None for auto-detect")
    args = parser.parse_args()

    transcribe_video(args.input, args.output, model_size=args.model, language=args.language)


if __name__ == "__main__":
    main()
