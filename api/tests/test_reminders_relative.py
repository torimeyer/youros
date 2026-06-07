import sys
import os
sys.path.append(os.path.join(os.getcwd(), "api"))
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from services.reminders import parse_reminder

def test_parse_relative_time():
    now = datetime(2026, 6, 6, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
    # Currently expected to FAIL (defaults to 9am tomorrow or similar)
    parsed = parse_reminder("remind me to call Tori in 30 minutes", tz="UTC", now=now)
    expected_fire = now + timedelta(minutes=30)
    
    print(f"Text: {parsed['text']}")
    print(f"Fire at UTC: {parsed['fire_at_utc']}")
    print(f"Expected: {expected_fire}")
    
    assert parsed["text"].lower() == "call tori"
    assert parsed["fire_at_utc"] == expected_fire

if __name__ == "__main__":
    test_parse_relative_time()
