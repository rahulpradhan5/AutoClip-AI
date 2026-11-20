import json
with open('temp_work/transcript.json', 'r', encoding='utf-8') as f:
    d = json.load(f)
segs = [s for s in d['segments'] if 684 <= s['start'] <= 745]
for s in segs:
    print(f"{s['start']} - {s['end']}: {s['text']}")
