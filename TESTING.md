# Testing Guide for Trash Reminder Bot

## Quick Local Tests

### 1. Test Holiday Calculation
```bash
cd /c/Users/andre/trash-reminder-bot
python test_auto_holidays.py
```
**What to verify:**
- Shows 8 holidays for 2025, 2026, 2027
- Dates are correct (e.g., Memorial Day is last Monday in May)
- Zone 3 shifts look reasonable

### 2. Test Holiday Shift Rules
```bash
python test_holiday_rules.py
```
**What to verify:**
- Christmas (Thursday) → Zone 3 shifts to Wednesday
- Each zone has proper shifted days

### 3. Test Address Lookup
```bash
python -c "from main import lookup_zone_by_address; print(lookup_zone_by_address('229 Ardleigh Rd'))"
```
**Expected output:**
```
{'zone': 'Zone 3', 'collection_day': 'Thursday'}
```

## Testing on Render (Production)

### Your Render App URL
Replace `YOUR-APP-NAME` with your actual Render web service name:
```
https://YOUR-APP-NAME.onrender.com
```

### Debug Endpoints

#### 1. Check Subscribers
```
https://YOUR-APP-NAME.onrender.com/subscribers_debug
```
**Shows:**
- How many subscribers
- Source (Google Sheet or memory)
- Sample subscriber data

#### 2. Test Holiday Logic for Specific Date
```
https://YOUR-APP-NAME.onrender.com/holiday_scrape_debug?iso=2025-12-25&zone=Zone%203
```
**Replace:**
- `iso=2025-12-25` with any date
- `zone=Zone%203` with Zone 1, 2, 3, or 4

**Expected output:**
```json
{
  "iso": "2025-12-25",
  "zone": "Zone 3",
  "year": 2025,
  "holiday_note": "Christmas Day: collection on Wednesday.",
  "all_entries": [...],
  "entry_count": 8
}
```

#### 3. Check Google Sheet Connection
```
https://YOUR-APP-NAME.onrender.com/csv_debug
```
**Shows:**
- If Sheet URL is configured
- Sheet headers
- First few rows

#### 4. Health Check
```
https://YOUR-APP-NAME.onrender.com/health
```
**Should return:**
```json
{"status": "ok"}
```

## Testing the Cron Job

### View Cron Logs on Render

1. Go to https://dashboard.render.com/
2. Click on your **Cron Job** service (not the Web Service)
3. Click "Logs" tab
4. Look for entries around 8:00 PM ET

**What to look for:**
- `"Loading subscribers..."`
- `"Loaded X addresses from lookup file"`
- `"No subscribers found"` (if no one signed up yet)
- OR successful message sends

### Manually Trigger Cron Job (Testing)

**Option A: Via Render Dashboard**
1. Go to your Cron Job service
2. Click "Manual Jobs" → "Trigger Job"
3. Wait 1-2 minutes
4. Check logs

**Option B: Via Code (Temporary Test)**

Add this test endpoint to main.py (REMOVE AFTER TESTING):
```python
@app.route("/test_reminders")
def test_reminders():
    """TEST ONLY - Remove in production"""
    from main import send_weekly_reminders
    results = send_weekly_reminders()
    return jsonify({"results": results})
```

Then visit: `https://YOUR-APP-NAME.onrender.com/test_reminders`

⚠️ **WARNING:** This will send REAL WhatsApp messages! Only use for testing.

## Testing With a Real Subscriber

### Step 1: Add Yourself as Test Subscriber

Fill out your Google Form with:
- **Address:** 229 Ardleigh Rd (or your real address)
- **Phone:** Your phone number (format: +1234567890)
- **Consent:** Agree

### Step 2: Verify You Were Added

Check: `https://YOUR-APP-NAME.onrender.com/subscribers_debug`

Should show your phone number in the list.

### Step 3: Check What Day Your Collection Is

If you used "229 Ardleigh Rd":
- Zone: 3
- Collection Day: Thursday

So you'll receive reminders on **Wednesday nights at 8 PM ET**.

### Step 4: Wait for Reminder

The cron job runs at 8 PM ET every night. You'll only get a message if:
1. Tomorrow matches your collection day
2. You're in the Google Sheet
3. Your phone number is valid WhatsApp format

## What Each Component Does

```
Google Form Submission
  ↓
Apps Script webhook → /webhook
  ↓
Look up zone/day from address_lookup.csv
  ↓
Save to Google Sheet
  ↓
Send welcome message

---

Every night at 8 PM ET:
  ↓
Cron job calls send_weekly_reminders()
  ↓
Load subscribers from Google Sheet
  ↓
For each subscriber:
  - Check if tomorrow = their collection day
  - If YES: calculate holiday shifts, send reminder
  - If NO: skip silently
```

## Common Issues & Solutions

### "No subscribers found"
- Check Google Sheet has data
- Verify SHEET_CSV_URL environment variable is set
- Check Sheet is published as CSV

### "Missing collection_day"
- Address not in lookup file
- Check address_lookup.csv was deployed
- Try exact address format from CSV

### "Reminders sending every day"
- Old code - ensure latest version deployed
- Check logs show: "Not this user's collection day - skip"

### "No reminders at all"
- Check cron job is enabled and running
- Verify cron schedule: `0 0 * * *` (8 PM ET daily)
- Check timezone in Render cron job settings

## Success Checklist

- [ ] Holiday calculation test shows 8 holidays
- [ ] Address lookup returns zone/day for test address
- [ ] /subscribers_debug shows your test subscriber
- [ ] /holiday_scrape_debug returns proper holiday note
- [ ] /health returns {"status": "ok"}
- [ ] Received welcome message after form submission
- [ ] Cron logs show execution around 8 PM ET
- [ ] Received reminder on night before collection day
- [ ] Did NOT receive reminders on other nights

## Getting Your Render App Name

1. Go to https://dashboard.render.com/
2. Click on your **Web Service** (the Flask app)
3. Look at the URL - it will be: `https://YOUR-APP-NAME.onrender.com`
4. Use that in all the URLs above
