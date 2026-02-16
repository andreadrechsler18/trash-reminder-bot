# cron.py
import sys
from datetime import datetime
import pytz
from main import send_weekly_reminders

def should_run_now():
    """Run at 8:00 PM US/Eastern, Sunday–Thursday (nights before Mon–Fri pickups)."""
    tz = pytz.timezone("US/Eastern")
    now_et = datetime.now(tz)

    # Python weekday: Mon=0 … Sun=6
    # Run Sun (6) through Thu (3) at 20:00 ET
    if now_et.weekday() in (6, 0, 1, 2, 3) and now_et.hour == 20:
        return True
    return False

def main():
    print("🚀 Cron start:", datetime.now(pytz.UTC).isoformat(), "UTC", flush=True)

    if should_run_now():
        try:
            send_weekly_reminders()
            print("✅ Reminders sent", flush=True)
            sys.exit(0)
        except Exception as e:
            print(f"❌ Error in send_weekly_reminders: {e}", flush=True)
            sys.exit(1)
    else:
        print("⏭️ Skipped (outside schedule window).", flush=True)
        sys.exit(0)

if __name__ == "__main__":
    main()
