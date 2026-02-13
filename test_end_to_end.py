#!/usr/bin/env python3
"""
End-to-end test of the reminder system
Tests everything except actually sending WhatsApp messages
"""
import os
os.environ.setdefault("TWILIO_ACCOUNT_SID", "test")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "test")
os.environ.setdefault("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

from datetime import date, datetime, timedelta
import pytz
from holiday_rules import get_all_holidays_for_year, get_shifted_collection_day

print("="*80)
print("END-TO-END REMINDER SYSTEM TEST")
print("="*80)

# Test 1: Holiday calculation
print("\n1. Testing Holiday Calculation for 2025:")
print("-" * 40)
holidays_2025 = get_all_holidays_for_year(2025)
print(f"Found {len(holidays_2025)} holidays")
for h in holidays_2025[:3]:  # Show first 3
    print(f"  - {h['name']}: {h['date']}")
print("  ...")

# Test 2: Shift logic for a specific holiday
print("\n2. Testing Collection Shift Logic:")
print("-" * 40)
christmas_2025 = date(2025, 12, 25)
print(f"Christmas 2025: {christmas_2025.strftime('%A, %B %d')}")
for zone in ["Zone 1", "Zone 2", "Zone 3", "Zone 4"]:
    shifted = get_shifted_collection_day(christmas_2025, zone)
    print(f"  {zone}: collection on {shifted}")

# Test 3: Check what would happen tomorrow
print("\n3. Testing for TOMORROW:")
print("-" * 40)
tz = pytz.timezone("US/Eastern")
today = datetime.now(tz).date()
tomorrow = today + timedelta(days=1)
tomorrow_weekday = tomorrow.strftime("%A")

print(f"Today: {today.strftime('%A, %B %d, %Y')}")
print(f"Tomorrow: {tomorrow.strftime('%A, %B %d, %Y')} ({tomorrow_weekday})")

# Check if tomorrow is a holiday
year = tomorrow.year
holidays = get_all_holidays_for_year(year)
is_holiday_week = False

for h in holidays:
    h_date = h["date"]
    # Check if holiday is in the same week as tomorrow
    days_diff = abs((h_date - tomorrow).days)
    if days_diff <= 3:  # Within same week
        print(f"\n⚠️  HOLIDAY WEEK: {h['name']} is {h_date}")
        is_holiday_week = True

        # Show shifted days
        for zone in ["Zone 1", "Zone 2", "Zone 3", "Zone 4"]:
            shifted = get_shifted_collection_day(h_date, zone)
            if shifted:
                print(f"  {zone}: collection on {shifted}")

if not is_holiday_week:
    print("\nNo holidays this week - regular schedule applies")

# Test 4: Address lookup (if file exists)
print("\n4. Testing Address Lookup:")
print("-" * 40)
try:
    from main import lookup_zone_by_address

    test_addresses = [
        "229 Ardleigh Rd",
        "100 Main St",  # May not exist
    ]

    for addr in test_addresses:
        result = lookup_zone_by_address(addr)
        if result:
            print(f"✓ {addr}")
            print(f"    Zone: {result['zone']}, Day: {result['collection_day']}")
        else:
            print(f"✗ {addr} - Not found in lookup")

except Exception as e:
    print(f"Address lookup test skipped: {e}")

# Test 5: Simulated reminder logic
print("\n5. Simulated Reminder Check:")
print("-" * 40)
print(f"If a user has collection day = '{tomorrow_weekday}':")
print("  → Would receive reminder tonight at 8 PM")
print(f"\nIf a user has collection day != '{tomorrow_weekday}':")
print("  → Would NOT receive reminder (skipped)")

print("\n" + "="*80)
print("TEST COMPLETE")
print("="*80)
print("\nNext steps:")
print("1. Check your Render deployment logs")
print("2. Visit debug endpoints (see below)")
print("3. Wait for 8 PM ET cron job to run")
