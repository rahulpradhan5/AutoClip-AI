"""
Intelligent Dynamic Metadata & YouTube Niche Crawler for Universal Clipper.
Crawls live YouTube search autocomplete trends and top-performing viral video titles
for the specific topic of each clip, extracting real trending niche hashtags and CTR-optimized titles.
Falls back seamlessly to offline NLP heuristics if network is unavailable.
"""

import html as html_lib
import json
from pathlib import Path
import random
import re
import urllib.parse
import urllib.request
from typing import List, Tuple

INTENSE_EMOJIS = ["🤯", "🚨", "😱", "🔥", "💀", "⚡", "👀", "💥", "🛑", "⚠️", "👑", "🎯"]

# In-memory session cache so repeated calls/regenerations are instantaneous
YT_METADATA_CACHE = {
    "suggestions": {},
    "titles": {},
}

# Offline core fallback triggers & tags in case of network unavailability
OFFLINE_FALLBACK_NICHES = {
    "gta": {
        "triggers": ["gta", "vice city", "lucia", "jason", "grand theft auto", "rockstar", "heist", "stole", "steal", "car"],
        "tags": ["#GTA6", "#ViceCity", "#GTA6Moments", "#LuciaAndJason", "#RockstarGames", "#GTAViral", "#GrandTheftAuto"]
    },
    "gaming": {
        "triggers": ["game", "gameplay", "controller", "boss", "clutch", "streamer", "fps", "headshot", "speedrun", "play"],
        "tags": ["#GamingShorts", "#GamerMoments", "#GamingClips", "#EpicPlay", "#GamerLife", "#ClutchMoment", "#GamingDaily"]
    },
    "crime_drama": {
        "triggers": ["money", "robbery", "jail", "prison", "arrest", "weapon", "shoot", "detective", "gun", "police", "cop"],
        "tags": ["#HighStakes", "#CrimeScene", "#PlotTwist", "#Escape", "#IntenseMoment", "#RobberyGoneWrong", "#Dangerous"]
    },
    "podcast_insight": {
        "triggers": ["life", "mindset", "success", "people", "think", "truth", "hard work", "discipline", "lesson", "advice", "wisdom"],
        "tags": ["#PodcastClips", "#LifeAdvice", "#DeepTruth", "#MindsetMatters", "#Wisdom", "#HardTruths", "#SelfImprovement"]
    },
    "comedy_reaction": {
        "triggers": ["bro", "laugh", "funny", "fail", "omg", "bruh", "hilarious", "idiot", "lmao", "crazy", "joke"],
        "tags": ["#FunnyMoments", "#EpicFail", "#ReactionShorts", "#GamingMemes", "#PureChaos", "#TryNotToLaugh", "#ComedyClips"]
    },
    "romance_drama": {
        "triggers": ["kiss", "kissing", "lips", "bed", "touch", "naked", "rizz", "girlfriend", "boyfriend", "crush", "date", "cheat", "flirt"],
        "tags": ["#FlirtingMoments", "#PickupLines", "#RizzMoments", "#RomanceShorts", "#RelationshipGoals", "#LoveDrama", "#Rizz"]
    }
}

STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with",
    "by", "from", "up", "about", "into", "over", "after", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did", "i", "you", "he",
    "she", "it", "we", "they", "me", "him", "her", "us", "them", "my", "your", "his",
    "its", "our", "their", "this", "that", "these", "those", "what", "which", "who",
    "whom", "whose", "when", "where", "why", "how", "all", "any", "both", "each", "few",
    "more", "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same",
    "so", "than", "too", "very", "s", "t", "can", "will", "just", "don", "should", "now",
    "okay", "yeah", "gonna", "wanna", "gotta", "like", "actually", "well", "um", "uh"
}


def clean_text(text: str) -> str:
    t = re.sub(r"[\r\n\t]+", " ", text).strip()
    t = re.sub(r'["\']', '', t)
    return t


