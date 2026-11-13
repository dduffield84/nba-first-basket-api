import os, json, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
H = {"X-API-Key": os.environ["EDGE_WRITE_KEY"], "Content-Type": "application/json"}
def post(p, b):
    r = urllib.request.Request("http://127.0.0.1:8765" + p, json.dumps(b).encode(), H)
    return json.load(urllib.request.urlopen(r))
now = datetime.now(timezone.utc)
day = now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
start = now + timedelta(hours=3)
teams = [("BOS","NYK"),("LAL","GSW"),("MIA","CHI"),("DEN","PHX"),("DAL","OKC")]
es = []
for i,(a,h) in enumerate(teams):
    es.append({"entry_key": f"demo-{i}", "kind": "prediction", "sport": "nba",
      "market": "nba_first_fg", "event_date": day, "event_label": f"{a}@{h}",
      "home": h, "away": a, "event_start_utc": start.isoformat(),
      "selection": f"Test Player {i}", "selection_id": str(i),
      "model_prob": 0.14 + i*0.02, "model_version": "v7-test",
      "odds_american": 550 - i*50, "decision": "BET" if i%2==0 else "PASS",
      "predicted_at": (now - timedelta(minutes=5)).isoformat(),
      "source": "local web test"})
past = now - timedelta(days=3)
pday = past.date().isoformat()
for i in range(6):
    es.append({"entry_key": f"past-{i}", "kind": "prediction", "sport": "nba",
      "market": "nba_first_fg", "event_date": pday, "event_label": f"T{i}@U{i}",
      "home": f"U{i}", "away": f"T{i}", "event_start_utc": past.isoformat(),
      "selection": f"Old Player {i}", "selection_id": str(i), "model_prob": 0.2,
      "model_version": "v7-test", "odds_american": 450,
      "predicted_at": (past - timedelta(hours=2)).isoformat(),
      "source": "local web test"})
print(post("/ledger/entries", {"entries": es}))
print(post("/ledger/results", {"results": [{"entry_key": f"past-{i}",
  "outcome": "win" if i < 2 else "loss", "settled_at": (past+timedelta(hours=3)).isoformat(),
  "source": "local web test"} for i in range(6)]}))
print(day)
