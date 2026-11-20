"""
Intelligent Dynamic Metadata & YouTube Niche Crawler for Universal Clipper.
Crawls live YouTube search autocomplete trends and top-performing viral video titles
for the specific topic of each clip, extracting real trending niche hashtags and CTR-optimized titles.
Falls back seamlessly to offline NLP heuristics if network is unavailable.
"""

import html as html_lib
import json
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


def extract_search_terms(text: str, hook: str) -> List[str]:
    """Extracts top keywords and entity phrases from transcript to crawl on YouTube."""
    combined = f"{hook} {text}".lower()
    # Check for known prominent entities first
    queries = []
    if "gta" in combined or "vice city" in combined or "lucia" in combined or "jason" in combined:
        if "stole" in combined or "car" in combined:
            queries.append("gta 6 stole car")
        if "lucia" in combined:
            queries.append("gta 6 lucia")
        queries.append("gta 6 viral moments")
    elif "podcast" in combined or "mindset" in combined or "lesson" in combined or "discipline" in combined:
        queries.append("podcast life lessons mindset")
    elif "kiss" in combined or "rizz" in combined or "date" in combined or "cheat" in combined:
        queries.append("viral rizz moments shorts")
    elif "fail" in combined or "funny" in combined or "laugh" in combined or "crazy" in combined:
        queries.append("funny viral fails shorts")

    # Word frequency analysis on content tokens
    words = re.findall(r"\b[a-zA-Z]{3,}\b", combined)
    content_words = [w for w in words if w not in STOP_WORDS]

    # Pick top 2-3 most frequent significant terms
    from collections import Counter
    counts = Counter(content_words)
    top_terms = [word for word, _ in counts.most_common(3)]
    if top_terms:
        queries.append(" ".join(top_terms))

    if not queries:
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
            # Scrape video title text from videoRenderer runs
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
    # Split into words and capitalize
    words = re.findall(r"[A-Za-z0-9]+", tag)
    if not words:
        return ""
    capitalized = "".join(w.capitalize() for w in words)
    return f"#{capitalized}"


def get_live_trending_hashtags(text: str, hook: str, variation: int = 0) -> List[str]:
    """Crawls live YouTube trending autocomplete hashtags for the clip's niche."""
    queries = extract_search_terms(text, hook)
    live_hashtags = []

    # 1. Query YouTube suggestion engine for live hashtags
    for q in queries[:2]:
        for term in q.split()[:2]:
            if len(term) < 3 or term in STOP_WORDS:
                continue
            suggested_tags = fetch_youtube_suggestions(f"#{term}")
            for stag in suggested_tags:
                fmt = format_hashtag(stag)
                if fmt and fmt not in live_hashtags and len(fmt) > 2:
                    live_hashtags.append(fmt)
            if len(live_hashtags) >= 8:
                break

    # 2. Query YouTube autocomplete for related video searches to turn into tags
    if len(live_hashtags) < 5:
        for q in queries[:1]:
            search_phrases = fetch_youtube_suggestions(q)
            for phrase in search_phrases:
                fmt = format_hashtag(phrase)
                if fmt and fmt not in live_hashtags and len(fmt) <= 22:
                    live_hashtags.append(fmt)

    # 3. If offline or insufficient tags, blend from offline niche rules
    if len(live_hashtags) < 5:
        text_lower = f"{hook} {text}".lower()
        for niche, data in OFFLINE_FALLBACK_NICHES.items():
            for trg in data["triggers"]:
                if trg in text_lower:
                    for t in data["tags"]:
                        if t not in live_hashtags:
                            live_hashtags.append(t)
                    break

    # 4. Global Shorts fallback tags
    fallbacks = ["#Shorts", "#Viral", "#Trending", "#PlotTwist", "#MustWatch", "#FYP", "#Clips", "#ExplorePage"]
    for fb in fallbacks:
        if fb not in live_hashtags:
            live_hashtags.append(fb)

    # Rotate with variation so regenerations give fresh new trending hashtag combinations
    rotated = live_hashtags[variation % len(live_hashtags):] + live_hashtags[:variation % len(live_hashtags)]
    return rotated[:5]


