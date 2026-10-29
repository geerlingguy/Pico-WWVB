> **Note:** This is a fork of [Longwave Pico](https://github.com/carter-oswald-dev/Pi-pico-time-signal-generator-), trimmed down to an unattended, NTP-synced WWVB transmitter.

# Pico WWVB

Sets radio-controlled ("atomic") clocks indoors, where the real WWVB signal from Colorado doesn't reach. A Raspberry Pi Pico 2 W (or Pico W) gets the time over Wi-Fi and transmits a 60 kHz WWVB signal through a small wire loop placed next to the clock.

## Parts

- Raspberry Pi Pico 2 W or Pico W
- 220 Ω resistor
- 1–2 m of thin insulated wire (bodge wire or magnet wire)

## Wiring

![GP0 through a 220 ohm resistor to a wire loop antenna, returning to GP1](wiring.svg)

**GP0 (pin 1) → 220 Ω → loop → GP1 (pin 2)**

Wind the wire into a loop of 20–30 turns, 2–3 cm across (a bottle cap makes a good form), and connect one end to the resistor and the other to GP1.

- Don't skip the resistor: at 60 kHz the loop is nearly a short circuit.
- Range is a few inches. Put the loop against the clock's movement, facing the same way as the clock's internal ferrite rod antenna; turning the loop matters more than moving it closer.

<sub>Pico 2 W illustration from [Pinout.xyz](https://github.com/pinout-xyz/Pinout.xyz), [CC BY-SA 4.0](https://github.com/pinout-xyz/Pinout.xyz?tab=CC-BY-SA-4.0-1-ov-file). `wiring.svg` is shared under the same license.</sub>

## Setup

1. Flash MicroPython for the [Pico 2 W](https://micropython.org/download/RPI_PICO2_W/) or [Pico W](https://micropython.org/download/RPI_PICO_W/).
2. Put your Wi-Fi name and password in `config.py`. Optionally, change `NTP_SERVER` to a server on your network.
3. Copy the files over and reset the Pico:

   ```
   mpremote cp main.py transmitter.py timecode.py ntp.py config.py :
   mpremote reset
   ```

The Pico starts transmitting at the next full minute and re-syncs with NTP every hour. Run `mpremote repl` to watch its progress.

Then start a manual sync on the clock (or pull and reinsert its battery) with the loop against it. Most clocks set themselves within 3–10 minutes.

## Testing

`python3 test_wwvb.py` checks the generated time code against Python's `datetime` for two years of minutes, including the DST changeovers.

## License

MIT, except `wiring.svg` (CC BY-SA 4.0, see above). Based on [Longwave Pico](https://github.com/carter-oswald-dev/Pi-pico-time-signal-generator-) by carter-oswald-dev.
