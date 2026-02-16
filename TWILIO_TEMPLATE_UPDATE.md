# Twilio Template Update - Holiday Message Fix

## Problem
Twilio rejects templates that end with a variable ({{3}}). The current HOLIDAY template structure violates this rule.

## Solution
Move the holiday note variable {{3}} to the middle of the message instead of the end.

---

## Templates to Create in Twilio Dashboard

### 1. BASIC Reminder Template
**Name:** `trash_reminder_basic`
**Category:** UTILITY
**Language:** English (US)

**Content:**
```
Collection for {{1}} is tomorrow
Recycling: {{2}} this week
```

**Variables:**
- {{1}} = Street address (e.g., "229 Ardleigh Rd")
- {{2}} = Recycling type (e.g., "Paper" or "Commingled")

**Example Output:**
```
Collection for 229 Ardleigh Rd is tomorrow
Recycling: Paper this week
```

---

### 2. HOLIDAY Reminder Template
**Name:** `trash_reminder_holiday`
**Category:** UTILITY
**Language:** English (US)

**Content:**
```
Collection for {{1}} is tomorrow
Holiday schedule: {{2}}
Recycling: {{3}} this week
```

**Variables:**
- {{1}} = Street address (e.g., "229 Ardleigh Rd")
- {{2}} = Holiday note (e.g., "Christmas Day on Thursday. Pickup shifted to Friday this week.")
- {{3}} = Recycling type (e.g., "Paper" or "Commingled")

**Example Output:**
```
Collection for 229 Ardleigh Rd is tomorrow
Holiday schedule: Christmas Day on Thursday. Pickup shifted to Friday this week.
Recycling: Paper this week
```

---

## Why This Works

✅ **Twilio Requirements Met:**

1. **Variables in sequential order:** {{1}}, {{2}}, {{3}} ✅
2. **Ends with static text:** "this week" after {{3}} ✅

The template structure ensures variables appear in order (1, 2, 3) and ends with the static text "this week" rather than a variable placeholder.

---

## Steps to Update

1. Log into [Twilio Console](https://console.twilio.com/)
2. Go to **Messaging** → **Content Templates**
3. Find your `trash_reminder_holiday` template
4. Click **Edit**
5. Update the content to: `Reminder for {{1}}. {{3}} Recycling: {{2}}.`
6. Submit for approval
7. Once approved, copy the new Template SID
8. Update your Render environment variable:
   - Variable: `TWILIO_TEMPLATE_SID_REMINDER_HOLIDAY`
   - Value: `HX...` (your new template SID)

---

## Testing

After the template is approved, test with:
```bash
# Via debug endpoint (if you added one)
curl "https://trash-reminder-bot-1.onrender.com/test_reminders"

# Or wait for the nightly cron job to run
```

Check that the message renders correctly with the holiday note in the middle.
