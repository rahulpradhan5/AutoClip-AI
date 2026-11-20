# Reusable Universal Clipper Prompt Template

Copy and paste this into chat whenever needed, replacing only the URL and number of clips:

```text
Prompt 2

Use the `universal-clipper` skill to process this link into viral 9:16 vertical Shorts/Reels:

INPUT SETTINGS:
• Video / Stream / Reel URL: [PASTE_LINK_HERE]
• Number of Clips: 3 clips (25 to 55 seconds each)
• Framing Mode: Auto-Detect (Split-Screen for Streamers, Stacked/Speaker-Focus for Podcasts, or Full-Screen 9:16 Crop for Challenges/Vlogs/Tech)
• Captions: Word-by-Word Karaoke (Auto-detect spoken language)

EXECUTION PIPELINE:
1. Download the video and extract its transcript/audio into `temp_work/`.
2. Identify the 3 highest-retention viral moments based on the video's niche (biggest twist/stakes, funniest reaction, strongest insight, or sharpest debate). Ensure clean sentence boundaries.
3. Render all 3 clips into `final_clips/` (`clip_1.mp4`, `clip_2.mp4`, `clip_3.mp4`) at `1080x1920`, 60 FPS, with burned-in karaoke captions and verified 0ms audio-video sync.
4. Delete all raw downloads and temporary files from `temp_work/`.
5. Print a summary in chat showing each clip's filename, exact timestamps, opening hook line, detected framing layout, and a viral title + 5 niche-matched hashtags.
```

---

### Even Faster: Zero-Prompt Shortcuts!
You don't even need to paste the full prompt anymore! You can use any of these 1-line shortcuts:

1. **Edit and Say "clip":**
   Edit [CLIP_INPUT.txt](file:///c:/Users/prahu/Desktop/CLIPS/CLIP_INPUT.txt) with your URL and number of clips, then simply tell me in chat:
   > **`clip`** or **`run CLIP_INPUT.txt`**

2. **Single-line Chat Command:**
   Simply tell me in chat:
   > **`clip https://www.youtube.com/watch?v=... 3`**

3. **Terminal / Batch:**
   Run from PowerShell or Command Prompt:
   ```powershell
   .\clip.bat
   ```
