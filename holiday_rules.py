"""
Holiday collection shift rules for Lower Merion Township
Based on the chart from: https://www.lowermerion.org/departments/public-works-department/refuse-and-recycling/holiday-collection

Automatically calculates federal holiday dates for any year.
"""

from datetime import date, timedelta
from calendar import monthcalendar, MONDAY, THURSDAY

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

# Helper functions to calculate federal holiday dates
def nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """
    Find the nth occurrence of a weekday in a given month.
    weekday: 0=Monday, 1=Tuesday, ..., 6=Sunday
    n: 1=first, 2=second, 3=third, 4=fourth, -1=last
    """
    cal = monthcalendar(year, month)

    if n == -1:  # Last occurrence
        # Go through weeks in reverse
        for week in reversed(cal):
            if week[weekday] != 0:
                return date(year, month, week[weekday])
    else:  # nth occurrence
        count = 0
        for week in cal:
            if week[weekday] != 0:
                count += 1
                if count == n:
                    return date(year, month, week[weekday])

    raise ValueError(f"Could not find {n}th {weekday} in {year}-{month}")


def calculate_federal_holidays(year: int) -> list:
    """
    Calculate the dates of federal holidays observed by Lower Merion Township.

    Holidays observed by the Refuse Division:
    - New Year's Day (January 1)
    - Birthday of Martin Luther King, Jr (3rd Monday in January)
    - Memorial Day (Last Monday in May)
    - Juneteenth (June 19)
    - Independence Day (July 4)
    - Labor Day (1st Monday in September)
    - Thanksgiving Day (4th Thursday in November)
    - Christmas Day (December 25)
    """
    holidays = [
        {"name": "New Year's Day", "date": date(year, 1, 1)},
        {"name": "Martin Luther King Jr. Day", "date": nth_weekday_of_month(year, 1, MONDAY, 3)},
        {"name": "Memorial Day", "date": nth_weekday_of_month(year, 5, MONDAY, -1)},
        {"name": "Juneteenth", "date": date(year, 6, 19)},
        {"name": "Independence Day", "date": date(year, 7, 4)},
        {"name": "Labor Day", "date": nth_weekday_of_month(year, 9, MONDAY, 1)},
        {"name": "Thanksgiving Day", "date": nth_weekday_of_month(year, 11, THURSDAY, 4)},
        {"name": "Christmas Day", "date": date(year, 12, 25)},
    ]

    return holidays


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
    """
    Get all official holidays for a given year.
    Automatically calculates federal holiday dates.
    """
    return calculate_federal_holidays(year)
