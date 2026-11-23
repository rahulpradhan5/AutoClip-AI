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
    def generate_clip_metadata(text, hook, idx=1, variation=0, video_title=""):
        return f"Viral Moment #{idx} 🤯", "#Shorts #Viral #Trending #HighStakes #FYP"

try:
    from ai_connector import (
        get_ai_config,
        score_hook_with_ai,
        batch_detect_and_title_moments_with_ai,
    )
except ImportError:
    get_ai_config = lambda: {"enabled": False}
    score_hook_with_ai = lambda *a, **k: None
    batch_detect_and_title_moments_with_ai = lambda *a, **k: None

# Extreme intensity, drama, action & intimate / romance trigger patterns
HIGH_INTENSITY_TRIGGERS = [
    # Violence, death, threats & action
    r"\b(kill|killed|die|dying|dead|shoot|gun|weapon|knife|murder|hostage|threat|fight)\b",
    # Law enforcement & arrests
    r"\b(police|cops?|arrest|arrested|caught|busted|jail|prison|patrol|siren|handcuff)\b",
    # Money, crimes & heists
    r"\b(money|dollars?|cash|million|billion|heist|rob|robbery|stole|stolen|rich|broke|treasure|strong\s*box)\b",
    # Betrayal, lies & secrets
    r"\b(betray|betrayed|liar|lied|snitch|traitor|trust|truth|secret|confess|stalking|stalker)\b",
    # Danger & adrenaline escapes
    r"\b(run|escape|hide|trap|trapped|ambush|surrounded|danger|warning|emergency)\b",
    # Shock, expletives & extreme reactions
    r"\b(holy shit|what the fuck|oh my god|no fucking way|are you fucking serious|shut up|get out|psycho|crazy)\b",
    
    # Intimate, Sexual & Sensual dialogue
    r"\b(sex|sexy|sexual|horny|naked|undress|bedroom|in bed|sleep together|slept with|hook up|hooking up|one night stand|make love|intimate|intimacy|body|touch|touching|stroke|feel you|strip|pleasure|turn me on|turned on|bedroom talk)\b",
    
    # Kissing, Flirting, Romance & Physical Chemistry
    r"\b(kiss|kissing|kissed|smooch|make out|making out|french kiss|lips|mouth|tongue|hold me|hug|cuddle|attracted|crush|hot|cute|handsome|gorgeous|beautiful|babe|baby|sweetheart|darling|honey|marry|married|marriage)\b",
    
    # Pickup lines, Rizz, Flirty openers & Confessions
    r"\b(pick up line|pickup line|rizz|take you out|date me|give me your number|can i have your number|you're so fine|are you single|got a girlfriend|got a boyfriend|have a boyfriend|have a girlfriend|like you|into you|fall for you|wanna be mine|come home with me|my place or yours|flirt|flirting)\b",
    
    # Cheating, Affairs & Relationship Drama
    r"\b(cheat|cheated|cheating|side piece|side chick|affair|jealous|jealousy|ex-boyfriend|ex-girlfriend|break up|breaking up|heartbroken|toxic|caught cheating|who is she|who is he|caught red handed)\b",
    
    # Multilingual / Hinglish Romance & Intimacy Triggers
    r"\b(pyaar|ishq|mohabbat|dil|chumma|pappi|kissi|sundar|haseen|gf|bf|dhokha|setting|pata|patana|line maarna|romance|chakkar)\b",
]

WEAK_OPENERS = re.compile(
    r"^(?:>>\s*)?(?:um|uh|well|like|so|and|yeah|okay|right|oh|i mean|you know|just|but|nuh-uh|uh-huh|barely|thing\?|fixing my|heard of|what\?|didn't think)\b",
    re.IGNORECASE
)

CENSOR_CLEANER = re.compile(r"\[\s*__\s*\]|\[\s*Â__Â\s*\]|\[\s*censored\s*\]", re.IGNORECASE)