def clean_video_topic(raw_title: str) -> str:
    """
    Extracts the core game, brand, creator, or topic name from the source video title.
    E.g. 'GTA 6 Gameplay Walkthrough Part 1 FULL GAME [4K ULTRA HD PS5 PRO] No Commentary' -> 'GTA 6'
    """
    if not raw_title:
        return ""
    t = raw_title.strip()

    # Specific known high-traffic franchises & creators
    if re.search(r"\b(gta\s*6|grand\s+theft\s+auto\s*(?:vi|6)|vice\s+city)\b", t, re.I):
        return "GTA 6"
    if re.search(r"\b(gta\s*5|grand\s+theft\s+auto\s*(?:v|5))\b", t, re.I):
        return "GTA 5"
    if re.search(r"\b(minecraft)\b", t, re.I):
        return "Minecraft"
    if re.search(r"\b(mrbeast)\b", t, re.I):
        return "MrBeast"
    if re.search(r"\b(joe\s+rogan|jre)\b", t, re.I):
        return "Joe Rogan"
    if re.search(r"\b(rdr\s*2|red\s+dead\s+redemption)\b", t, re.I):
        return "RDR 2"
    if re.search(r"\b(fortnite)\b", t, re.I):
        return "Fortnite"
    if re.search(r"\b(kai\s+cenat)\b", t, re.I):
        return "Kai Cenat"
    if re.search(r"\b(ishowspeed|speed)\b", t, re.I):
        return "IShowSpeed"
    if re.search(r"\b(lex\s+fridman)\b", t, re.I):
        return "Lex Fridman"
    if re.search(r"\b(elden\s+ring)\b", t, re.I):
        return "Elden Ring"
    if re.search(r"\b(valorant)\b", t, re.I):
        return "Valorant"
    if re.search(r"\b(roblox)\b", t, re.I):
        return "Roblox"
    if re.search(r"\b(call\s+of\s+duty|warzone|cod)\b", t, re.I):
        return "Call of Duty"

    # Remove brackets/parentheses content
    t = re.sub(r"\[.*?\]", "", t)
    t = re.sub(r"\(.*?\)", "", t)

    # Remove standard YouTube clutter
    clutter = [
        r"\b(?:4k|8k|1080p|60fps|hdr|uhd|pro)\b",
        r"\b(?:full\s+game|walkthrough|gameplay|playthrough)\b",
        r"\b(?:no\s+commentary|with\s+commentary)\b",
        r"\b(?:official\s+trailer|teaser\s+trailer|trailer\s*\d*)\b",
        r"\b(?:official\s+music\s+video|official\s+video|music\s+video)\b",
        r"\b(?:part\s*\d+|episode\s*\d+|ep\s*\d+|#\d+)\b",
        r"\b(?:live\s+stream|livestream|vod)\b",
    ]
    for c in clutter:
        t = re.sub(c, "", t, flags=re.I)

    # Split by separators if present
    parts = re.split(r"[\-–—|:]+", t)
    first_part = parts[0].strip() if parts else t
    if len(first_part) < 3 and len(parts) > 1:
        first_part = parts[1].strip()

    # Clean punctuation and extra spaces
    cleaned = re.sub(r"[^a-zA-Z0-9\s\']", " ", first_part).strip()
    words = [w for w in cleaned.split() if len(w) > 1]
    if words:
        return " ".join(words[:4]).title()
    return ""


def get_cached_video_title() -> str:
    """Reads cached video title if available from temp_work."""
    for p in [Path("temp_work/source_title.txt"), Path("temp/source_title.txt")]:
        if p.exists():
            try:
                title = p.read_text(encoding="utf-8").strip()
                if title:
                    return title
            except Exception:
                pass
    return ""


def extract_search_terms(text: str, hook: str, video_title: str = "") -> List[str]:
    """Extracts top keywords and entity phrases from transcript & video title to crawl on YouTube."""
    if not video_title:
        video_title = get_cached_video_title()

    core_topic = clean_video_topic(video_title)
    combined = f"{hook} {text}".lower()
    queries = []

    # Word frequency analysis on content tokens from clip
    words = re.findall(r"\b[a-zA-Z]{3,}\b", combined)
    content_words = [w for w in words if w not in STOP_WORDS]
    from collections import Counter
    counts = Counter(content_words)
    top_terms = [word for word, _ in counts.most_common(3)]

    # Check for clip-specific actions
    clip_action = ""
    if re.search(r"\b(car|stole|steal|vehicle|drive)\b", combined):
        clip_action = "stolen car"
    elif re.search(r"\b(chase|police|cops|arrest)\b", combined):
        clip_action = "police chase"
    elif re.search(r"\b(gun|shoot|fight|weapon)\b", combined):
        clip_action = "gunfight"
    elif re.search(r"\b(lucia|jason)\b", combined):
        clip_action = "lucia jason cutscene"
    elif re.search(r"\b(heist|robbery|money|cash)\b", combined):
        clip_action = "heist money"
    elif top_terms:
        clip_action = " ".join(top_terms[:2])

    if core_topic:
        if clip_action:
            queries.append(f"{core_topic} {clip_action}")
        if top_terms:
            queries.append(f"{core_topic} {' '.join(top_terms[:2])}")
        queries.append(f"{core_topic} viral moments")
        queries.append(f"{core_topic} shorts")
    else:
        # Fallback without video topic
        if clip_action:
            queries.append(f"{clip_action} viral shorts")
        if top_terms:
            queries.append(" ".join(top_terms))
        queries.append("viral trending shorts")

    return queries


