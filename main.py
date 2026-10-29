"""
main.py — sync time over NTP and transmit WWVB. Settings are in config.py;
progress prints to the USB serial console (`mpremote repl`).
"""

import time
import network

import config
import ntp
import transmitter

RESYNC_MS = 3_600_000  # hourly, to cancel crystal drift
RETRY_MS = 30_000


def fmt(epoch_s):
    return "%04d-%02d-%02d %02d:%02d:%02d UTC" % time.gmtime(epoch_s)[:6]


def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    if wlan.isconnected():
        return
    wlan.active(True)
    wlan.config(pm=network.WLAN.PM_NONE)  # power saving adds NTP jitter
    print("Connecting to", config.WIFI_SSID)
    wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
    for _ in range(30):
        if wlan.isconnected():
            print("Wi-Fi connected:", wlan.ifconfig()[0])
            return
        time.sleep(1)
    raise OSError("Wi-Fi connect timed out")


def sync():
    """Fresh NTP anchor, or None on failure."""
    try:
        wifi_connect()
        anchor = ntp.query(config.NTP_SERVER)
    except Exception as e:
        print("NTP sync failed:", e)
        return None
    print("NTP sync:", fmt(ntp.now_us(anchor) // 1_000_000), "(rtt %.1f ms)" % (anchor[2] / 1000))
    return anchor


def start(anchor):
    # Next minute at least 0.2 s away, leaving time to set up the PIO.
    epoch = ((ntp.now_us(anchor) + 200_000) // 60_000_000 + 1) * 60
    transmitter.start(epoch, ntp.ticks_at(anchor, epoch * 1_000_000))
    print("Transmitting WWVB from", fmt(epoch))


def correct_drift(anchor, position):
    # How far the current minute's actual start is from where NTP says it should be.
    epoch, ticks = position
    delta = time.ticks_diff(ntp.ticks_at(anchor, epoch * 1_000_000), ticks)
    transmitter.adjust(delta)
    print("Drift correction: %+.1f ms" % (delta / 1000))


def main():
    if not config.WIFI_SSID:
        raise SystemExit("Set WIFI_SSID and WIFI_PASSWORD in config.py")
    while True:
        anchor = sync()
        position = transmitter.position()
        if anchor and position:
            correct_drift(anchor, position)
        elif anchor and not transmitter.running():
            start(anchor)
        time.sleep_ms(RESYNC_MS if transmitter.running() else RETRY_MS)


if __name__ == "__main__":
    main()
