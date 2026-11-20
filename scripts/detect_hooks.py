"""
Viral Hook Detector & Segment Scorer.
Analyzes transcript JSON to locate high-retention, high-intensity viral moments (25-55s),
scores them on hook tension and curiosity gaps, and attaches dynamic metadata.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add scripts directory to path for generate_metadata
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from generate_metadata import generate_clip_metadata
except ImportError:
    def generate_clip_metadata(text, hook, idx=1):
        return f"Viral Moment #{idx} 🤯", "#Shorts #Viral #Trending #HighStakes #FYP"

# Extreme intensity, drama, action & intimate / romance trigger patterns
HIGH_INTENSITY_TRIGGERS = [
    # Violence, death, threats & action
    r"\b(kill|killed|die|dying|dead|shoot|gun|weapon|knife|murder|hostage|threat)\b",
    # Law enforcement & arrests
    r"\b(police|cops?|arrest|arrested|caught|busted|jail|prison|patrol|siren)\b",
    # Money, crimes & heists
    r"\b(money|dollars?|cash|million|billion|heist|rob|robbery|stole|stolen|rich|broke)\b",
    # Betrayal, lies & secrets
    r"\b(betray|betrayed|liar|lied|snitch|traitor|trust|truth|secret|confess)\b",
    # Danger & adrenaline escapes
    r"\b(run|escape|hide|trap|trapped|ambush|surrounded|danger|warning|emergency)\b",
    # Shock, expletives & extreme reactions
    r"\b(holy shit|what the fuck|oh my god|no fucking way|are you fucking serious|shut up|get out)\b",
    
    # Intimate, Sexual & Sensual dialogue
    r"\b(sex|sexy|sexual|horny|naked|undress|bedroom|in bed|sleep together|slept with|hook up|hooking up|one night stand|make love|intimate|intimacy|body|touch|touching|stroke|feel you|strip|pleasure|turn me on|turned on|bedroom talk)\b",
    
    # Kissing, Flirting, Romance & Physical Chemistry
    r"\b(kiss|kissing|kissed|smooch|make out|making out|french kiss|lips|mouth|tongue|hold me|hug|cuddle|attracted|crush|hot|cute|handsome|gorgeous|beautiful|babe|baby|sweetheart|darling|honey)\b",
    
    # Pickup lines, Rizz, Flirty openers & Confessions
    r"\b(pick up line|pickup line|rizz|take you out|date me|give me your number|can i have your number|you're so fine|are you single|got a girlfriend|got a boyfriend|have a boyfriend|have a girlfriend|like you|into you|fall for you|wanna be mine|come home with me|my place or yours|flirt|flirting)\b",
    
    # Cheating, Affairs & Relationship Drama
    r"\b(cheat|cheated|cheating|side piece|side chick|affair|jealous|jealousy|ex-boyfriend|ex-girlfriend|break up|breaking up|heartbroken|toxic|caught cheating|who is she|who is he|caught red handed)\b",
    
    # Multilingual / Hinglish Romance & Intimacy Triggers
    r"\b(pyaar|ishq|mohabbat|dil|chumma|pappi|kissi|sundar|haseen|gf|bf|dhokha|setting|pata|patana|line maarna|romance|chakkar)\b",
]

INTIMATE_ROMANCE_PATTERNS = [
    r"\b(sex|sexy|sexual|naked|bedroom|in bed|sleep together|slept with|hook up|make out|kiss|kissing|kissed|lips|smooch|pick up line|pickup line|rizz|number|take you out|date me|single|fine|flirt|flirting|cheat|cheated|cheating|affair|jealous|girlfriend|boyfriend|crush|love you|turn me on|chumma|pyaar|ishq)\b"
]

HOOK_OPENERS = [
    r"\b(why|how|what if|the secret|nobody knows|stop doing|the truth about|never|always|biggest mistake|warning|watch this)\b",
    r"\b(insane|crazy|unbelievable|mind-blowing|shocking|secret|proven|hack)\b",
    r"\b(bro|omg|oh my god|no way|what the|are you serious|is this real|look at this|wait wait|listen to me)\b",
    r"\b(are you single|can i ask you something|you're so fine|give me your number|do you have a girlfriend|do you have a boyfriend|wanna come over|wanna kiss|look at her|look at him)\b",
    r"\b(kyun|kaise|kya|sach|galti|mat karna|secret|dekho|asliyat)\b",
]

def calculate_intensity_score(text: str, duration: float, words: list) -> tuple[float, str]:
    score = 15.0
    text_lower = text.lower()

    # Optimal viral Short duration is 25 to 50 seconds
    if 25.0 <= duration <= 50.0:
        score += 25.0
    elif 50.0 < duration <= 55.0:
        score += 15.0
    else:
        score -= 10.0

    # 1. Opening hook in first 12 words
    first_words = " ".join([w.get("word", "").lower() for w in words[:14]])
    hook_matched = False
    for pat in HOOK_OPENERS:
        if re.search(pat, first_words):
            score += 25.0
            hook_matched = True
            break
        elif re.search(pat, text_lower):
            score += 10.0

    # Question hook
    if "?" in first_words or first_words.startswith(("why", "how", "what", "is ", "are ", "do ", "can ", "did ")):
        score += 15.0

    # 2. High intensity drama / action triggers throughout segment
    intensity_hits = 0
    for pat in HIGH_INTENSITY_TRIGGERS:
        matches = len(re.findall(pat, text_lower))
        if matches > 0:
            intensity_hits += matches
            score += min(30.0, matches * 12.0)

    # 3. Speech density (pacing)
    wps = len(words) / max(1.0, duration)
    if 2.2 <= wps <= 4.2:
        score += 15.0
    elif wps < 1.4:
        score -= 15.0  # Too slow / dead air

    # Cap score at 100
    normalized_score = min(100.0, max(10.0, score))

    # Determine intensity label
    is_intimate = any(re.search(pat, text_lower) for pat in INTIMATE_ROMANCE_PATTERNS)
    
    if is_intimate and (intensity_hits >= 2 or normalized_score >= 65):
        if re.search(r"\b(sex|sexual|horny|naked|bedroom|in bed|sleep together|slept with|body|touch|strip|turn me on)\b", text_lower):
            label = "Intimate Scene 🔥"
        elif re.search(r"\b(kiss|kissing|kissed|lips|make out|smooch|chumma|pappi)\b", text_lower):
            label = "Kiss & Romance 💋"
        elif re.search(r"\b(pick up line|pickup line|rizz|take you out|date me|single|fine|give me your number|wanna come over)\b", text_lower):
            label = "Rizz & Flirting 😏"
        elif re.search(r"\b(cheat|cheated|cheating|side piece|side chick|affair|jealous|who is she|who is he)\b", text_lower):
            label = "Relationship Drama 💔"
        else:
            label = "Intense Romance ❤️"
    elif normalized_score >= 85 or intensity_hits >= 3:
        label = "Extreme Intensity 🔥"
    elif normalized_score >= 70 or hook_matched:
        label = "High Drama ⚡"
    elif "?" in first_words:
        label = "Curiosity Gap ❓"
    else:
        label = "Strong Moment ✨"

    return round(normalized_score, 1), label


def find_viral_segments(
    transcript_path: str,
    min_duration: float = 25.0,
    max_duration: float = 55.0,
    top_k: int = 15,
    start_after_sec: float = 0.0,
    start_index: int = 1,
) -> list[dict]:
    with open(transcript_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        segments = data
    elif isinstance(data, dict):
        segments = data.get("segments", [])
    else:
        segments = []

    if not segments:
        return []

    candidates = []

    for i in range(len(segments)):
        start_t = segments[i]["start"]
        # Skip moments before resume threshold
        if start_t < start_after_sec:
            continue

        cur_words = []
        cur_text = []

        for j in range(i, len(segments)):
            seg = segments[j]
            cur_text.append(seg.get("text", "").strip())
            if seg.get("words"):
                cur_words.extend(seg["words"])
            else:
                for token in seg.get("text", "").split():
                    cur_words.append({"word": token})

            end_t = seg["end"]
            duration = end_t - start_t

            if duration > max_duration:
                break

            if duration >= min_duration:
                full_text = " ".join(cur_text).strip()
                last_char = full_text[-1] if full_text else ""
                ends_cleanly = last_char in [".", "!", "?", "\""]

                score_val, label = calculate_intensity_score(full_text, duration, cur_words)
                if ends_cleanly:
                    score_val = min(100.0, score_val + 10.0)

                first_words_str = " ".join([w.get("word", "") for w in cur_words[:12]])

                candidates.append({
                    "start": round(start_t, 2),
                    "end": round(end_t, 2),
                    "duration": round(duration, 2),
                    "score": score_val,
                    "intensity": label,
                    "text": full_text,
                    "hook": first_words_str + ("..." if len(cur_words) > 12 else ""),
                    "ends_cleanly": ends_cleanly
                })

    # Sort descending by intensity score
    candidates.sort(key=lambda x: x["score"], reverse=True)

    # Filter overlapping segments (> 8s overlap)
    selected = []
    for cand in candidates:
        overlap = False
        for sel in selected:
            if not (cand["end"] <= sel["start"] + 8 or cand["start"] >= sel["end"] - 8):
                overlap = True
                break
        if not overlap:
            selected.append(cand)
            if len(selected) >= top_k:
                break

    # Attach dynamic titles and niche hashtags
    for offset, c in enumerate(selected):
        c_idx = start_index + offset
        c["id"] = c_idx
        title, hashtags = generate_clip_metadata(c["text"], c["hook"], c_idx)
        c["title"] = title
        c["hashtags"] = hashtags

    return selected


def main():
    parser = argparse.ArgumentParser(description="Find high-intensity viral moments from transcript.")
    parser.add_argument("--transcript", default="./temp_work/transcript.json", help="Path to transcript JSON")
    parser.add_argument("--top-k", type=int, default=15, help="Number of viral clips to detect")
    parser.add_argument("--min-duration", type=float, default=25.0, help="Minimum clip duration in seconds")
    parser.add_argument("--max-duration", type=float, default=55.0, help="Maximum clip duration in seconds")
    parser.add_argument("--start-after", type=float, default=0.0, help="Skip segments before this timestamp (resume)")
    parser.add_argument("--start-index", type=int, default=1, help="Starting index for clip numbering")
    parser.add_argument("--output", default="./temp_work/viral_hooks.json", help="Path to save candidate hooks")
    args = parser.parse_args()

    candidates = find_viral_segments(
        args.transcript,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
        top_k=args.top_k,
        start_after_sec=args.start_after,
        start_index=args.start_index,
    )
    output_payload = {
        "total": len(candidates),
        "segments": candidates
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2, ensure_ascii=False)

    print(f"[detect_hooks] Found {len(candidates)} high-retention viral segments:")
    for idx, c in enumerate(candidates, 1):
        try:
            print(f"  Clip #{idx:02d} [{c['start']}s -> {c['end']}s | {c['duration']}s | Score: {c['score']} | {c['intensity']}]: {c['title']} | {c['hashtags']}")
        except Exception:
            safe_title = c['title'].encode('ascii', 'replace').decode('ascii')
            print(f"  Clip #{idx:02d} [{c['start']}s -> {c['end']}s | {c['duration']}s | Score: {c['score']} | {c['intensity']}]: {safe_title}")

if __name__ == "__main__":
    main()
