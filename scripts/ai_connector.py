"""
Universal AI Model Connector
Connects Universal Clipper Studio to:
1. Antigravity Agent Bridge (native agy CLI - zero API keys required)
2. Google Gemini API (gemini-2.5-flash, gemini-1.5-pro, etc.)
3. OpenAI API (gpt-4o, gpt-4o-mini, etc.)
4. Anthropic Claude (claude-3-5-sonnet, claude-3-5-haiku, etc.)
5. Groq (llama-3.3-70b-versatile, fast inference)
6. Local Ollama (llama3, mistral, qwen2.5, deepseek-r1)
7. Custom OpenAI-Compatible (DeepSeek, OpenRouter, LM Studio, vLLM)
"""

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = WORKSPACE_ROOT / "ai_config.json"
AGY_EXE = r"C:\Users\prahu\.gemini\bin\agy.exe"

DEFAULT_CONFIG: Dict[str, Any] = {
    "provider": "antigravity",
    "model": "gemini-3.8-flash-medium",
    "api_key": "",
    "base_url": "",
    "temperature": 0.7,
    "enabled": True,
}

MODEL_PRESETS = {
    "antigravity": [
        "gemini-3.8-flash-medium",
        "gemini-3.8-flash-high",
        "claude-sonnet-4-6",
        "gpt-oss-120b-medium",
    ],
    "gemini": [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ],
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
        "o1-mini",
        "gpt-4-turbo",
    ],
    "anthropic": [
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
    ],
    "groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "mixtral-8x7b-32768",
    ],
    "ollama": [
        "llama3",
        "mistral",
        "qwen2.5",
        "deepseek-r1",
        "phi3",
    ],
    "custom": [
        "deepseek-chat",
        "meta-llama/llama-3.3-70b-instruct",
        "custom-model",
    ],
}


def get_ai_config() -> Dict[str, Any]:
    """Loads ai_config.json or returns defaults."""
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    cfg.update(saved)
        except Exception:
            pass
    # Fallback to env vars if api_key not set in file
    if not cfg.get("api_key"):
        prov = cfg.get("provider", "antigravity")
        if prov == "gemini":
            cfg["api_key"] = os.environ.get("GEMINI_API_KEY", "")
        elif prov == "openai":
            cfg["api_key"] = os.environ.get("OPENAI_API_KEY", "")
        elif prov == "anthropic":
            cfg["api_key"] = os.environ.get("ANTHROPIC_API_KEY", "")
        elif prov == "groq":
            cfg["api_key"] = os.environ.get("GROQ_API_KEY", "")
    return cfg


def save_ai_config(cfg: Dict[str, Any]) -> bool:
    """Saves AI config to ai_config.json."""
    try:
        merged = get_ai_config()
        merged.update(cfg)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)
        return True
    except Exception as e:
        print(f"[ai_connector] Error saving config: {e}")
        return False


def find_agy_path() -> Optional[str]:
    """Finds path to agy.exe."""
    if os.path.exists(AGY_EXE):
        return AGY_EXE
    found = shutil.which("agy")
    if found:
        return found
    env_path = os.environ.get("ANTIGRAVITY_AGENTAPI_EXE")
    if env_path and os.path.exists(env_path):
        return env_path
    return None


def call_antigravity(prompt: str, model: str = "gemini-3.8-flash-medium", timeout: float = 30.0) -> str:
    """Executes prompt via Antigravity agy CLI."""
    agy_path = find_agy_path()
    if not agy_path:
        raise RuntimeError("Antigravity CLI (agy.exe) not found on system path.")

    cmd = [agy_path, "--print", prompt]
    if model:
        cmd.extend(["--model", model])

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        err = proc.stderr.strip() or f"CLI exited with code {proc.returncode}"
        raise RuntimeError(f"Antigravity CLI error: {err}")

    out = proc.stdout.strip()
    return out