def clean_dialogue(text: str) -> str:
    """Strips subtitle markers, censorship brackets, and stray artifacts."""
    if not text:
        return ""
    t = text.replace(">>", "").replace("<<", "")
    t = CENSOR_CLEANER.sub("...", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

INTIMATE_ROMANCE_PATTERNS = [
    r"\b(sex|sexy|sexual|naked|bedroom|in bed|sleep together|slept with|hook up|make out|kiss|kissing|kissed|lips|smooch|pick up line|pickup line|rizz|number|take you out|date me|single|fine|flirt|flirting|cheat|cheated|cheating|affair|jealous|girlfriend|boyfriend|crush|love you|turn me on|chumma|pyaar|ishq|marry|married)\b"
]

HOOK_OPENERS = [
    r"\b(why|how|what if|the secret|nobody knows|stop doing|the truth about|never|always|biggest mistake|warning|watch this)\b",
    r"\b(insane|crazy|unbelievable|mind-blowing|shocking|secret|proven|hack|stalking|married)\b",
    r"\b(bro|omg|oh my god|no way|what the|are you serious|is this real|look at this|wait wait|listen to me|you lied)\b",
    r"\b(are you single|can i ask you something|you're so fine|give me your number|do you have a girlfriend|do you have a boyfriend|wanna come over|wanna kiss|look at her|look at him|got your gun)\b",
    r"\b(kyun|kaise|kya|sach|galti|mat karna|secret|dekho|asliyat)\b",
]

def calculate_intensity_score(text: str, duration: float, words: list) -> tuple[float, str]:
    score = 15.0
    text_clean = clean_dialogue(text)
    text_lower = text_clean.lower()

    # Optimal viral Short duration is 20 to 38 seconds (High retention & loopability)
    if 20.0 <= duration <= 38.0:
        score += 30.0
    elif 38.0 < duration <= 45.0:
        score += 18.0
    elif duration > 50.0:
        score -= 25.0  # Too long for high-speed Shorts feed
    else:
        score -= 10.0

    # 1. Opening hook in first 10 words (Zero-Fluff 0-2s rule)
    first_words = " ".join([w.get("word", "").lower() for w in words[:10]])
    first_words_clean = clean_dialogue(first_words).lower()

    # Heavy penalty for starting with conversational filler or weak mutterings
    if WEAK_OPENERS.match(first_words_clean):
        score -= 30.0

    hook_matched = False
    for pat in HOOK_OPENERS:
        if re.search(pat, first_words_clean):
            score += 35.0
            hook_matched = True
            break
        elif re.search(pat, text_lower):
            score += 10.0

    # High-impact immediate question or confrontation hook
    if "?" in first_words_clean or any(first_words_clean.startswith(q) for q in ["why", "how", "what", "is ", "are ", "do ", "can ", "did ", "who ", "where "]):
        score += 20.0

    # 2. High intensity drama / action triggers throughout segment
    intensity_hits = 0
    for pat in HIGH_INTENSITY_TRIGGERS:
        matches = len(re.findall(pat, text_lower))
        if matches > 0:
            intensity_hits += matches
            score += min(30.0, matches * 12.0)

    # Immediate trigger in first 8 words gets massive boost
    for pat in HIGH_INTENSITY_TRIGGERS:
        if re.search(pat, first_words_clean):
            score += 25.0
            break

    # 3. Speech density (pacing)
    wps = len(words) / max(1.0, duration)
    if 2.2 <= wps <= 4.2:
        score += 15.0
    elif wps < 1.4:
        score -= 20.0  # Too slow / dead air

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
        elif re.search(r"\b(cheat|cheated|cheating|side piece|side chick|affair|jealous|who is she|who is he|marry|married)\b", text_lower):
            label = "Relationship Drama 💔"
        else:
            label = "Intense Romance ❤️"
    elif normalized_score >= 85 or intensity_hits >= 3:
        label = "Extreme Intensity 🔥"
    elif normalized_score >= 70 or hook_matched:
        label = "High Drama ⚡"
    elif "?" in first_words_clean:
        label = "Curiosity Gap ❓"
    else:
        label = "Strong Moment ✨"

    return normalized_score, label

    return round(normalized_score, 1), label


def check_ollama_available(host: str = "http://localhost:11434") -> tuple[bool, str]:
    """Checks if Ollama is running locally and returns (is_available, model_name)."""
    import urllib.request
    try:
        req = urllib.request.Request(f"{host}/api/tags", headers={"User-Agent": "AutoClip-AI"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                models = data.get("models", [])
                if models:
                    preferred = ["llama3", "mistral", "qwen", "gemma", "phi3"]
                    for p in preferred:
                        for m in models:
                            if p in m.get("name", "").lower():
                                return True, m["name"]
                    return True, models[0].get("name", "llama3")
                return True, "llama3"
    except Exception:
        pass
    return False, ""


def score_candidate_with_ollama(text: str, model_name: str, host: str = "http://localhost:11434") -> dict | None:
    """Uses local Ollama LLM to assess virality and hook tension."""
    import urllib.request
    prompt = (
        "You are an expert viral video editor. Analyze this short transcript segment for a YouTube Short / TikTok.\n"
        f"Transcript: \"{text[:600]}\"\n\n"
        "Respond ONLY with a JSON object in this exact format:\n"
        "{\"virality_score\": 85, \"hook_type\": \"Curiosity Gap\", \"viral_title\": \"Wait For It... 🤯\"}"
    )
    payload = json.dumps({
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "format": "json"
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"{host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "AutoClip-AI"}
        )
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            if resp.status == 200:
                res_data = json.loads(resp.read().decode("utf-8"))
                response_str = res_data.get("response", "{}")
                return json.loads(response_str)
    except Exception:
        pass
    return None


def find_viral_segments(
    transcript_path: str,
    min_duration: float = 20.0,
    max_duration: float = 42.0,
    top_k: int = 15,
    start_after_sec: float = 0.0,
    start_index: int = 1,
    use_ollama: bool = True,
    video_title: str = "",
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

        start_seg_text = clean_dialogue(segments[i].get("text", "")).strip()

        # ZERO-FLUFF FILTER: Avoid starting clips on weak mutterings or small talk fragments
        # unless that segment immediately leads to a question or hook within itself
        if WEAK_OPENERS.match(start_seg_text) and "?" not in start_seg_text and len(start_seg_text.split()) < 4:
            continue

        cur_words = []
        cur_text = []

        for j in range(i, len(segments)):
            seg = segments[j]

            # DEAD-AIR DETECTION: If silence between dialogue segments exceeds 2.5s, cut candidate here
            if j > i:
                prev_end = segments[j - 1]["end"]
                cur_start = seg["start"]
                if (cur_start - prev_end) > 2.5:
                    break

            raw_txt = seg.get("text", "").strip()
            cur_text.append(clean_dialogue(raw_txt))
            if seg.get("words"):
                cur_words.extend(seg["words"])
            else:
                for token in raw_txt.split():
                    cur_words.append({"word": clean_dialogue(token)})

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

                first_words_str = clean_dialogue(" ".join([w.get("word", "") for w in cur_words[:10]]))

                candidates.append({
                    "start": round(start_t, 2),
                    "end": round(end_t, 2),
                    "duration": round(duration, 2),
                    "score": score_val,
                    "intensity": label,
                    "text": full_text,
                    "hook": first_words_str + ("..." if len(cur_words) > 10 else ""),
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

    # Check AI Model Connector (Antigravity CLI, Gemini, OpenAI, Claude, Groq, Ollama)
    ai_cfg = get_ai_config()
    ai_active = bool(ai_cfg and ai_cfg.get("enabled", True))
    ai_batch_data = None
    if ai_active:
        print(f"[detect_hooks] AI Director Active ({ai_cfg.get('provider', 'antigravity').upper()} • {ai_cfg.get('model')})!")
        print(f"[detect_hooks] Prompting selected AI model to curate hot moments, viral titles, hooks, and caption styles...")
        try:
            ai_batch_data = batch_detect_and_title_moments_with_ai(selected, video_title=video_title, top_n=len(selected))
        except Exception as e:
            print(f"[detect_hooks] AI batch generation failed, falling back to NLP heuristics: {e}")
            ai_batch_data = None

    # Check optional fallback Ollama LLM scoring if AI connector is not configured
    ollama_online = False
    ollama_model = ""
    if not ai_active and use_ollama:
        ollama_online, ollama_model = check_ollama_available()
        if ollama_online:
            print(f"[detect_hooks] Local Ollama detected! Enhancing top hooks with model: {ollama_model}")
        else:
            print("[detect_hooks] Using built-in high-speed NLP heuristic engine.")

    # Save AI recommended caption style if returned
    if ai_batch_data and "caption_style" in ai_batch_data:
        try:
            temp_dir = Path(transcript_path).parent
            cap_style_file = temp_dir / "ai_caption_style.json"
            with open(cap_style_file, "w", encoding="utf-8") as f:
                json.dump(ai_batch_data["caption_style"], f, indent=2)
            c_style = ai_batch_data["caption_style"]
            print(f"[detect_hooks] AI Caption Stylist: Theme={c_style.get('theme')} | Format={c_style.get('format')} | Pos={c_style.get('position')}")
        except Exception:
            pass

    # Map AI-curated moments
    ai_moments_by_idx = {}
    if ai_batch_data and "moments" in ai_batch_data:
        for m in ai_batch_data["moments"]:
            idx = m.get("index")
            if idx is not None:
                ai_moments_by_idx[idx] = m

    # Attach dynamic titles and niche hashtags
    for offset, c in enumerate(selected):
        c_idx = start_index + offset
        c["id"] = c_idx

        # If AI curated this moment, apply the AI-crafted metadata
        ai_m = ai_moments_by_idx.get(offset + 1)
        if ai_m:
            c["score"] = float(ai_m.get("score", c["score"]))
            c["title"] = ai_m.get("viral_title") or c.get("title", "")
            c["hashtags"] = ai_m.get("hashtags") or "#Shorts #Viral #Trending"
            if ai_m.get("hook"):
                c["hook"] = ai_m["hook"].strip()
            if ai_m.get("intensity"):
                c["intensity"] = ai_m["intensity"].strip()
            c["ai_powered"] = True
            c["generated_by"] = ai_batch_data.get("generated_by", "ai")
        else:
            # Fallback to local heuristic metadata
            title, hashtags = generate_clip_metadata(c["text"], c["hook"], c_idx, 0, video_title=video_title)
            c["title"] = title
            c["hashtags"] = hashtags
            c["ai_powered"] = False

            if ollama_online and offset < 5:
                llm_res = score_candidate_with_ollama(c["text"], ollama_model)
                if llm_res and isinstance(llm_res, dict):
                    llm_score = llm_res.get("virality_score")
                    if llm_score:
                        c["score"] = round((c["score"] + float(llm_score)) / 2.0, 1)
                    if llm_res.get("viral_title"):
                        c["title"] = f"{llm_res['viral_title'].strip()} 🔥"
                    if llm_res.get("hook_type"):
                        c["intensity"] = f"{llm_res['hook_type'].strip()} ⚡"

        # AI Audio Director: Select ideal royalty-free BGM and hook SFX
        try:
            from audio_mixer import ai_select_audio
            rec = ai_select_audio(
                transcript=c.get("text", ""),
                hook=c.get("hook", ""),
                video_title=video_title,
                intensity_score=c.get("score", 85.0)
            )
            c["bgm_track"] = rec.get("bgm_track", "dark_tension")
            c["bgm_volume"] = rec.get("bgm_volume", 0.16)
            c["hook_sfx"] = rec.get("hook_sfx", "whoosh_riser")
            c["ai_audio_reason"] = rec.get("ai_reasoning", "AI auto-ducked BGM")
        except Exception:
            c["bgm_track"] = "dark_tension"
            c["bgm_volume"] = 0.16
            c["hook_sfx"] = "whoosh_riser"
            c["ai_audio_reason"] = "Royalty-free BGM with sidechain ducking"

    return selected


def main():
    parser = argparse.ArgumentParser(description="Find high-intensity viral moments from transcript.")
    parser.add_argument("--transcript", default="./temp_work/transcript.json", help="Path to transcript JSON")
    parser.add_argument("--video-title", default="", help="Source video title for context-aware clipping & niche metadata")
    parser.add_argument("--top-k", type=int, default=15, help="Number of viral clips to detect")
    parser.add_argument("--min-duration", type=float, default=20.0, help="Minimum clip duration in seconds")
    parser.add_argument("--max-duration", type=float, default=42.0, help="Maximum clip duration in seconds")
    parser.add_argument("--start-after", type=float, default=0.0, help="Skip segments before this timestamp (resume)")
    parser.add_argument("--start-index", type=int, default=1, help="Starting index for clip numbering")
    parser.add_argument("--output", default="./temp_work/viral_hooks.json", help="Path to save candidate hooks")
    parser.add_argument("--no-ollama", action="store_true", help="Disable local Ollama LLM hook scoring")
    args = parser.parse_args()

    candidates = find_viral_segments(
        args.transcript,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
        top_k=args.top_k,
        start_after_sec=args.start_after,
        start_index=args.start_index,
        use_ollama=not args.no_ollama,
        video_title=args.video_title,
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
