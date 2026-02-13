"""
Holiday collection shift rules for Lower Merion Township
Based on the chart from: https://www.lowermerion.org/departments/public-works-department/refuse-and-recycling/holiday-collection
"""

from datetime import date

# Holiday shift rules based on the township chart
HOLIDAY_SHIFT_RULES = {
    "Zone 1": {
        "Monday": "Tuesday",
        "Tuesday": "Monday",
        "Wednesday": "Monday",
        "Thursday": "Monday",
        "Friday": "Monday",
    },
    "Zone 2": {
        "Monday": "Wednesday",
        "Tuesday": "Wednesday",
        "Wednesday": "Tuesday",
        "Thursday": "Tuesday",
        "Friday": "Tuesday",
    },
    "Zone 3": {
        "Monday": "Thursday",
        "Tuesday": "Thursday",
        "Wednesday": "Thursday",
        "Thursday": "Wednesday",
        "Friday": "Wednesday",
    },
    "Zone 4": {
        "Monday": "Friday",
        "Tuesday": "Friday",
        "Wednesday": "Friday",
        "Thursday": "Friday",
        "Friday": "Thursday",
    },
}

# Official holidays that affect collection (add more as needed)
OFFICIAL_HOLIDAYS_2025 = [
    {"name": "New Year's Day", "date": date(2025, 1, 1)},  # Wednesday
    {"name": "Memorial Day", "date": date(2025, 5, 26)},  # Monday
    {"name": "Independence Day", "date": date(2025, 7, 4)},  # Friday
    {"name": "Labor Day", "date": date(2025, 9, 1)},  # Monday
    {"name": "Thanksgiving Day", "date": date(2025, 11, 27)},  # Thursday
    {"name": "Christmas Day", "date": date(2025, 12, 25)},  # Thursday
]

OFFICIAL_HOLIDAYS_2026 = [
    {"name": "New Year's Day", "date": date(2026, 1, 1)},  # Thursday
    {"name": "Memorial Day", "date": date(2026, 5, 25)},  # Monday
    {"name": "Independence Day", "date": date(2026, 7, 4)},  # Saturday (observed Friday 7/3?)
    {"name": "Labor Day", "date": date(2026, 9, 7)},  # Monday
    {"name": "Thanksgiving Day", "date": date(2026, 11, 26)},  # Thursday
    {"name": "Christmas Day", "date": date(2026, 12, 25)},  # Friday
]


def get_shifted_collection_day(holiday_date: date, zone: str) -> str:
    """
    Given a holiday date and zone, return the shifted collection day.

    Args:
        holiday_date: The date of the holiday
        zone: The zone (e.g., "Zone 1", "Zone 2", etc.)

    Returns:
        The weekday name when collection will occur (e.g., "Tuesday", "Friday")
    """
    if zone not in HOLIDAY_SHIFT_RULES:
        return ""

    # Get the weekday name of the holiday (Monday, Tuesday, etc.)
    holiday_weekday = holiday_date.strftime("%A")

    # If the holiday falls on a weekend, it doesn't affect collection
    if holiday_weekday in ["Saturday", "Sunday"]:
        return ""

    # Look up the shifted day based on the chart
    shifted_day = HOLIDAY_SHIFT_RULES[zone].get(holiday_weekday, "")
    return shifted_day


def get_all_holidays_for_year(year: int) -> list:
    """Get all official holidays for a given year."""
    if year == 2025:
        return OFFICIAL_HOLIDAYS_2025
    elif year == 2026:
        return OFFICIAL_HOLIDAYS_2026
    else:
        # Return empty list for years we don't have data for
        return []
