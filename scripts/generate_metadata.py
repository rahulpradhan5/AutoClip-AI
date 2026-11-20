"""
Intelligent Metadata Generator for Universal Clipper.
Analyzes transcript text, detects niche, entities, and emotional tone,
and generates high-retention viral titles and 5 unique niche-matched hashtags.
Supports multi-variation regeneration for creative exploration.
"""

import random
import re
from typing import Tuple, List

NICHE_KEYWORDS = {
    "gta": {
        "triggers": ["gta", "vice city", "lucia", "jason", "grand theft auto", "rockstar", "leonida", "heist", "wanted", "police", "cop", "car", "drive", "stole", "steal"],
        "tag_pool": ["#GTA6", "#ViceCity", "#GTA6Moments", "#LuciaAndJason", "#RockstarGames", "#GTAViral", "#GrandTheftAuto", "#GTA6Gameplay", "#GamingShorts", "#StoryCutscene"]
    },
    "gaming": {
        "triggers": ["game", "gameplay", "controller", "boss", "level", "fps", "streamer", "clutch", "headshot", "kill", "speedrun", "play", "won", "died", "spawn"],
        "tag_pool": ["#GamingShorts", "#GamerMoments", "#GamingClips", "#EpicPlay", "#GamerLife", "#ClutchMoment", "#GamingCommunity", "#ProGamer", "#GamingDaily"]
    },
    "crime_drama": {
        "triggers": ["money", "robbery", "jail", "prison", "arrest", "steal", "run", "weapon", "shoot", "detective", "gun", "caught", "cop", "busted"],
        "tag_pool": ["#HighStakes", "#CrimeScene", "#PlotTwist", "#Escape", "#IntenseMoment", "#RobberyGoneWrong", "#Dangerous", "#Underworld", "#ActionShorts"]
    },
    "podcast_insight": {
        "triggers": ["life", "mindset", "success", "people", "money", "think", "truth", "hard work", "discipline", "lesson", "advice", "never", "rules", "learn"],
        "tag_pool": ["#PodcastClips", "#LifeAdvice", "#DeepTruth", "#MindsetMatters", "#Wisdom", "#HardTruths", "#SelfImprovement", "#MotivationDaily", "#DeepTalk"]
    },
    "comedy_reaction": {
        "triggers": ["bro", "laugh", "funny", "fail", "omg", "no way", "what the", "bruh", "hilarious", "idiot", "lmao", "joke", "crying", "crazy"],
        "tag_pool": ["#FunnyMoments", "#EpicFail", "#ReactionShorts", "#GamingMemes", "#PureChaos", "#TryNotToLaugh", "#LaughOutLoud", "#UnrealFail", "#ComedyClips"]
    },
    "mystery_conspiracy": {
        "triggers": ["secret", "hidden", "truth", "nobody knows", "conspiracy", "exposed", "mystery", "warning", "aliens", "evidence", "proof"],
        "tag_pool": ["#HiddenSecret", "#TheTruthExposed", "#ConspiracyTheory", "#MindBlowing", "#MysteryUnsolved", "#DeepDive", "#ShockingTruth", "#Unexplained"]
    },
    "romance_intimate": {
        "triggers": ["sex", "sexy", "kiss", "kissing", "lips", "bed", "bedroom", "touch", "naked", "hook up", "pick up", "pickup line", "pick up line", "rizz", "girlfriend", "boyfriend", "crush", "date", "dating", "single", "number", "fine", "cheat", "cheating", "flirt", "flirting", "love", "attracted", "cute", "babe", "make out", "pyaar", "ishq", "chumma"],
        "tag_pool": ["#FlirtingMoments", "#PickupLines", "#RizzMoments", "#RomanceShorts", "#RelationshipGoals", "#IntenseChemistry", "#LoveDrama", "#DatingAdvice", "#AwkwardRomance", "#CoupleMoments", "#Rizz"]
    },
    "tech_ai": {
        "triggers": ["ai", "tech", "computer", "phone", "apple", "google", "software", "update", "feature", "code", "model", "future", "robot"],
        "tag_pool": ["#TechShorts", "#FutureTech", "#AIRevolution", "#TechTrends", "#Innovation", "#Gadgets", "#SoftwareDev", "#AITools"]
    }
}

INTENSE_EMOJIS = ["🤯", "🚨", "😱", "🔥", "💀", "⚡", "👀", "💥", "🛑", "⚠️"]

def clean_sentence(text: str) -> str:
    text = re.sub(r'[\r\n]+', ' ', text).strip()
    text = re.sub(r'["\']', '', text)
    return text

