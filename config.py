# config.py — Pico WWVB settings.

WIFI_SSID = ""
WIFI_PASSWORD = ""
WIFI_COUNTRY = "US"

NTP_SERVER = "pool.ntp.org"   # or an NTP server on your LAN
NTP_RESYNC_S = 3600           # re-sync (and correct drift) hourly

PROTOCOL = "WWVB"             # WWVB, DCF77, MSF, JJY, or BPC

# Antenna pins: (0, 1) = GP0 -> 220R -> loop -> GP1 (pins 1 and 2).
# Use (0,) for GP0 -> 220R -> loop -> GND.
TX_PINS = (0, 1)
