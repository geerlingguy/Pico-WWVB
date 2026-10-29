#!/usr/bin/env python3
"""Desktop test: decode every 97th minute of 2026-2027 from timecode.frame()
and check markers, fields, and DST bits against datetime/zoneinfo."""

import datetime as dt
import zoneinfo
import timecode

MARKERS = (0, 9, 19, 29, 39, 49, 59)
CHICAGO = zoneinfo.ZoneInfo("America/Chicago")


def decode(ms):
    sym = [{800: "M", 500: 1, 200: 0}[x] for x in ms]
    for s in range(60):
        assert (sym[s] == "M") == (s in MARKERS), "marker at :%02d" % s
    val = lambda fields: sum(w for s, w in fields if sym[s] == 1)
    return (val(timecode.MINUTE), val(timecode.HOUR), val(timecode.DAY_OF_YEAR),
            val(timecode.YEAR), sym[55], sym[57], sym[58])


def dst(t):
    return int(bool(t.astimezone(CHICAGO).dst()))


t = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
n = 0
while t.year < 2028:
    got = decode(timecode.frame(int(t.timestamp())))
    midnight = t.replace(hour=0, minute=0)
    want = (t.minute, t.hour, t.timetuple().tm_yday, t.year % 100,
            int(t.year % 4 == 0), dst(midnight + dt.timedelta(days=1)), dst(midnight))
    assert got == want, (t, got, want)
    n += 1
    t += dt.timedelta(minutes=97)

print("OK: %d frames match" % n)
