"""
timecode.py — WWVB amplitude time code (NIST SP 432).

Integer math only, so it runs on MicroPython and desktop Python alike.
"""

MARKERS = (0, 9, 19, 29, 39, 49, 59)

# (second, weight) of each BCD field
MINUTE = ((1, 40), (2, 20), (3, 10), (5, 8), (6, 4), (7, 2), (8, 1))
HOUR = ((12, 20), (13, 10), (15, 8), (16, 4), (17, 2), (18, 1))
DAY_OF_YEAR = ((22, 200), (23, 100), (25, 80), (26, 40), (27, 20), (28, 10),
               (30, 8), (31, 4), (32, 2), (33, 1))
YEAR = ((45, 80), (46, 40), (47, 20), (48, 10), (50, 8), (51, 4), (52, 2), (53, 1))


def days_from_civil(y, m, d):
    """Days since 1970-01-01 (Howard Hinnant's algorithm)."""
    y -= m <= 2
    era = y // 400
    yoe = y - era * 400
    doy = (153 * ((m + 9) % 12) + 2) // 5 + d - 1
    return era * 146097 + yoe * 365 + yoe // 4 - yoe // 100 + doy - 719468


def year_from_days(z):
    z += 719468
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    return yoe + era * 400 + (doy >= 306)  # doy counts from March 1


def _nth_sunday(y, m, n):
    first = days_from_civil(y, m, 1)
    return first + (3 - first) % 7 + 7 * (n - 1)  # 1970-01-04 was a Sunday


def us_dst(day):
    """US DST in effect at 00:00 UTC on `day` (days since 1970)? DST runs
    from the 2nd Sunday of March to the 1st Sunday of November, and 00:00
    UTC is still the previous evening in the US."""
    y = year_from_days(day)
    return _nth_sunday(y, 3, 2) < day <= _nth_sunday(y, 11, 1)


def frame(epoch_s):
    """Reduced-carrier time in ms for each second of the minute starting
    at epoch_s: 200 = 0, 500 = 1, 800 = marker."""
    day, secs = divmod(epoch_s, 86400)
    y = year_from_days(day)
    bits = [0] * 60

    def put(value, fields):
        for sec, weight in fields:
            if value >= weight:
                bits[sec] = 1
                value -= weight

    put(secs // 60 % 60, MINUTE)
    put(secs // 3600, HOUR)
    put(day - days_from_civil(y, 1, 1) + 1, DAY_OF_YEAR)
    put(y % 100, YEAR)
    bits[55] = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)  # leap year
    bits[57] = us_dst(day + 1)  # DST at end of this UTC day
    bits[58] = us_dst(day)      # DST at start of this UTC day
    return [800 if s in MARKERS else 500 if bits[s] else 200 for s in range(60)]
