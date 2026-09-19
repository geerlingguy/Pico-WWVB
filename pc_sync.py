#!/usr/bin/env python3
"""
pc_sync.py — run this on the PC/laptop connected to the Pico's USB port.

Lets you pick a time-code protocol and start transmission timed to the
exact second you press Enter — useful for setting your PC's clock to a
reference (like a phone's network time, or a WWV/CHU audio time signal)
and then hitting Enter the instant a real second boundary lands, rather
than trusting the PC's clock to already be accurate to the second.

Two modes:
  1. "Use PC clock now" — presses Enter immediately, transmission starts
     using the PC's current clock reading at the moment Enter is hit.
  2. "Type in a time, then hit Enter on the tick" — you type the target
     date/time ahead of time (no seconds — :00 is assumed), then watch a
     separate accurate clock and press Enter the exact instant its
     second hand reaches 12 for that minute. That keystroke *is* second
     zero — the Pico is told "the time you just entered starts now."

Note: this connects over the same USB serial link as the Pico's MicroPython
REPL, which by default runs main.py in "serial mode" and waits for this
script. If you've previously created a "wifi_mode.txt" file on the Pico
to use the standalone Wi-Fi web UI instead, delete it and reboot the
Pico before using this script.

Requires: pyserial (`pip install pyserial`)
"""

import sys
import time

try:
    import serial
except ImportError:
    print("This script needs pyserial: pip install pyserial")
    sys.exit(1)

PROTOCOLS = ["DCF77", "MSF", "WWVB", "JJY", "BPC"]


def find_port():
    import serial.tools.list_ports
    ports = list(serial.tools.list_ports.comports())
    pico_ports = [p for p in ports if "usb" in (p.description or "").lower()
                  or "pico" in (p.description or "").lower()
                  or (p.vid == 0x2E8A)]  # Raspberry Pi Foundation VID
    candidates = pico_ports or ports
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0].device
    print("Multiple serial ports found:")
    for i, p in enumerate(candidates):
        print(f"  [{i}] {p.device}  ({p.description})")
    idx = input("Pick a port number: ").strip()
    try:
        return candidates[int(idx)].device
    except (ValueError, IndexError):
        return None


def pick_protocol():
    print("\nAvailable time-code protocols:")
    for i, p in enumerate(PROTOCOLS):
        print(f"  [{i}] {p}")
    choice = input("Pick a protocol number: ").strip()
    try:
        return PROTOCOLS[int(choice)]
    except (ValueError, IndexError):
        print("Invalid choice.")
        sys.exit(1)


def get_target_epoch():
    """Returns a UTC epoch second for the transmission's second-zero, and
    a description of how it will be captured."""
    print("\nHow do you want to set the time?")
    print("  [1] Use this PC's clock, starting the instant I press Enter")
    print("  [2] Type in a specific date/time, then I'll press Enter on the tick")
    mode = input("Choice: ").strip()

    if mode == "1":
        input("Press Enter to start transmitting using the PC's current time... ")
        return time.time(), "captured at Enter keypress (PC clock)"

    elif mode == "2":
        print("\nEnter the date and time this should start at (24-hour, seconds")
        print("are always :00 — you'll press Enter exactly on that tick):")
        y = int(input("  Year (e.g. 2026): ").strip())
        mo = int(input("  Month (1-12): ").strip())
        d = int(input("  Day (1-31): ").strip())
        hh = int(input("  Hour (0-23): ").strip())
        mm = int(input("  Minute (0-59): ").strip())
        tz_choice = input("  Is that UTC or local time? [utc/local] (default: local): ").strip().lower()

        if tz_choice == "utc":
            target_struct = (y, mo, d, hh, mm, 0, 0, 0, 0)
            target_epoch_naive = time.mktime(target_struct)
            # mktime treats input as LOCAL, so if the user said UTC we need
            # to correct by the local UTC offset.
            utc_offset = time.mktime(time.localtime()) - time.mktime(time.gmtime())
            target_epoch = target_epoch_naive + utc_offset
        else:
            target_struct = (y, mo, d, hh, mm, 0, 0, 0, -1)
            target_epoch = time.mktime(target_struct)

        print(f"\nReady to send: {y:04d}-{mo:02d}-{d:02d} {hh:02d}:{mm:02d}:00")
        print("Watch your reference clock. The MOMENT its second hand hits 12")
        print("for that minute, press Enter — that keypress becomes second zero.")
        input("(waiting for your Enter press) ")
        return target_epoch, "captured at Enter keypress (typed time, tick-aligned)"

    else:
        print("Invalid choice.")
        sys.exit(1)


def main():
    port = find_port()
    if not port:
        print("No serial port found. Is the Pico plugged in?")
        sys.exit(1)
    print(f"Using port: {port}")

    protocol = pick_protocol()

    # Open the serial connection BEFORE asking the user to watch a clock
    # and press Enter — opening it after the keypress would add
    # uncontrolled latency (and on some boards, opening a fresh serial
    # connection toggles DTR/RTS and can reset the Pico), which would
    # defeat the point of tick-accurate timing. Everything after the
    # keypress below is just the write itself.
    with serial.Serial(port, 115200, timeout=2) as ser:
        time.sleep(0.3)  # let the Pico's USB-serial settle after opening
        # Drain any boot-time chatter so it doesn't get mistaken for a reply
        try:
            ser.reset_input_buffer()
        except Exception:
            pass

        target_epoch, how = get_target_epoch()
        # Everything above this line (including the Enter keypress inside
        # get_target_epoch) is done. Send immediately.
        cmd = f"START {protocol} {int(target_epoch)}\n"
        ser.write(cmd.encode())
        ser.flush()
        send_time = time.time()
        resp = ser.readline().decode(errors="replace").strip()

    print(f"\nStarting {protocol} transmission — {how}")
    print(f"Target epoch: {int(target_epoch)}  (PC time at send: {send_time:.3f})")
    print("Pico says:", resp or "(no response — check main.py is running the serial listener)")
    print("\nDone. The Pico should now be transmitting.")
    print("To stop: reconnect and send STOP, or power-cycle the Pico.")


if __name__ == "__main__":
    main()