def call_gemini(prompt: str, model: str, api_key: str, system_prompt: str = "") -> str:
    """Calls Google Gemini API via REST."""
    if not api_key:
        raise ValueError("Google Gemini API Key is required.")

    clean_model = model or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"

    contents = []
    if system_prompt:
        contents.append({"role": "user", "parts": [{"text": f"SYSTEM INSTRUCTIONS:\n{system_prompt}"}]})
        contents.append({"role": "model", "parts": [{"text": "Understood. I will strictly adhere to these instructions."}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    payload = json.dumps({"contents": contents}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "UniversalClipper-Studio"},
    )
    with urllib.request.urlopen(req, timeout=25.0) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        candidates = res.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                return parts[0].get("text", "")
    return ""


def call_openai_compatible(
    prompt: str,
    model: str,
    api_key: str,
    base_url: str = "",
    system_prompt: str = "",
    provider: str = "openai",
) -> str:
    """Calls OpenAI, Groq, or OpenAI-compatible custom endpoints."""
    if not base_url:
        if provider == "groq":
            base_url = "https://api.groq.com/openai/v1"
        else:
            base_url = "https://api.openai.com/v1"

    clean_url = base_url.rstrip("/")
    if not clean_url.endswith("/chat/completions"):
        clean_url = f"{clean_url}/chat/completions"

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload_dict = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
    }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "UniversalClipper-Studio",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = json.dumps(payload_dict).encode("utf-8")
    req = urllib.request.Request(clean_url, data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=25.0) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        choices = res.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
    return ""


def call_anthropic(prompt: str, model: str, api_key: str, system_prompt: str = "") -> str:
    """Calls Anthropic Claude Messages API."""
    if not api_key:
        raise ValueError("Anthropic API Key is required.")

    url = "https://api.anthropic.com/v1/messages"
    clean_model = model or "claude-3-5-haiku-20241022"

    payload_dict = {
        "model": clean_model,
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system_prompt:
        payload_dict["system"] = system_prompt

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "User-Agent": "UniversalClipper-Studio",
    }
    payload = json.dumps(payload_dict).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=25.0) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        content_parts = res.get("content", [])
        if content_parts:
            return content_parts[0].get("text", "")
    return ""


def call_ollama(prompt: str, model: str, host: str = "http://localhost:11434") -> str:
    """Calls local Ollama API."""
    clean_host = (host or "http://localhost:11434").rstrip("/")
    url = f"{clean_host}/api/generate"
    payload = json.dumps({
        "model": model or "llama3",
        "prompt": prompt,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "UniversalClipper-Studio"},
    )
    with urllib.request.urlopen(req, timeout=30.0) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return res.get("response", "")


def generate_ai_completion(
    prompt: str,
    system_prompt: str = "",
    config_override: Optional[Dict[str, Any]] = None,
) -> str:
    """Main dispatcher for AI completion based on configured provider."""
    cfg = get_ai_config()
    if config_override:
        cfg.update(config_override)

    prov = cfg.get("provider", "antigravity").lower()
    model = cfg.get("model", "")
    api_key = cfg.get("api_key", "")
    base_url = cfg.get("base_url", "")

    if prov == "antigravity":
        return call_antigravity(prompt, model=model or "gemini-3.8-flash-medium")
    elif prov == "gemini":
        return call_gemini(prompt, model=model or "gemini-2.5-flash", api_key=api_key, system_prompt=system_prompt)
    elif prov in ("openai", "groq", "custom"):
        return call_openai_compatible(
            prompt,
            model=model or ("gpt-4o-mini" if prov == "openai" else "llama-3.3-70b-versatile"),
            api_key=api_key,
            base_url=base_url,
            system_prompt=system_prompt,
            provider=prov,
        )
    elif prov == "anthropic":
        return call_anthropic(prompt, model=model or "claude-3-5-haiku-20241022", api_key=api_key, system_prompt=system_prompt)
    elif prov == "ollama":
        return call_ollama(prompt, model=model or "llama3", host=base_url or "http://localhost:11434")
    else:
        return call_antigravity(prompt, model="gemini-3.8-flash-medium")


