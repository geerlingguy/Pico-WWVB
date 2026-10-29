# config.py — Longwave Pico settings.
#
# Fill in WIFI_SSID to boot into NTP mode: the Pico joins your network,
# gets the time over NTP, and transmits automatically (no phone or PC).
# Leave WIFI_SSID empty to use the original serial / access-point modes.

WIFI_SSID = ""
WIFI_PASSWORD = ""
WIFI_COUNTRY = "US"
HOSTNAME = "longwave-pico"        # also reachable as longwave-pico.local

NTP_SERVER = "pool.ntp.org"       # or your own LAN NTP server
NTP_RESYNC_S = 3600               # re-sync (and correct drift) hourly

PROTOCOL = "WWVB"                 # WWVB, DCF77, MSF, JJY, BPC

# Antenna pins. (0, 1) = differential drive across GP0/GP1 (physical pins
# 1 and 2): GP0 -> 220R -> coil -> GP1. Use (0,) for GP0 -> 220R -> coil
# -> GND. Two-pin mode needs consecutive GPIOs.
TX_PINS = (0, 1)

# UTC hours to transmit in, or None for 24/7. Example for US Central,
# covering the overnight hours most clocks search (~midnight-6am local):
#   TX_HOURS_UTC = (5, 6, 7, 8, 9, 10, 11)
TX_HOURS_UTC = None

STATUS_SERVER = True              # tiny JSON status page on port 80
