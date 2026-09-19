"""
timecode.py — protocol encoders for DCF77, MSF, WWVB, JJY, and BPC.

Pure integer/float arithmetic only (no `datetime`, no `zoneinfo`) so this
runs unmodified on MicroPython (Raspberry Pi Pico / Pico 2 W) as well as
CPython, which lets it be unit-tested on a desktop before flashing.

All functions take a UTC instant as epoch seconds (standard Unix epoch,
1970-01-01 00:00:00 UTC) and a protocol name, and return a "frame": a
dict describing every field plus a list of per-second pulses, in the
same shape used by this project's earlier JavaScript version (protocols.js)
so the two can be sanity-checked against each other.

Pulse shape (one dict per second-of-frame):
  {"low_ms": int, "high_ms": int}                      -- default: low
      (reduced carrier) for low_ms, then high (full carrier) for the rest
  {"first_ms": int, "first_level": "high"/"low",
   "second_ms": int, "second_level": "high"/"low"}      -- JJY (high-first)
  {"pattern": [(ms, level), ...]}                        -- MSF's split bit

A pulse's levels describe carrier amplitude, not any particular GPIO
polarity; transmitter.py maps "high" to "carrier on" and "low" to
"carrier off" (OOK), which is what all five formats actually transmit.
"""

# ---------------------------------------------------------------------------
# Pure-integer civil calendar math (Howard Hinnant's days-from-civil /
# civil-from-days algorithm) — avoids needing `datetime` on MicroPython.
# ---------------------------------------------------------------------------

