#!/usr/bin/env python3
"""Desktop sanity test for timecode.encode_wwvb: decodes frames back
independently and checks markers, fields, and DST bits against CPython's
datetime. Run with: python3 test_wwvb.py"""

import datetime as dt
import timecode

MARKERS = (0, 9, 19, 29, 39, 49, 59)


def sym(p):
    return {800: "M", 500: 1, 200: 0}[p["low_ms"]]


def decode(pulses):
    c = [sym(p) for p in pulses]
    for s in range(60):
        assert (c[s] == "M") == (s in MARKERS), "marker mismatch at :%02d" % s
    w = lambda idx_w: sum(wt for i, wt in idx_w if c[i] == 1)
    minute = w([(1, 40), (2, 20), (3, 10), (5, 8), (6, 4), (7, 2), (8, 1)])
    hour = w([(12, 20), (13, 10), (15, 8), (16, 4), (17, 2), (18, 1)])
    doy = w([(22, 200), (23, 100), (25, 80), (26, 40), (27, 20), (28, 10),
             (30, 8), (31, 4), (32, 2), (33, 1)])
    yy = w([(45, 80), (46, 40), (47, 20), (48, 10), (50, 8), (51, 4), (52, 2), (53, 1)])
    return minute, hour, doy, yy, c[55], c[57], c[58]


def us_dst(utc_instant):
    # Reference: DST in US Central at that UTC instant (rule is national).
    import zoneinfo
    return bool(utc_instant.astimezone(zoneinfo.ZoneInfo("America/Chicago")).dst())


t = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
checked = 0
while t.year < 2028:
    epoch = int(t.timestamp())
    m, h, doy, yy, leap, b57, b58 = decode(timecode.encode_wwvb(epoch)["pulses"])
    assert (m, h, doy, yy) == (t.minute, t.hour, t.timetuple().tm_yday, t.year % 100), t
    assert leap == (1 if t.year % 4 == 0 else 0)
    day0 = t.replace(hour=0, minute=0)
    assert b58 == int(us_dst(day0)), ("bit58", t)
    assert b57 == int(us_dst(day0 + dt.timedelta(days=1))), ("bit57", t)
    checked += 1
    t += dt.timedelta(minutes=97)  # walks through every hour/minute combo over time

for label, d in [("DST starts", dt.datetime(2026, 3, 8, 12, tzinfo=dt.timezone.utc)),
                 ("DST ends", dt.datetime(2026, 11, 1, 12, tzinfo=dt.timezone.utc)),
                 ("today", dt.datetime(2026, 10, 29, 12, tzinfo=dt.timezone.utc))]:
    r = decode(timecode.encode_wwvb(int(d.timestamp()))["pulses"])
    print("%-11s %s  bits57/58 = %d%d" % (label, d.date(), r[5], r[6]))

print("OK: %d frames decoded, markers/fields/DST all match" % checked)
