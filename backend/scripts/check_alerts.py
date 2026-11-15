"""Check every saved area's alert rules once. Run on a schedule, e.g. hourly with cron:

    0 * * * *  cd /path/to/firewatch360 && backend/.venv/bin/python backend/scripts/check_alerts.py

New events go to the in-app inbox and each area's webhook / e-mail. Repeats are not re-sent.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.routes import check_areas  # noqa: E402
from app.main import create_app  # noqa: E402

if __name__ == "__main__":
    out = check_areas(create_app())
    print(f"checked {out['checked']} areas, {len(out['new_events'])} new events")
    for e in out["new_events"]:
        print(f"- {e['area_name']}: {e['title']} ({e['delivery']})")