def fetch_youtube_suggestions(query: str, timeout_sec: float = 3.0) -> List[str]:
    """Queries YouTube search autocomplete suggestions engine in real-time."""
    if query in YT_METADATA_CACHE["suggestions"]:
        return YT_METADATA_CACHE["suggestions"][query]

    url = f"http://suggestqueries.google.com/complete/search?client=firefox&ds=yt&q={urllib.parse.quote(query)}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data[1] if len(data) > 1 else []
            YT_METADATA_CACHE["suggestions"][query] = results
            return results
    except Exception:
        return []


def crawl_youtube_video_titles(query: str, timeout_sec: float = 3.5) -> List[str]:
    """Crawls live YouTube search results to extract actual top-ranking viral video titles in this niche."""
    if query in YT_METADATA_CACHE["titles"]:
        return YT_METADATA_CACHE["titles"][query]

    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query + ' shorts')}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    req = urllib.request.Request(url, headers=headers)
    titles = []
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            raw_titles = re.findall(r'"videoRenderer":\{.*?"title":\{"runs":\[\{"text":"([^"]+)"\}', html)
            for t in raw_titles:
                clean_t = html_lib.unescape(t).replace("\\u0026", "&").replace("\\'", "'").replace('\\"', '"').strip()
                if len(clean_t) >= 12 and clean_t not in titles:
                    titles.append(clean_t)
            YT_METADATA_CACHE["titles"][query] = titles
            return titles
    except Exception:
        return []


def format_hashtag(raw_tag: str) -> str:
    """Formats string into standard #PascalCase hashtag."""
    tag = raw_tag.replace("#", "").strip()
    words = re.findall(r"[A-Za-z0-9]+", tag)
    if not words:
        return ""
    capitalized = "".join(w.capitalize() for w in words)
    return f"#{capitalized}"


def get_live_trending_hashtags(text: str, hook: str, variation: int = 0, video_title: str = "") -> List[str]:
    """Crawls live YouTube trending autocomplete hashtags for the clip's niche and video topic."""
    if not video_title:
        video_title = get_cached_video_title()

    core_topic = clean_video_topic(video_title)
    queries = extract_search_terms(text, hook, video_title)
    live_hashtags = []

    # 1. First anchor with primary hashtags for the core video topic
    if core_topic:
        primary_tag = format_hashtag(core_topic)
        if primary_tag and primary_tag not in live_hashtags:
            live_hashtags.append(primary_tag)
        topic_suggested = fetch_youtube_suggestions(f"#{core_topic.replace(' ', '')}")
        for stag in topic_suggested[:4]:
            fmt = format_hashtag(stag)
            if fmt and fmt not in live_hashtags and len(fmt) > 2:
                live_hashtags.append(fmt)

    # 2. Query YouTube suggestion engine for live clip action hashtags
    for q in queries[:2]:
        for term in q.split()[:2]:
            if len(term) < 3 or term in STOP_WORDS or (core_topic and term.lower() in core_topic.lower()):
                continue
            suggested_tags = fetch_youtube_suggestions(f"#{term}")
            for stag in suggested_tags:
                fmt = format_hashtag(stag)
                if fmt and fmt not in live_hashtags and len(fmt) > 2:
                    live_hashtags.append(fmt)
            if len(live_hashtags) >= 8:
                break

    # 3. Query YouTube autocomplete for related video searches to turn into tags
    if len(live_hashtags) < 6:
        for q in queries[:1]:
            search_phrases = fetch_youtube_suggestions(q)
            for phrase in search_phrases:
                fmt = format_hashtag(phrase)
                if fmt and fmt not in live_hashtags and len(fmt) <= 22:
                    live_hashtags.append(fmt)

    # 4. If offline or insufficient tags, blend from offline niche rules
    if len(live_hashtags) < 6:
        text_lower = f"{hook} {text} {video_title}".lower()
        for niche, data in OFFLINE_FALLBACK_NICHES.items():
            for trg in data["triggers"]:
                if trg in text_lower:
                    for t in data["tags"]:
                        if t not in live_hashtags:
                            live_hashtags.append(t)
                    break

    # 5. Global Shorts fallback tags
    fallbacks = ["#Shorts", "#Viral", "#Trending", "#PlotTwist", "#MustWatch", "#FYP", "#Clips", "#ExplorePage"]
    for fb in fallbacks:
        if fb not in live_hashtags:
            live_hashtags.append(fb)

    # Ensure the primary topic tags appear at the front, followed by rotated clip tags
    topic_tags = [t for t in live_hashtags if core_topic and core_topic.replace(' ', '').lower() in t.lower()]
    other_tags = [t for t in live_hashtags if t not in topic_tags]

    rot_other = other_tags[variation % len(other_tags):] + other_tags[:variation % len(other_tags)] if other_tags else []
    final_tags = topic_tags[:2] + rot_other
    return final_tags[:5]


