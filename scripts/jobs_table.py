"""Print a compact table of jobs from the API (used during manual testing)."""
import json
import sys
import urllib.request

base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
data = json.load(urllib.request.urlopen(f"{base}/v1/jobs?limit=200"))
for j in reversed(data["items"]):
    e = j["error"]
    print(f"{j['status']:10} {j['document']['filename'][:26]:26} {j['document']['kind']:6} p={j['page_count']} "
          f"chars={j['char_count']} t={j['duration_s']} lang={(j['language'] or {}).get('code')} "
          f"err={e and e['code']} {(e and e['message'][:100]) or ''} W={[w[:80] for w in j['warnings']]}")
