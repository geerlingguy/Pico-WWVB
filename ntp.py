"""
ntp.py — minimal SNTP client with sub-second precision.

Returns an anchor (ticks_us, epoch_us, rtt_us): at that ticks_us() reading,
UTC was epoch_us microseconds since 1970. ticks_us() wraps every ~18 min,
so use an anchor right away rather than keeping it.
"""

import socket
import struct
import utime

NTP_TO_UNIX_S = 2208988800


def _timestamp_us(data, offset):
    secs, frac = struct.unpack("!II", data[offset:offset + 8])
    return (secs - NTP_TO_UNIX_S) * 1_000_000 + ((frac * 1_000_000) >> 32)


def _query_once(addr):
    pkt = bytearray(48)
    pkt[0] = 0x23  # version 4, client mode
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    try:
        sent = utime.ticks_us()
        s.sendto(pkt, addr)
        data = s.recv(48)
        received = utime.ticks_us()
    finally:
        s.close()
    if len(data) < 48 or data[0] & 7 != 4 or not 0 < data[1] < 16:
        raise OSError("bad NTP reply")
    server_rx, server_tx = _timestamp_us(data, 32), _timestamp_us(data, 40)
    rtt = max(0, utime.ticks_diff(received, sent) - (server_tx - server_rx))
    return received, server_tx + rtt // 2, rtt


def query(host):
    """Best of 4 samples (lowest round trip = least asymmetry)."""
    addr = socket.getaddrinfo(host, 123)[0][-1]
    best, err = None, None
    for _ in range(4):
        try:
            r = _query_once(addr)
            if best is None or r[2] < best[2]:
                best = r
        except OSError as e:
            err = e
    if best is None:
        raise err
    return best


def ticks_at(anchor, epoch_us):
    """ticks_us() value at UTC epoch_us (within a few minutes of anchor)."""
    return utime.ticks_add(anchor[0], epoch_us - anchor[1])


def now_us(anchor):
    """Current UTC epoch in microseconds."""
    return anchor[1] + utime.ticks_diff(utime.ticks_us(), anchor[0])
