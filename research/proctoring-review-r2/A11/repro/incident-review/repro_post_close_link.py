"""A11 isolated check: A05 'updated after closed' (link) change -> A08 store keeps closed state + new links."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import repro_engine_store as R
spec = {"name": "a11_post_close_link", "mode": "synthetic",
        "phone": [{"from": 0, "to": 2000, "step": 125, "visible": "absent"},
                  {"from": 2000, "to": 4000, "step": 125, "visible": "present"},
                  {"from": 4000, "to": 14000, "step": 125, "visible": "absent"}],
        "attention": [{"from": 0, "to": 3500, "step": 100, "direction": "center"},
                      {"from": 3500, "to": 9500, "step": 100, "direction": "down"},
                      {"from": 9500, "to": 14000, "step": 100, "direction": "center"}],
        "finish": 14000, "expected": []}
p = Path(__file__).parent / "tmp" / "a11_post_close_link.json"
p.write_text(json.dumps(spec))
print(json.dumps(R.run(p), ensure_ascii=False))