def _days_from_civil(y, m, d):
    y -= 1 if m <= 2 else 0
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    mp = (m + 9) % 12
    doy = (153 * mp + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _civil_from_days(z):
    z += 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    y = y + 1 if m <= 2 else y
    return y, m, d


def _is_leap_year(y):
    return (y % 4 == 0 and y % 100 != 0) or y % 400 == 0


def _weekday_from_days(z):
    # z = days since 1970-01-01 (a Thursday). Returns 0=Sunday..6=Saturday.
    return (z + 4) % 7


def _day_of_year(y, m, d):
    days_at_start_of_year = _days_from_civil(y, 1, 1)
    return _days_from_civil(y, m, d) - days_at_start_of_year + 1


def utc_parts_from_epoch(epoch_s):
    """epoch_s -> (year, month, day, hour, minute, second, weekday0to6sun)."""
    days, rem = divmod(int(epoch_s), 86400)
    y, m, d = _civil_from_days(days)
    hour, rem = divmod(rem, 3600)
    minute, second = divmod(rem, 60)
    weekday = _weekday_from_days(days)
    return y, m, d, hour, minute, second, weekday


def epoch_from_utc_parts(y, m, d, hour, minute, second):
    days = _days_from_civil(y, m, d)
    return days * 86400 + hour * 3600 + minute * 60 + second


# ---------------------------------------------------------------------------
# European DST rule (applies to both DCF77/Germany and MSF/UK — the whole
# EU and UK switch at the same UTC instant): clocks go forward 1 hour at
# 01:00 UTC on the last Sunday of March, and back at 01:00 UTC on the last
# Sunday of October. Valid for the modern EU/UK rule (in force since 1996,
# and the UK has kept the same dates post-Brexit).
# ---------------------------------------------------------------------------

def _last_sunday_epoch_utc_1am(year, month):
    # Find the last day of `month`, then walk back to the most recent Sunday.
    if month == 12:
        last_day_epoch = epoch_from_utc_parts(year + 1, 1, 1, 0, 0, 0) - 86400
    else:
        last_day_epoch = epoch_from_utc_parts(year, month + 1, 1, 0, 0, 0) - 86400
    days = last_day_epoch // 86400
    wd = _weekday_from_days(days)  # 0=Sunday
    days -= wd
    return days * 86400 + 3600  # 01:00 UTC that Sunday


def eu_is_dst(epoch_s):
    y = utc_parts_from_epoch(epoch_s)[0]
    start = _last_sunday_epoch_utc_1am(y, 3)
    end = _last_sunday_epoch_utc_1am(y, 10)
    return start <= epoch_s < end


def eu_local_parts(epoch_s):
    """Returns (y,m,d,hour,minute,second,weekday0to6sun,is_dst) for
    Central European (DCF77) civil time: UTC+1, or UTC+2 during DST."""
    is_dst = eu_is_dst(epoch_s)
    offset_s = 7200 if is_dst else 3600
    y, m, d, hh, mm, ss, wd = utc_parts_from_epoch(epoch_s + offset_s)
    return y, m, d, hh, mm, ss, wd, is_dst


def uk_local_parts(epoch_s):
    """Returns (y,m,d,hour,minute,second,weekday0to6sun,is_dst) for UK
    civil time: UTC+0, or UTC+1 during DST (BST)."""
    is_dst = eu_is_dst(epoch_s)
    offset_s = 3600 if is_dst else 0
    y, m, d, hh, mm, ss, wd = utc_parts_from_epoch(epoch_s + offset_s)
    return y, m, d, hh, mm, ss, wd, is_dst


def fixed_offset_local_parts(epoch_s, offset_hours):
    """No-DST fixed offset (JST = +9, CST-China = +8)."""
    y, m, d, hh, mm, ss, wd = utc_parts_from_epoch(epoch_s + offset_hours * 3600)
    return y, m, d, hh, mm, ss, wd


# ---------------------------------------------------------------------------
# Small bit-field helpers (ported 1:1 from protocols.js, verified there
# against a full year of hourly test dates for DCF77/MSF/WWVB/JJY, and
# against a real hardware decoder for BPC).
# ---------------------------------------------------------------------------

def _bcd_digit_lsb_first(n):
    return [n & 1, (n >> 1) & 1, (n >> 2) & 1, (n >> 3) & 1]


def _bits_for_weights(value, weights):
    remaining = value
    bits = []
    for w in weights:
        if w == 0:
            bits.append(0)
            continue
        if remaining >= w:
            bits.append(1)
            remaining -= w
        else:
            bits.append(0)
    return bits


def _even_parity(bits):
    return sum(bits) % 2


def _pad2(n):
    s = str(n)
    return s if len(s) >= 2 else "0" + s


# ---------------------------------------------------------------------------
# DCF77 — 77.5 kHz, Europe/Berlin local time, 59 bits (bit59 = no pulse)
# ---------------------------------------------------------------------------

def encode_dcf77(epoch_s):
    y, m, d, hour, minute, second, weekday, is_dst = eu_local_parts(epoch_s)
    bits = [0] * 59

    bits[0] = 0
    bits[16] = 0
    bits[17] = 1 if is_dst else 0
    bits[18] = 0 if is_dst else 1
    bits[19] = 0
    bits[20] = 1

    min_u = _bcd_digit_lsb_first(minute % 10)
    min_t = _bcd_digit_lsb_first(minute // 10)
    bits[21], bits[22], bits[23], bits[24] = min_u
    bits[25], bits[26], bits[27] = min_t[0], min_t[1], min_t[2]
    bits[28] = _even_parity(bits[21:28])

    hr_u = _bcd_digit_lsb_first(hour % 10)
    hr_t = _bcd_digit_lsb_first(hour // 10)
    bits[29], bits[30], bits[31], bits[32] = hr_u
    bits[33], bits[34] = hr_t[0], hr_t[1]
    bits[35] = _even_parity(bits[29:35])

    day_u = _bcd_digit_lsb_first(d % 10)
    day_t = _bcd_digit_lsb_first(d // 10)
    bits[36], bits[37], bits[38], bits[39] = day_u
    bits[40], bits[41] = day_t[0], day_t[1]

    dow = 7 if weekday == 0 else weekday  # DCF77: Monday=1..Sunday=7
    dow_bits = _bcd_digit_lsb_first(dow)
    bits[42], bits[43], bits[44] = dow_bits[0], dow_bits[1], dow_bits[2]

    mon_u = _bcd_digit_lsb_first(m % 10)
    mon_t = _bcd_digit_lsb_first(m // 10)
    bits[45], bits[46], bits[47], bits[48] = mon_u
    bits[49] = mon_t[0]

    yy = y % 100
    yr_u = _bcd_digit_lsb_first(yy % 10)
    yr_t = _bcd_digit_lsb_first(yy // 10)
    bits[50], bits[51], bits[52], bits[53] = yr_u
    bits[54], bits[55], bits[56], bits[57] = yr_t

    bits[58] = _even_parity(bits[36:58])

    pulses = []
    for s in range(59):
        low_ms = 200 if bits[s] == 1 else 100
        pulses.append({"low_ms": low_ms, "high_ms": 1000 - low_ms})
    pulses.append({"low_ms": 0, "high_ms": 1000})  # second 59: no pulse

    summary = "DCF77  %s.%s.%d  %s:%s  %s" % (
        _pad2(d), _pad2(m), y, _pad2(hour), _pad2(minute), "CEST" if is_dst else "CET"
    )
    return {
        "name": "DCF77", "carrier_hz": 77500, "frame_len_s": 60,
        "civil": {"year": y, "month": m, "day": d, "hour": hour, "minute": minute,
                  "second": second, "weekday": weekday, "is_dst": is_dst},
        "pulses": pulses, "summary": summary,
    }


# ---------------------------------------------------------------------------
# MSF — 60 kHz, Europe/London local time, 2 bits/sec (A,B)
# ---------------------------------------------------------------------------

def encode_msf(epoch_s):
    y, m, d, hour, minute, second, weekday, is_dst = uk_local_parts(epoch_s)
    A = [0] * 60
    B = [0] * 60

    yy = y % 100
    yr_bits = _bits_for_weights(yy, [80, 40, 20, 10, 8, 4, 2, 1])
    for i in range(8):
        A[17 + i] = yr_bits[i]

    mon_bits = _bits_for_weights(m, [10, 8, 4, 2, 1])
    for i in range(5):
        A[25 + i] = mon_bits[i]

    dom_bits = _bits_for_weights(d, [20, 10, 8, 4, 2, 1])
    for i in range(6):
        A[30 + i] = dom_bits[i]

    dow_bits = _bits_for_weights(weekday, [4, 2, 1])  # 0=Sunday..6=Saturday
    for i in range(3):
        A[36 + i] = dow_bits[i]

    hr_bits = _bits_for_weights(hour, [20, 10, 8, 4, 2, 1])
    for i in range(6):
        A[39 + i] = hr_bits[i]

    min_bits = _bits_for_weights(minute, [40, 20, 10, 8, 4, 2, 1])
    for i in range(7):
        A[45 + i] = min_bits[i]

    B[54] = _even_parity(yr_bits)
    B[55] = _even_parity(mon_bits + dom_bits)
    B[56] = _even_parity(dow_bits)
    B[57] = _even_parity(hr_bits + min_bits)
    B[58] = 1 if is_dst else 0

    pulses = []
    for s in range(60):
        if s == 0:
            pulses.append({"low_ms": 500, "high_ms": 500})
            continue
        a, b = A[s], B[s]
        if a == 0 and b == 0:
            pulses.append({"low_ms": 100, "high_ms": 900})
        elif a == 1 and b == 0:
            pulses.append({"low_ms": 200, "high_ms": 800})
        elif a == 1 and b == 1:
            pulses.append({"low_ms": 300, "high_ms": 700})
        else:
            pulses.append({"pattern": [
                (100, "low"), (100, "high"), (100, "low"), (700, "high")
            ]})

    summary = "MSF  %s.%s.%d  %s:%s  %s" % (
        _pad2(d), _pad2(m), y, _pad2(hour), _pad2(minute), "BST" if is_dst else "GMT"
    )
    return {
        "name": "MSF", "carrier_hz": 60000, "frame_len_s": 60,
        "civil": {"year": y, "month": m, "day": d, "hour": hour, "minute": minute,
                  "second": second, "weekday": weekday, "is_dst": is_dst},
        "pulses": pulses, "summary": summary,
    }


# ---------------------------------------------------------------------------
# WWVB — 60 kHz, UTC, 1 bit/sec (NIST SP432 Fig 2.6 layout)
# ---------------------------------------------------------------------------

def encode_wwvb(epoch_s):
    y, m, d, hour, minute, second, weekday = utc_parts_from_epoch(epoch_s)
    doy = _day_of_year(y, m, d)
    leap = 1 if _is_leap_year(y) else 0

    code = [0] * 60
    for s in (0, 9, 19, 29, 39, 49, 59):
        code[s] = "M"

    min_bits = _bits_for_weights(minute, [40, 20, 10, 0, 8, 4, 2, 1])
    for i in range(8):
        code[1 + i] = min_bits[i]

    hr_bits = _bits_for_weights(hour, [0, 0, 20, 10, 0, 8, 4, 2, 1])
    for i in range(9):
        code[10 + i] = hr_bits[i]

    doy_bits = _bits_for_weights(doy, [200, 100, 0, 80, 40, 20, 10, 0, 8, 4, 2, 1])
    for i in range(12):
        code[22 + i] = doy_bits[i]

    yy = y % 100
    yr_tens_bits = _bits_for_weights(yy // 10, [8, 4, 2, 1])
    for i in range(4):
        code[45 + i] = yr_tens_bits[i]
    yr_units_bits = _bits_for_weights(yy % 10, [8, 4, 2, 1])
    for i in range(4):
        code[50 + i] = yr_units_bits[i]

    code[55] = leap
    code[56] = 0
    code[57] = 0
    code[58] = 0

    pulses = []
    for sym in code:
        if sym == "M":
            pulses.append({"low_ms": 800, "high_ms": 200})
        elif sym == 1:
            pulses.append({"low_ms": 500, "high_ms": 500})
        else:
            pulses.append({"low_ms": 200, "high_ms": 800})

    summary = "WWVB (UTC)  day %d, %d  %s:%s" % (doy, y, _pad2(hour), _pad2(minute))
    return {
        "name": "WWVB", "carrier_hz": 60000, "frame_len_s": 60,
        "civil": {"year": y, "month": m, "day": d, "hour": hour, "minute": minute,
                  "second": second, "weekday": weekday},
        "pulses": pulses, "summary": summary,
    }


# ---------------------------------------------------------------------------
# JJY — 40/60 kHz, JST = UTC+9, no DST
# ---------------------------------------------------------------------------

def encode_jjy(epoch_s):
    y, m, d, hour, minute, second, weekday = fixed_offset_local_parts(epoch_s, 9)
    doy = _day_of_year(y, m, d)

    code = [0] * 60
    for s in (0, 9, 19, 29, 39, 49, 59):
        code[s] = "M"

    min_bits = _bits_for_weights(minute, [40, 20, 10, 0, 8, 4, 2, 1])
    for i in range(8):
        code[1 + i] = min_bits[i]

    hr_bits = _bits_for_weights(hour, [0, 0, 20, 10, 0, 8, 4, 2, 1])
    for i in range(9):
        code[10 + i] = hr_bits[i]

    doy_bits = _bits_for_weights(doy, [200, 100, 0, 80, 40, 20, 10, 0, 8])
    for i in range(9):
        code[20 + i] = doy_bits[i]
    doy_high_sum = sum(b * w for b, w in zip(doy_bits, [200, 100, 0, 80, 40, 20, 10, 0, 8]))
    doy_remainder = doy - doy_high_sum
    low_bits = _bits_for_weights(doy_remainder, [4, 2, 1])
    for i in range(3):
        code[30 + i] = low_bits[i]

    code[36] = _even_parity(hr_bits)
    code[37] = _even_parity(min_bits)
    code[40] = 0

    yr_bits = _bits_for_weights(y % 100, [80, 40, 20, 10, 8, 4, 2, 1])
    for i in range(8):
        code[41 + i] = yr_bits[i]

    dow_bits = _bits_for_weights(weekday, [4, 2, 1])  # 0=Sunday
    for i in range(3):
        code[50 + i] = dow_bits[i]

    pulses = []
    for sym in code:
        if sym == "M":
            pulses.append({"first_ms": 200, "first_level": "high", "second_ms": 800, "second_level": "low"})
        elif sym == 1:
            pulses.append({"first_ms": 500, "first_level": "high", "second_ms": 500, "second_level": "low"})
        else:
            pulses.append({"first_ms": 800, "first_level": "high", "second_ms": 200, "second_level": "low"})

    summary = "JJY (JST)  %s.%s.%d  %s:%s  day %d" % (_pad2(d), _pad2(m), y, _pad2(hour), _pad2(minute), doy)
    return {
        "name": "JJY", "carrier_hz": 40000, "frame_len_s": 60,
        "civil": {"year": y, "month": m, "day": d, "hour": hour, "minute": minute,
                  "second": second, "weekday": weekday},
        "pulses": pulses, "summary": summary,
    }


# ---------------------------------------------------------------------------
# BPC — 68.5 kHz, CST China = UTC+8, no DST, 20-second frame, 2-bit-per-
# second quaternary values (0-3 as one of four pulse widths). Verified
# against a real hardware project's independent generator (Verilog) and
# decoder (PIC firmware) — see this project's README for the full story.
# ---------------------------------------------------------------------------

def encode_bpc(epoch_s):
    y, m, d, hour, minute, second, weekday = fixed_offset_local_parts(epoch_s, 8)
    block_second = (second // 20) * 20  # 0, 20, or 40

    def pair(msb, lsb):
        return [msb, lsb]

    words = {}

    sec_code = 2 if block_second == 40 else (1 if block_second == 20 else 0)
    words[1] = pair((sec_code >> 1) & 1, sec_code & 1)
    words[2] = pair(0, 0)

    hour12 = hour % 12
    is_pm = 1 if hour >= 12 else 0
    hr_bits = _bits_for_weights(hour12, [8, 4, 2, 1])
    words[3] = pair(hr_bits[0], hr_bits[1])
    words[4] = pair(hr_bits[2], hr_bits[3])

    min_bits = _bits_for_weights(minute, [32, 16, 8, 4, 2, 1])
    words[5] = pair(min_bits[0], min_bits[1])
    words[6] = pair(min_bits[2], min_bits[3])
    words[7] = pair(min_bits[4], min_bits[5])

    dow = 7 if weekday == 0 else weekday  # 1=Monday..7=Sunday
    dow_bits = _bits_for_weights(dow, [8, 4, 2, 1])
    words[8] = pair(dow_bits[0], dow_bits[1])
    words[9] = pair(dow_bits[2], dow_bits[3])

    def two_bit_xor(a, b):
        return [a[0] ^ b[0], a[1] ^ b[1]]

    check = [0, 0]
    for s in range(1, 10):
        check = two_bit_xor(check, words[s])
    check_val = (check[0] << 1) | check[1]
    if check_val == 0 or check_val == 3:
        p1 = 2 if is_pm else 0
    else:
        p1 = 3 if is_pm else 1
    words[10] = pair((p1 >> 1) & 1, p1 & 1)

    dom_bits = _bits_for_weights(d, [32, 16, 8, 4, 2, 1])
    words[11] = pair(dom_bits[0], dom_bits[1])
    words[12] = pair(dom_bits[2], dom_bits[3])
    words[13] = pair(dom_bits[4], dom_bits[5])

    mon_bits = _bits_for_weights(m, [8, 4, 2, 1])
    words[14] = pair(mon_bits[0], mon_bits[1])
    words[15] = pair(mon_bits[2], mon_bits[3])

    yr_bits = _bits_for_weights(y % 100, [32, 16, 8, 4, 2, 1])
    words[16] = pair(yr_bits[0], yr_bits[1])
    words[17] = pair(yr_bits[2], yr_bits[3])
    words[18] = pair(yr_bits[4], yr_bits[5])

    def low3(v):
        return [(v >> 2) & 1, (v >> 1) & 1, v & 1]

    p4_parity_bits = low3(d & 0b111) + low3(m & 0b111) + low3((y % 100) & 0b111)
    p4_parity = 0
    for b in p4_parity_bits:
        p4_parity ^= b
    yr_high_flag = ((y % 100) >> 6) & 1
    if not p4_parity and not yr_high_flag:
        p4 = 0
    elif p4_parity and not yr_high_flag:
        p4 = 1
    elif p4_parity and yr_high_flag:
        p4 = 2
    else:
        p4 = 3
    words[19] = pair((p4 >> 1) & 1, p4 & 1)

    low_ms_table = [100, 200, 300, 400]
    pulses = []
    for s in range(20):
        if s == 0:
            pulses.append({"low_ms": 0, "high_ms": 1000})
            continue
        w = words.get(s, [0, 0])
        low_ms = low_ms_table[(w[0] << 1) | w[1]]
        pulses.append({"low_ms": low_ms, "high_ms": 1000 - low_ms})

    summary = "BPC (CST)  %s.%s.%d  %s:%s  (20s frame)" % (_pad2(d), _pad2(m), y, _pad2(hour), _pad2(minute))
    return {
        "name": "BPC", "carrier_hz": 68500, "frame_len_s": 20,
        "civil": {"year": y, "month": m, "day": d, "hour": hour, "minute": minute,
                  "second": second, "weekday": weekday},
        "pulses": pulses, "summary": summary,
    }


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

ENCODERS = {
    "DCF77": encode_dcf77,
    "MSF": encode_msf,
    "WWVB": encode_wwvb,
    "JJY": encode_jjy,
    "BPC": encode_bpc,
}

CARRIER_HZ = {
    "DCF77": 77500,
    "MSF": 60000,
    "WWVB": 60000,
    "JJY": 40000,   # or 60000; both carry the same time code
    "BPC": 68500,
}

FRAME_LEN_S = {
    "DCF77": 60,
    "MSF": 60,
    "WWVB": 60,
    "JJY": 60,
    "BPC": 20,
}


def encode(protocol, epoch_s):
    return ENCODERS[protocol](epoch_s)
