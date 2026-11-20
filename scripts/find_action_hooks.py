import json
with open('temp_work/transcript.json', 'r', encoding='utf-8') as f:
    d = json.load(f)
segs = d['segments']
for s in segs:
    t = s['text'].lower()
    if any(k in t for k in ['robbery', 'hostage', 'police', 'hands up', 'get down', 'shoot', 'gun', 'cops', 'run', 'money']) and 300 < s['start'] < 3000:
        print(f"{s['start']:.1f} - {s['end']:.1f}: {s['text'][:90]}")
