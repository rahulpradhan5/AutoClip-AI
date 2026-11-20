"""
Find top 3 viral moments from GTA 6 walkthrough transcript.
Searches for high drama, story twists, robberies, confrontations, or iconic dialogue.
"""

import json
import re

with open("temp_work/transcript.json", "r", encoding="utf-8") as f:
    data = json.load(f)

segments = data.get("segments", [])
print(f"Total segments: {len(segments)}")

# Look for high energy keywords in GTA 6 / Vice City storyline
keywords = [
    "trust", "crazy", "money", "cops", "kill", "gun", "police", "jail", "prison",
    "run", "shoot", "car", "vice city", "lucia", "jason", "heist", "rob", "plan",
    "dead", "trouble", "rule", "promise"
]

candidates = []

# Scan for windows between 28 and 52 seconds
for i in range(0, len(segments) - 5, 2):
    start_t = segments[i]["start"]
    # Only look within first 30-40 minutes where the main story/prologue intro unfolds (or any strong cutscene)
    if start_t > 3600: # limit search to first hour for fastest extraction
        break

    for j in range(i + 3, min(i + 40, len(segments))):
        end_t = segments[j]["end"]
        dur = end_t - start_t
        if 28.0 <= dur <= 52.0:
            window_segs = segments[i:j+1]
            full_text = " ".join([s["text"].strip() for s in window_segs])
            text_lower = full_text.lower()
            
            # Count density and keywords
            kw_hits = sum(1 for kw in keywords if re.search(r'\b' + kw + r'\b', text_lower))
            words_count = sum(len(s.get("words", [])) for s in window_segs)
            wps = words_count / dur
            
            # Sentence ends
            ends_clean = full_text.strip()[-1] in [".", "!", "?", "\""]
            
            score = kw_hits * 15.0 + (wps * 10.0)
            if ends_clean:
                score += 20.0
            if "?" in full_text[:40]: # hook question early
                score += 15.0
                
            candidates.append({
                "start": round(start_t, 2),
                "end": round(end_t, 2),
                "duration": round(dur, 2),
                "score": round(score, 2),
                "text": full_text,
                "first_words": full_text[:60]
            })

candidates.sort(key=lambda x: x["score"], reverse=True)

# Select 3 non-overlapping
selected = []
for c in candidates:
    overlap = False
    for s in selected:
        if not (c["end"] < s["start"] - 30 or c["start"] > s["end"] + 30):
            overlap = True
            break
    if not overlap:
        selected.append(c)
        if len(selected) == 3:
            break

print(json.dumps(selected, indent=2))
with open("temp_work/top3_hooks.json", "w", encoding="utf-8") as f:
    json.dump(selected, f, indent=2)