def extract_entities_and_topics(text: str, variation: int = 0) -> List[str]:
    text_lower = text.lower()
    matched_tags = []

    # Entity priority
    if re.search(r"\b(lucia)\b", text_lower):
        matched_tags.append("#Lucia")
    if re.search(r"\b(jason)\b", text_lower):
        matched_tags.append("#Jason")
    if re.search(r"\b(vice city|miami)\b", text_lower):
        matched_tags.append("#ViceCity")
    if re.search(r"\b(gta|grand theft auto)\b", text_lower):
        matched_tags.append("#GTA6")
    if re.search(r"\b(police|cops?|arrest)\b", text_lower):
        matched_tags.append("#PoliceChase")
    if re.search(r"\b(money|cash|dollars?)\b", text_lower):
        matched_tags.append("#BigMoney")
    if re.search(r"\b(betray|liar|snitch|trust)\b", text_lower):
        matched_tags.append("#Betrayal")
    if re.search(r"\b(secret|hidden|truth)\b", text_lower):
        matched_tags.append("#TheTruth")
    if re.search(r"\b(kiss|kissing|kissed|smooch|make out)\b", text_lower):
        matched_tags.append("#KissScene")
    if re.search(r"\b(rizz|pickup line|pick up line|single|date me)\b", text_lower):
        matched_tags.append("#Rizz")
    if re.search(r"\b(sex|sexy|sexual|in bed|bedroom|naked|hook up)\b", text_lower):
        matched_tags.append("#IntimateMoment")
    if re.search(r"\b(cheat|cheated|cheating|affair|side chick)\b", text_lower):
        matched_tags.append("#CaughtCheating")

    # Match predefined niches
    for niche, data in NICHE_KEYWORDS.items():
        for trigger in data["triggers"]:
            if re.search(r"\b" + re.escape(trigger) + r"\b", text_lower):
                pool = data["tag_pool"]
                # Shift pool by variation to produce fresh tags on regenerate
                shifted_pool = pool[variation % len(pool):] + pool[:variation % len(pool)]
                for t in shifted_pool:
                    if t not in matched_tags:
                        matched_tags.append(t)
                break

    # Fallback pool
    fallback_pool = ["#Shorts", "#Viral", "#Trending", "#PlotTwist", "#MustWatch", "#FYP", "#Clips", "#ExplorePage"]
    shifted_fallbacks = fallback_pool[variation % len(fallback_pool):] + fallback_pool[:variation % len(fallback_pool)]
    for fb in shifted_fallbacks:
        if fb not in matched_tags:
            matched_tags.append(fb)

    # Return exactly 5 niche-matched tags
    return matched_tags[:5]


def generate_viral_title(text: str, hook: str, index: int = 1, variation: int = 0) -> str:
    text_clean = clean_sentence(text)
    hook_clean = clean_sentence(hook)
    t_lower = text_clean.lower()
    h_lower = hook_clean.lower()

    emoji = INTENSE_EMOJIS[(index + variation - 1) % len(INTENSE_EMOJIS)]

    first_sentence = text_clean.split(".")[0].strip()
    first_q = hook_clean.split("?")[0].strip() + "?" if "?" in hook_clean else ""

    # Variation 0: Quote / Core conflict
    if variation == 0:
        if 20 <= len(first_sentence) <= 65:
            return f"\"{first_sentence}\" {emoji}"
        if first_q and len(first_q) <= 65:
            return f"{first_q} {emoji}"
        if re.search(r"\b(kiss|kissing|kissed|lips|make out|smooch)\b", t_lower):
            return f"The Tension Was Unreal Before That Kiss 💋"
        if re.search(r"\b(sex|sexy|sexual|in bed|bedroom|sleep together|naked|hook up)\b", t_lower):
            return f"They Really Talked About That On Camera... 😳🔥"
        if re.search(r"\b(pick up line|pickup line|rizz|take you out|date me|give me your number|you're so fine)\b", t_lower):
            return f"Unstoppable Rizz Or Total Cringe? 😂🔥"
        if re.search(r"\b(cheat|cheated|cheating|side piece|side chick|affair|who is she)\b", t_lower):
            return f"Caught In 4K: The Relationship Just Ended 🚨💔"
        if "lucia" in t_lower:
            return f"The Moment Lucia Realized It's Real {emoji}"
        if "vice city" in t_lower or "gta" in t_lower:
            return f"The Cold Reality of Vice City {emoji}"
        if re.search(r"\b(police|cops?|arrest|caught|jail)\b", t_lower):
            return f"The Exact Moment Everything Went Wrong... {emoji}"
        if re.search(r"\b(money|stole|steal|heist|cash)\b", t_lower):
            return f"When A High-Stakes Job Turns Deadly {emoji}"

    # Variation 1: Curiosity gap question
    elif variation == 1:
        if first_q and len(first_q) <= 65:
            return f"{first_q} {emoji}"
        if re.search(r"\b(kiss|kissing|lips|make out|crush)\b", t_lower):
            return f"Did They Actually Just Kiss On Stream?! 😳👀"
        if re.search(r"\b(pick up line|pickup line|rizz|date me|single)\b", t_lower):
            return f"Did That Pickup Line Actually Work?! 💀👀"
        if "lucia" in t_lower or "jason" in t_lower:
            return f"Can They Really Trust Each Other?! {emoji}"
        if re.search(r"\b(stole|steal|car|money)\b", t_lower):
            return f"Did He Actually Steal That In Broad Daylight?! {emoji}"
        return f"Did They Really Just Say That Out Loud?! {emoji}"

    # Variation 2: High Stakes / Plot Twist angle
    elif variation == 2:
        if re.search(r"\b(run|escape|caught|trapped)\b", t_lower):
            return f"No Way Out: The Ultimate Confrontation {emoji}"
        if re.search(r"\b(liar|lied|betray|trust)\b", t_lower):
            return f"The Ultimate Betrayal Caught On Camera {emoji}"
        return f"Wait For The Plot Twist At The End! {emoji}"

    # Variation 3+: Shock & Chaos angle
    else:
        if re.search(r"\b(bro|omg|what the|are you serious)\b", t_lower):
            return f"His Reaction Was Completely Unhinged... {emoji}"
        return f"Nobody Saw This Coming In 2026 {emoji}"

    # Fallback
    return f"This Changed Everything In Seconds {emoji}"


def generate_clip_metadata(text: str, hook: str, index: int = 1, variation: int = 0) -> Tuple[str, str]:
    """Returns (viral_title, space_separated_hashtags)"""
    title = generate_viral_title(text, hook, index, variation)
    hashtags = " ".join(extract_entities_and_topics(text, variation))
    return title, hashtags