def generate_live_viral_title(text: str, hook: str, index: int = 1, variation: int = 0) -> str:
    """
    Crawls YouTube search results for top-ranking viral titles in this clip's niche,
    then dynamically synthesizes a high-CTR title tailored to the spoken dialogue.
    """
    text_clean = clean_text(text)
    hook_clean = clean_text(hook)
    queries = extract_search_terms(text_clean, hook_clean)
    emoji = INTENSE_EMOJIS[(index + variation - 1) % len(INTENSE_EMOJIS)]

    # 1. Crawl live YouTube search results for top titles in this niche
    crawled_titles = []
    for q in queries[:2]:
        c = crawl_youtube_video_titles(q)
        if c:
            crawled_titles.extend(c)

    first_sentence = text_clean.split(".")[0].strip()
    first_q = hook_clean.split("?")[0].strip() + "?" if "?" in hook_clean else ""

    # Variation 0: Authentic punchline dialogue quote
    if variation == 0:
        if 20 <= len(first_sentence) <= 65:
            return f"\"{first_sentence}\" {emoji}"
        if first_q and len(first_q) <= 65:
            return f"{first_q} {emoji}"

    # Variation 1: Curiosity gap question
    elif variation == 1:
        if first_q and len(first_q) <= 65:
            return f"{first_q} {emoji}"
        # If we have crawled YouTube titles, adapt a high-ranking title question pattern
        for yt_title in crawled_titles:
            if "?" in yt_title and len(yt_title) <= 68:
                return f"{yt_title.split('?')[0]}?! {emoji}"

    # Variation 2: High Stakes / Plot Twist angle derived from crawled top performers
    elif variation == 2:
        if crawled_titles:
            # Pick a crawled viral title that has high impact terms
            for yt_title in crawled_titles:
                # Remove channel names or hashtags from title
                clean_yt = re.sub(r"#\w+", "", yt_title)
                clean_yt = re.sub(r"\[.*?\]|\(.*?\)", "", clean_yt).strip()
                if 25 <= len(clean_yt) <= 65 and not clean_yt.lower().startswith("live"):
                    return f"{clean_yt} {emoji}"

    # Variation 3+: Shock & Chaos angle
    elif variation >= 3:
        if crawled_titles and len(crawled_titles) > 2:
            alt_title = crawled_titles[variation % len(crawled_titles)]
            clean_alt = re.sub(r"#\w+", "", alt_title).strip()
            if 25 <= len(clean_alt) <= 68:
                return f"{clean_alt} {emoji}"

    # Fallback to intelligent transcript-derived punchy titles
    t_lower = text_clean.lower()
    if re.search(r"\b(car|stole|steal|driving|keys)\b", t_lower):
        patterns = [
            f"The Exact Moment The Car Got Stolen {emoji}",
            f"Wait For Her Reaction To The Stolen Car! 💀",
            f"\"It's Actually Not My Car...\" {emoji}",
            f"Did He Really Just Steal That In Broad Daylight?! {emoji}"
        ]
        return patterns[variation % len(patterns)]

    if re.search(r"\b(lucia|jason)\b", t_lower):
        patterns = [
            f"The Moment Lucia Realized What's Coming {emoji}",
            f"Can They Really Trust Each Other In Vice City? 🚨",
            f"This Cutscene Changed Everything {emoji}"
        ]
        return patterns[variation % len(patterns)]

    if re.search(r"\b(money|cash|dollars|paid|grant)\b", t_lower):
        patterns = [
            f"When A High-Stakes Job Turns Deadly {emoji}",
            f"\"Should've Granted Cash!\" {emoji}",
            f"They Actually Got Away With All That Money?! 🤯"
        ]
        return patterns[variation % len(patterns)]

    if re.search(r"\b(kiss|rizz|date|girlfriend|boyfriend)\b", t_lower):
        return f"Did That Pickup Line Actually Work?! 😳🔥"

    # Default fallback
    if 18 <= len(first_sentence) <= 65:
        return f"\"{first_sentence}\" {emoji}"
    return f"This Changed Everything In Seconds {emoji}"


def generate_clip_metadata(text: str, hook: str, index: int = 1, variation: int = 0) -> Tuple[str, str]:
    """
    Returns (viral_title, space_separated_hashtags) by crawling live YouTube search trends
    and synthesizing hyper-relevant metadata for the clip.
    """
    title = generate_live_viral_title(text, hook, index, variation)
    hashtags = " ".join(get_live_trending_hashtags(text, hook, variation))
    return title, hashtags