def test_ai_connection(config_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Pings the selected AI provider with a quick test to measure latency and verify credentials."""
    cfg = get_ai_config()
    if config_override:
        cfg.update(config_override)

    provider = cfg.get("provider", "antigravity")
    model = cfg.get("model", "default")
    start_t = time.time()

    try:
        test_prompt = "Reply with exactly one word: 'CONNECTED'"
        reply = generate_ai_completion(test_prompt, config_override=cfg)
        latency_ms = round((time.time() - start_t) * 1000)

        clean_reply = reply.strip().strip("'\"")
        return {
            "success": True,
            "provider": provider,
            "model": model,
            "latency_ms": latency_ms,
            "reply": clean_reply,
            "message": f"Successfully connected to {provider.upper()} ({model}) in {latency_ms}ms!",
        }
    except Exception as e:
        latency_ms = round((time.time() - start_t) * 1000)
        return {
            "success": False,
            "provider": provider,
            "model": model,
            "latency_ms": latency_ms,
            "error": str(e),
            "message": f"Connection failed to {provider.upper()}: {str(e)}",
        }


def extract_json_from_response(text: str) -> Optional[Dict[str, Any]]:
    """Robustly extracts JSON object from LLM response text."""
    if not text:
        return None
    # Look for fenced json or raw braces
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass

    m2 = re.search(r"(\{.*\})", text, re.DOTALL)
    if m2:
        try:
            return json.loads(m2.group(1))
        except Exception:
            pass
    return None


def score_hook_with_ai(transcript_text: str, current_hook: str, video_title: str = "") -> Optional[Dict[str, Any]]:
    """Uses configured AI model to evaluate viral potential and refine the opening hook."""
    prompt = (
        "You are an elite YouTube Shorts / TikTok retention editor.\n"
        f"Parent Video Topic: {video_title or 'Gaming Highlight'}\n"
        f"Clip Dialogue: \"{transcript_text[:700]}\"\n\n"
        "Analyze this clip for maximum 0-2 second retention in the YouTube Shorts feed.\n"
        "Return ONLY a valid JSON object in this exact schema with NO extra commentary:\n"
        "{\n"
        '  "virality_score": 95,\n'
        '  "opening_hook": "Exact punchy 4-10 word quote that must start the clip",\n'
        '  "viral_title": "Curiosity gap title with quotes and emoji",\n'
        '  "hook_type": "Conflict / Confrontation / Rizz / Plot Twist",\n'
        '  "reasoning": "Why viewers will stay instead of swiping away"\n'
        "}"
    )

    try:
        raw = generate_ai_completion(prompt)
        parsed = extract_json_from_response(raw)
        return parsed
    except Exception as e:
        print(f"[ai_connector] Hook scoring fallback: {e}")
        return None


def generate_ai_metadata(
    text: str,
    hook: str,
    video_title: str = "",
    timestamps: str = "",
    variation: int = 0
) -> Optional[Dict[str, Any]]:
    """Uses configured AI model to generate ultra-viral title, hashtags, keywords, and description."""
    cfg = get_ai_config()
    if not cfg.get("enabled", True):
        return None

    prov = cfg.get("provider", "antigravity")
    model = cfg.get("model", "default")

    prompt = (
        "You are an elite YouTube Shorts growth hacker & viral copywriter for high-retention channels.\n"
        f"Parent Video: {video_title or 'Gaming Highlight'}\n"
        f"Clip Hook / Punchline: \"{hook or text[:120]}\"\n"
        f"Clip Dialogue Transcript: \"{text[:500]}\"\n"
        f"Scene Range: {timestamps or 'Highlight'}\n\n"
        "Generate maximum-CTR, high-retention YouTube Shorts metadata.\n"
        "Requirements:\n"
        "1. title: Under 65 characters. Must contain high curiosity gap, punchy quote or high-stakes event, ending with an intense emoji + #Shorts.\n"
        "2. hashtags: 4 to 6 relevant high-volume hashtags separated by spaces (e.g. #GTA6 #Gaming #Viral #Shorts #FYP).\n"
        "3. first_200: The first 180-200 characters of the description. MUST contain crucial high-volume search queries within the first two sentences so YouTube algorithm indexes it above the fold.\n"
        "4. description: Full comprehensive YouTube description formatted with hook quote, timestamps, format (1080x1920 60 FPS), search keywords, and hashtags.\n"
        "5. keywords: An array of 8 to 12 high-intent YouTube search queries (lowercase strings).\n\n"
        "Return ONLY a valid JSON object in this exact schema with NO extraneous text or markdown explanation:\n"
        "{\n"
        '  "title": "GTA 6 - The Cops Showed Up at the WORST Time 💀 #Shorts",\n'
        '  "hashtags": "#GTA6 #ViceCity #GamingShorts #Shorts",\n'
        '  "first_200": "Lucia and Jason thought they had the clean getaway until sirens echoed across Vice City...",\n'
        '  "description": "Full description text...",\n'
        '  "keywords": ["gta 6 gameplay", "vice city", "gta 6 viral moments", "police chase"]\n'
        "}"
    )

    try:
        raw = generate_ai_completion(prompt)
        parsed = extract_json_from_response(raw)
        if parsed and isinstance(parsed, dict) and parsed.get("title"):
            parsed["variation"] = variation
            parsed["generated_by"] = f"{prov}:{model}"
            return parsed
    except Exception as e:
        print(f"[ai_connector] Metadata generation fallback: {e}")

    return None


def batch_detect_and_title_moments_with_ai(
    candidates: list,
    video_title: str = "",
    top_n: int = 15
) -> Optional[Dict[str, Any]]:
    """
    Sends candidate segments to the active AI model (Antigravity CLI or external provider)
    to comprehensively curate, score, and title the hot moments, plus recommend the best caption style.
    """
    cfg = get_ai_config()
    if not cfg.get("enabled", True):
        return None

    prov = cfg.get("provider", "antigravity")
    model = cfg.get("model", "default")

    if not candidates:
        return None

    items_to_score = []
    for i, c in enumerate(candidates[:top_n], 1):
        dialogue = (c.get("text", "") or "")[:200].replace("\n", " ").strip()
        items_to_score.append(
            f"Moment #{i} [{c.get('start', 0)}s - {c.get('end', 0)}s, {c.get('duration', 0)}s]: \"{dialogue}\""
        )
    moments_text = "\n".join(items_to_score)

    prompt = (
        "You are an elite YouTube Shorts / TikTok executive editor & viral retention strategist.\n"
        f"Video Topic: \"{video_title or 'Gaming Highlight'}\"\n\n"
        "Here are candidate dialogue moments extracted from the video:\n"
        f"{moments_text}\n\n"
        "TASK:\n"
        "1. Evaluate every candidate moment for viral 0-2s hook retention, conflict, and curiosity.\n"
        "2. For each moment, extract the EXACT punchy 0-2s hook line (4-10 words) that must start the clip.\n"
        "3. Craft an ultra-clickable viral title under 65 chars (with quote or high-stakes action, emoji + #Shorts).\n"
        "4. Generate 4-5 relevant niche hashtags.\n"
        "5. Assign a virality score (65-100) and an intensity badge (e.g. 'Cop Ambush 🚨', 'High Stakes 💀', 'Plot Twist 🤯', 'Rizz Moment 🔥').\n"
        "6. Recommend the single best animated subtitle style for this video from: 'hormozi' (Gold), 'mrbeast' (Lime), 'cyber' (Cyan), 'crimson' (Red), 'sunset' (Orange), 'bubblegum' (Pink), 'violet' (Electric Violet), 'classic_white'.\n\n"
        "Return ONLY a valid JSON object matching this schema with NO extra commentary:\n"
        "{\n"
        '  "caption_style": {\n'
        '    "theme": "hormozi",\n'
        '    "position": "bottom",\n'
        '    "format": "karaoke",\n'
        '    "font": "Arial Black",\n'
        '    "reason": "High-contrast gold karaoke text for fast-paced action"\n'
        '  },\n'
        '  "moments": [\n'
        '    {\n'
        '      "index": 1,\n'
        '      "score": 96,\n'
        '      "intensity": "Cop Ambush 🚨",\n'
        '      "hook": "Exact punchy quote to start clip",\n'
        '      "viral_title": "GTA 6 - When The Cops Ambushed The Heist 💀 #Shorts",\n'
        '      "hashtags": "#GTA6 #ViceCity #GamingShorts #Shorts #Viral"\n'
        '    }\n'
        '  ]\n'
        "}"
    )

    try:
        raw = generate_ai_completion(prompt)
        parsed = extract_json_from_response(raw)
        if parsed and isinstance(parsed, dict) and "moments" in parsed:
            parsed["generated_by"] = f"{prov}:{model}"
            return parsed
    except Exception as e:
        print(f"[ai_connector] Batch moment detection fallback: {e}")

    return None


def parse_reedit_feedback_with_ai(
    feedback: str,
    current_clip: Dict[str, Any],
    video_title: str = ""
) -> Optional[Dict[str, Any]]:
    """
    Parses user natural language feedback to intelligently adjust start/end timestamps,
    framing layout (split-streamer, pillarbox, crop-fullscreen), caption styling, and titles.
    """
    cfg = get_ai_config()
    if not cfg.get("enabled", True):
        return None

    prov = cfg.get("provider", "antigravity")
    model = cfg.get("model", "default")

    cur_start = float(current_clip.get("start", 0.0))
    cur_end = float(current_clip.get("end", 30.0))
    cur_title = current_clip.get("title", "")
    cur_hook = current_clip.get("hook", "")
    cur_layout = current_clip.get("framing_mode", "auto")
    cur_theme = current_clip.get("subtitle_theme", "hormozi")
    cur_format = current_clip.get("subtitle_format", "karaoke")

    prompt = (
        "You are an expert video editor & viral director for YouTube Shorts and Instagram Reels.\n"
        f"Video Title: \"{video_title}\"\n"
        f"Current Clip Settings:\n"
        f"- Start Time: {cur_start}s, End Time: {cur_end}s (Duration: {round(cur_end - cur_start, 1)}s)\n"
        f"- Current Title: \"{cur_title}\"\n"
        f"- Opening Hook: \"{cur_hook}\"\n"
        f"- Framing Layout: \"{cur_layout}\" (Choices: 'auto', 'split-streamer', 'pillarbox', 'crop-fullscreen')\n"
        f"- Subtitle Theme: \"{cur_theme}\" (Choices: 'hormozi', 'mrbeast', 'cyber', 'crimson', 'sunset', 'bubblegum', 'violet', 'classic_white')\n"
        f"- Caption Format: \"{cur_format}\" (Choices: 'karaoke', 'single_word', 'boxed_pill')\n"
        f"- BGM Track: \"{current_clip.get('bgm_track', 'auto')}\" (Choices: 'auto', 'romantic_piano', 'intense_cinematic', 'phonk_drift', 'dark_tension', 'epic_hype', 'lofi_chill', 'quirky_bounce', 'none')\n"
        f"- Hook SFX: \"{current_clip.get('hook_sfx', 'whoosh_riser')}\" (Choices: 'auto', 'romantic_sparkle', 'heartbeat_thud', 'whoosh_riser', 'vine_boom', 'pop_ding', 'record_scratch', 'none')\n\n"
        f"USER RE-EDIT FEEDBACK: \"{feedback}\"\n\n"
        "TASK:\n"
        "1. Understand what the user wants to adjust based on their feedback.\n"
        "   - If they ask to trim, shift, or start earlier/later, adjust 'start' and 'end' (keep duration between 18s and 45s).\n"
        "   - If they ask for streamer split or facecam layout, set layout to 'split-streamer'.\n"
        "   - If they ask for blurred background or full 16:9, set layout to 'pillarbox'.\n"
        "   - If they ask for full screen action, set layout to 'crop-fullscreen'.\n"
        "   - If they request specific caption styles, adjust 'theme', 'format', and 'position'.\n"
        "   - If they request audio changes (romantic, intense cinematic, phonk, tension, lo-fi, funny music, vine boom, whoosh, quieter/louder music, or mute music), adjust 'bgm_track', 'bgm_volume', and 'hook_sfx'.\n"
        "   - If they request a different title, punchy hook, or vibe, generate an improved viral title and hashtags.\n"
        "2. Provide an 'ai_note' explaining what was adjusted.\n\n"
        "Return ONLY a valid JSON object matching this schema with NO extra markdown:\n"
        "{\n"
        f'  "start": {cur_start},\n'
        f'  "end": {cur_end},\n'
        '  "layout": "split-streamer",\n'
        '  "theme": "hormozi",\n'
        '  "format": "karaoke",\n'
        '  "position": "bottom",\n'
        '  "bgm_track": "phonk_drift",\n'
        '  "bgm_volume": 0.18,\n'
        '  "hook_sfx": "whoosh_riser",\n'
        '  "title": "Improved High-CTR Title 💀 #Shorts",\n'
        '  "hook": "Exact revised opening quote",\n'
        '  "hashtags": "#GTA6 #Gaming #Viral #Shorts",\n'
        '  "ai_note": "Adjusted framing to Streamer Split-Screen, updated music to Phonk Drift, and trimmed opening for faster hook."\n'
        "}"
    )

    try:
        raw = generate_ai_completion(prompt)
        parsed = extract_json_from_response(raw)
        if parsed and isinstance(parsed, dict):
            parsed["generated_by"] = f"{prov}:{model}"
            return parsed
    except Exception as e:
        print(f"[ai_connector] Re-edit feedback parsing fallback: {e}")

    return None
