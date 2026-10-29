"""
ntp.py — minimal SNTP client that keeps sub-second precision.

MicroPython's built-in ntptime only sets the RTC to whole seconds, which
isn't good enough to line up the start of each transmitted second. This
returns a (ticks_us, epoch_us) pair instead: "at this ticks_us() reading,
the UTC time was epoch_us microseconds since 1970".

Note: on the rp2 port ticks_us() wraps every ~17.9 minutes, so a pair is
only good for converting times within a few minutes of when it was taken.
Callers should use it right away (to start or correct the transmitter)
rather than keep it around.
"""

import socket
import struct
import utime

NTP_TO_UNIX_S = 2208988800


def _ts_us(data, offset):
    secs, frac = struct.unpack("!II", data[offset:offset + 8])
    return (secs - NTP_TO_UNIX_S) * 1_000_000 + ((frac * 1_000_000) >> 32)


def query_once(addr, timeout_s=2):
    """One SNTP exchange. Returns (ticks_us_at_receive, epoch_us, rtt_us)."""
    pkt = bytearray(48)
    pkt[0] = 0x23  # LI=0, VN=4, Mode=3 (client)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout_s)
    try:
        t_send = utime.ticks_us()
        s.sendto(pkt, addr)
        data = s.recv(48)
        t_recv = utime.ticks_us()
    finally:
        s.close()
    if len(data) < 48:
        raise OSError("short NTP reply")
    mode = data[0] & 0x07
    stratum = data[1]
    if mode != 4 or stratum == 0 or stratum > 15:
        raise OSError("bad NTP reply (mode %d, stratum %d)" % (mode, stratum))
    t2 = _ts_us(data, 32)  # server receive time
    t3 = _ts_us(data, 40)  # server transmit time
    rtt = utime.ticks_diff(t_recv, t_send) - (t3 - t2)
    if rtt < 0:
        rtt = 0
    return t_recv, t3 + rtt // 2, rtt


def query(host, samples=4, port=123):
    """Take several samples and keep the one with the lowest round trip
    (least network asymmetry). Returns (ticks_us, epoch_us, rtt_us)."""
    addr = socket.getaddrinfo(host, port)[0][-1]
    best = None
    last_err = None
    for _ in range(samples):
        try:
            r = query_once(addr)
            if best is None or r[2] < best[2]:
                best = r
        except OSError as e:
            last_err = e
        utime.sleep_ms(50)
    if best is None:
        raise last_err or OSError("NTP failed")
    return best


def ticks_for_epoch_us(anchor, epoch_us):
    """ticks_us() value corresponding to epoch_us, given an anchor from
    query(). Only valid for times within a few minutes of the anchor."""
    a_ticks, a_epoch_us, _ = anchor
    return utime.ticks_add(a_ticks, epoch_us - a_epoch_us)


def epoch_us_now(anchor):
    a_ticks, a_epoch_us, _ = anchor
    return a_epoch_us + utime.ticks_diff(utime.ticks_us(), a_ticks)
