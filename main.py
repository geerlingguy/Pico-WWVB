"""
main.py — Pico WWVB: get the time over NTP and transmit a radio time signal.

Settings are in config.py. On boot the Pico joins Wi-Fi, syncs over NTP,
starts transmitting at the next minute, and re-syncs every NTP_RESYNC_S
seconds to cancel crystal drift. Progress is printed to the USB serial
console (`mpremote repl`).
"""

import time
import network

import config
import ntp
import transmitter

ANCHOR_MAX_AGE_MS = 300_000   # ticks_us() wraps every ~17.9 min on rp2
RETRY_MS = 30_000
PROTOCOL = config.PROTOCOL.upper()


def fmt(epoch_s):
    return "%04d-%02d-%02d %02d:%02d:%02d" % time.gmtime(epoch_s)[:6]


def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    if wlan.isconnected():
        return
    try:
        import rp2
        rp2.country(config.WIFI_COUNTRY)
    except Exception:
        pass
    wlan.active(True)
    try:
        wlan.config(pm=network.WLAN.PM_NONE)  # steadier NTP round trips
    except Exception:
        pass
    print("Connecting to Wi-Fi", config.WIFI_SSID, "...")
    wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
    for _ in range(150):  # 30 s
        if wlan.isconnected():
            print("Wi-Fi connected:", wlan.ifconfig()[0])
            return
        time.sleep_ms(200)
    wlan.disconnect()
    raise OSError("Wi-Fi connect timed out (status %d)" % wlan.status())


def sync():
    """Returns a fresh NTP anchor (ticks_us, epoch_us, rtt_us), or None."""
    try:
        wifi_connect()
        anchor = ntp.query(config.NTP_SERVER)
    except Exception as e:
        print("NTP sync failed:", e)
        return None
    print("NTP sync OK: %s UTC (rtt %.1f ms)" % (
        fmt(ntp.epoch_us_now(anchor) // 1_000_000), anchor[2] / 1000))
    return anchor


def start(anchor):
    # First frame boundary at least 200 ms out, to leave time for PIO setup.
    s = -(-(ntp.epoch_us_now(anchor) + 200_000) // 1_000_000)
    frame = transmitter.next_frame_boundary(PROTOCOL, s)
    ticks = ntp.ticks_for_epoch_us(anchor, frame * 1_000_000)
    if transmitter.start(PROTOCOL, frame, ticks):
        print("Transmitting %s from %s UTC on GPIO %s" % (PROTOCOL, fmt(frame), transmitter.TX_PINS))


def correct_drift(anchor):
    """Compare when the current frame actually started against NTP, and
    shift the following frames to match."""
    pos = transmitter.position()
    if pos is None:
        return
    frame, ticks = pos
    delta_us = time.ticks_diff(ntp.ticks_for_epoch_us(anchor, frame * 1_000_000), ticks)
    print("Drift correction: %+.1f ms" % (delta_us / 1000))
    if abs(delta_us) > 2_000_000:
        transmitter.stop(wait_ms=3000)  # way off: the main loop restarts it
    else:
        transmitter.adjust(delta_us)


def main():
    if not config.WIFI_SSID:
        raise SystemExit("Set WIFI_SSID and WIFI_PASSWORD in config.py")
    print("Pico WWVB: %s via %s" % (PROTOCOL, config.NTP_SERVER))
    resync_ms = config.NTP_RESYNC_S * 1000
    anchor, anchor_ms, last_try = None, 0, None

    while True:
        now = time.ticks_ms()
        age = time.ticks_diff(now, anchor_ms)
        need_sync = (anchor is None or age >= resync_ms
                     or (not transmitter.is_transmitting() and age > ANCHOR_MAX_AGE_MS))
        if need_sync and (last_try is None or time.ticks_diff(now, last_try) >= RETRY_MS):
            last_try = now
            fresh = sync()
            if fresh:
                anchor, anchor_ms = fresh, time.ticks_ms()
                if transmitter.is_transmitting():
                    correct_drift(anchor)

        if (anchor and not transmitter.is_transmitting()
                and time.ticks_diff(time.ticks_ms(), anchor_ms) <= ANCHOR_MAX_AGE_MS):
            start(anchor)

        time.sleep_ms(500)


if __name__ == "__main__":
    main()