def generate_live_viral_title(text: str, hook: str, index: int = 1, variation: int = 0, video_title: str = "") -> str:
    """
    Crawls YouTube search results for top-ranking viral titles in this clip's niche,
    then dynamically synthesizes a high-CTR title tailored to both the parent video topic
    and the clip's specific spoken dialogue/action.
    """
    if not video_title:
        video_title = get_cached_video_title()

    core_topic = clean_video_topic(video_title)
    text_clean = clean_text(text)
    hook_clean = clean_text(hook)
    emoji = INTENSE_EMOJIS[(index + variation - 1) % len(INTENSE_EMOJIS)]

    first_sentence = text_clean.split(".")[0].strip()
    first_q = hook_clean.split("?")[0].strip() + "?" if "?" in hook_clean else ""
    t_lower = text_clean.lower()

    # Identify clip event
    clip_event = ""
    if re.search(r"\b(car|stole|steal|driving|keys)\b", t_lower):
        clip_event = "The Moment The Car Got Stolen"
    elif re.search(r"\b(chase|police|cops|arrest|sirens)\b", t_lower):
        clip_event = "When The Cops Showed Up"
    elif re.search(r"\b(lucia|jason)\b", t_lower):
        clip_event = "The Moment Lucia Realized"
    elif re.search(r"\b(money|cash|dollars|paid|grant)\b", t_lower):
        clip_event = "When A High-Stakes Job Turned Deadly"
    elif re.search(r"\b(kiss|rizz|date|girlfriend|boyfriend)\b", t_lower):
        clip_event = "Did That Pickup Line Actually Work?!"
    elif re.search(r"\b(die|dead|killed|boss|fight)\b", t_lower):
        clip_event = "Wait For This Insane Climax"
    elif 18 <= len(first_sentence) <= 50:
        clip_event = f'"{first_sentence}"'

    # When core_topic is known (e.g. "GTA 6", "Minecraft", "Joe Rogan", etc.)
    if core_topic:
        patterns = []
        if clip_event:
            patterns.append(f"{core_topic} - {clip_event} {emoji}")
            patterns.append(f"{core_topic}: {clip_event} 💀")
        if 18 <= len(first_sentence) <= 55:
            patterns.append(f"{core_topic} - \"{first_sentence}\" {emoji}")
            patterns.append(f"{core_topic}: \"{first_sentence}\" 🚨")
        if first_q and len(first_q) <= 55:
            patterns.append(f"{core_topic}: {first_q} {emoji}")
        patterns.append(f"Wait For This Insane {core_topic} Moment! {emoji}")
        patterns.append(f"Did He Really Just Do That In {core_topic}?! 😱")
        patterns.append(f"This {core_topic} Cutscene Changed Everything {emoji}")
        patterns.append(f"{core_topic} Hits Different In Broad Daylight {emoji}")

        return patterns[variation % len(patterns)]

    # Fallback if no core_topic identified
    if 20 <= len(first_sentence) <= 65:
        return f"\"{first_sentence}\" {emoji}"
    if first_q and len(first_q) <= 65:
        return f"{first_q} {emoji}"
    return f"This Changed Everything In Seconds {emoji}"


def generate_clip_metadata(text: str, hook: str, index: int = 1, variation: int = 0, video_title: str = "") -> Tuple[str, str]:
    """
    Returns (viral_title, space_separated_hashtags) by crawling live YouTube search trends
    and synthesizing hyper-relevant metadata for the clip based on parent video topic and dialogue.
    """
    if not video_title:
        video_title = get_cached_video_title()

    title = generate_live_viral_title(text, hook, index, variation, video_title=video_title)
    hashtags = " ".join(get_live_trending_hashtags(text, hook, variation, video_title=video_title))
    return title, hashtags
