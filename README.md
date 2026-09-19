# Longwave Pico

Transmits DCF77, MSF, WWVB, JJY, or BPC time-code signals from a
Raspberry Pi Pico or Pico 2 W, so a nearby radio-controlled clock can
sync from a GPIO pin instead of over the air — useful if the real
transmitter is down, out of range, or your clock has no manual
time-set button.

This is the hardware sibling of the "Longwave" browser app (the one
that flashes your screen instead) — same five protocols, same verified
bit encoders, different antenna.

## What you need

| You need | Notes |
|---|---|
| Raspberry Pi Pico or Pico 2 W | Any variant works |
| ~20–30cm wire | Solid core or stranded, bare end |
| The clock you want to set | Whatever you're trying to sync |
| A phone or PC, briefly, to enable Wi-Fi mode once | Only for the one-time setup step below |

Solder or clip the wire to **GP0 (physical Pin 1)**. That's it — no
other components needed. Coiling the wire into a small loop (a few
turns around a pen or similar) tends to couple better into these
clocks' ferrite-rod antennas than a straight wire does.

Powering it from a portable battery pack works exactly like any other
USB power source — the Pico has no idea what's supplying its 5V and
doesn't care.

## Files

- `timecode.py` — the five protocol encoders (DCF77, MSF, WWVB, JJY,
  BPC). Pure integer/float math, no `datetime` — takes a UTC epoch
  second and returns the exact per-second pulse pattern each station
  transmits, deriving each protocol's own local time and DST
  convention along the way.
- `transmitter.py` — drives GP0 as a PIO-generated carrier at each
  protocol's real frequency, gated on/off (OOK) second by second
  according to `timecode.py`'s output. Runs continuously on the Pico's
  second CPU core once started, independent of whether anything stays
  connected over Wi-Fi or USB.
- `main.py` — boot entry point. Listens on USB serial for commands
  from `pc_sync.py` by default, or serves a standalone Wi-Fi web UI
  with automatic phone-clock syncing if you create a `wifi_mode.txt`
  flag file (see below).
- `pc_sync.py` — run this on a PC connected to the Pico's USB port to
  drive it from there instead.

## Installing

1. Flash the Pico with MicroPython from
   [micropython.org/download](https://micropython.org/download/rp2-pico/).
2. Copy `timecode.py`, `transmitter.py`, and `main.py` to the Pico's
   filesystem (Thonny or `mpremote`).
3. Reboot the Pico (or it'll start running `main.py` immediately once
   copied, depending on your tool).

## Setting the time — two ways

### Option 1: Standalone Wi-Fi, with automatic phone-clock sync (easiest)

Create an empty file named `wifi_mode.txt` on the Pico's filesystem
once (via Thonny), then reboot. From then on — battery pack included,
no PC needed at all — the Pico opens its own Wi-Fi access point,
**LongwavePico** (no password).

Connect your phone to it and open `http://192.168.4.1`. The page
automatically reads your phone's own (already-accurate) clock in the
background the moment it loads — no typing, no watching a clock, no
tapping anything on a tick. Once the badge shows **CLOCK SYNCED**,
pick a protocol and hit **Start transmitting**.

This works by timing a quick round trip to the Pico over the direct
Wi-Fi link and sending back the phone's current time corrected for
that latency — the same idea NTP uses, just over a single local hop.
Once the Pico's clock is set this way, it keeps running on its own
straight through — transmission continues in the background even if
your phone disconnects or the app closes.

**If auto-sync fails** (shown as **SYNC FAILED**), an expandable
"Manual time entry" section on the same page lets you type in the
date/time and your current UTC offset by hand instead, the same way
earlier versions of this worked.

The Pico's RTC has no battery backup, so power-cycling it always
clears the synced time — that's expected, not a bug. The next phone
that connects and loads the page re-syncs it automatically, with no
extra steps.

### Option 2: PC-connected over serial

Run `pc_sync.py` on a computer plugged into the Pico's USB port
(needs `pip install pyserial`; requires serial mode, i.e. no
`wifi_mode.txt` file present). It walks you through:

1. Picking a protocol.
2. Either using your PC's clock as-is, or typing in a specific
   date/time and then watching a reference clock — a phone's synced
   clock, a radio time signal, whatever you trust — and **pressing
   Enter the instant its second hand reaches 12** for that minute.

That keypress is second zero: `pc_sync.py` sends it to the Pico
immediately (the serial connection is opened *before* you're asked to
watch the clock, specifically so nothing but the actual `write()` call
happens after your keypress), and the Pico starts transmitting using
that moment as "now."

Delete `wifi_mode.txt` (or never create it) to use this mode; create
it and reboot to switch to Option 1 instead.

## Why a flag file instead of just waiting a few seconds for a PC?

An earlier version of this tried "wait 3 seconds for a serial command,
then fall back to Wi-Fi" — but typing a date and time into `pc_sync.py`
routinely takes longer than that, so the Pico would time out and boot
into Wi-Fi mode mid-setup. A flag file you set once for however you
actually use the device avoids that race entirely.

## Accuracy notes

`timecode.py` is a direct port of the same encoders used in the
browser version of this project, and has been checked bit-for-bit
against that already-verified implementation across a full year of
hourly test dates for all five protocols, with zero mismatches. See
that project's own README for the detailed accuracy history per
protocol (particularly BPC, which has no official specification).

Whether the signal actually reaches a given clock depends on antenna
placement, wire length/shape, and that clock's own receiver
sensitivity — there's no way to guarantee reception in software. Try
moving the wire closer, coiling it, or repositioning the clock before
concluding it isn't working.

The auto-sync feature's accuracy depends on how symmetric the Wi-Fi
round trip actually is (typically a few milliseconds on a direct
phone-to-Pico AP link with nothing else on the network) and, of
course, on the phone's own clock being correct in the first place —
true for any phone with a working data or Wi-Fi connection at some
point recently, since phones sync from the cellular network or NTP
automatically.

## Credits

- The PIO carrier-generation and pin-gating technique — a state
  machine free-running a square wave, gated on/off by handing the pin
  between PIO control and plain GPIO-low control — is adapted directly
  from **PicoProjects**, a Pico AM-radio transmitter project shared as
  a zip upload (`pico_am_radio.py` and the `Web-iinteractive/` Wi-Fi
  web-controller variant). That project plays MIDI files as OOK AM
  transmissions around 1 MHz; this one repurposes the exact same
  gating mechanism at much lower carrier frequencies (40–77.5 kHz) and
  second-scale timing instead of audio-rate note toggling. The
  standalone Wi-Fi AP + web page pattern in `main.py`
  (`network.WLAN(network.AP_IF)`, the minimal from-scratch HTTP server,
  the `_thread`-based start/stop control of a background transmission)
  is also carried over from that project's `Web-iinteractive/main.py`
  and `radio.py` nearly unchanged in structure.
- The GP0 pin choice and reasoning (top-left corner pin, so a stray
  touch only risks a harmless neighboring GPIO) is preserved from that
  same project's README.
- The protocol encoders (`timecode.py`) are a port of this project
  family's own browser-based "Longwave" app, whose BPC implementation
  in turn came from **[BPC_VFD_Clock](https://github.com/Belief997/BPC_VFD_Clock)**
  by Belief997 — see that project's README for the full story of how
  its Verilog generator and PIC decoder fixed BPC's encoding.
