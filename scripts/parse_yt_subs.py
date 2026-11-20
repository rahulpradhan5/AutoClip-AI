"""
Converts YouTube json3 subtitle format into standard pipeline transcript.json
with word-level and segment-level timestamps.
"""

import json
import os
import sys

def convert_json3(json3_path: str, output_path: str):
    with open(json3_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    events = data.get("events", [])
    segments = []
    seg_id = 0

    for ev in events:
        if "segs" not in ev or not ev["segs"]:
            continue
        start_ms = ev.get("tStartMs", 0)
        dur_ms = ev.get("dDurationMs", 0)
        end_ms = start_ms + dur_ms

        words = []
        full_text_parts = []
        cur_offset = 0

        for s in ev["segs"]:
            text = s.get("utf8", "")
            if not text or text == "\n":
                continue
            offset = s.get("tOffsetMs", cur_offset)
            w_start = (start_ms + offset) / 1000.0
            full_text_parts.append(text)
            clean_word = text.strip()
            if clean_word:
                words.append({
                    "word": clean_word,
                    "start": round(w_start, 3),
                    "end": round(w_start + 0.35, 3) # default estimate
                })
            cur_offset = offset + 350

        # Refine word end timestamps to not overlap next word
        for i in range(len(words) - 1):
            if words[i]["end"] > words[i+1]["start"]:
                words[i]["end"] = words[i+1]["start"]
        if words:
            words[-1]["end"] = max(words[-1]["start"] + 0.2, end_ms / 1000.0)

        seg_text = "".join(full_text_parts).strip()
        if seg_text and words:
            segments.append({
                "id": seg_id,
                "start": round(start_ms / 1000.0, 3),
                "end": round(end_ms / 1000.0, 3),
                "text": seg_text,
                "words": words
            })
            seg_id += 1

    transcript_data = {
        "source_file": "temp_work/source.mp4",
        "language": "en",
        "duration": segments[-1]["end"] if segments else 0.0,
        "total_segments": len(segments),
        "segments": segments
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(transcript_data, f, indent=2, ensure_ascii=False)

    print(f"Converted {len(segments)} segments to {output_path}")

if __name__ == "__main__":
    convert_json3("temp_work/yt_sub.en.json3", "temp_work/transcript.json")
