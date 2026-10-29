"""
transmitter.py — drives GPIO pins as a low-frequency on/off-keyed carrier
for WWVB (or DCF77, MSF, JJY, BPC), gated by timecode.py's per-second
pulse frames.

A PIO state machine free-runs a square wave on the pins; "gating" hands
the pins between PIO control (carrier on) and plain GPIO-low (carrier off).

Antenna (see README): GP0 -> 220R -> loop -> GP1 for TX_PINS = (0, 1),
where the two pins toggle in antiphase; or GP0 -> 220R -> loop -> GND for
TX_PINS = (0,). Never connect the loop without the resistor.
"""

import rp2
import machine
import utime
import _thread

import config
import timecode

TX_PINS = tuple(config.TX_PINS)
_ALT_PIO0 = getattr(machine.Pin, "ALT_PIO0", 6)

_sm = None
_playing = False
_stop_flag = False
_position = None        # (frame_epoch_s, frame_start_ticks_us) of current frame
_pending_adjust_us = 0  # applied at the next frame boundary


@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW)
def _squarewave_1pin():
    wrap_target()
    set(pins, 1)
    set(pins, 0)
    wrap()


@rp2.asm_pio(set_init=(rp2.PIO.OUT_LOW, rp2.PIO.OUT_LOW))
def _squarewave_2pin():
    wrap_target()
    set(pins, 0b01)  # pin A high, pin B low
    set(pins, 0b10)  # pin A low, pin B high
    wrap()


def _gate_off():
    for p in TX_PINS:
        machine.Pin(p, machine.Pin.OUT, value=0)


def _gate_on():
    for p in TX_PINS:
        machine.Pin(p).init(machine.Pin.ALT, alt=_ALT_PIO0)


def _carrier_init(carrier_hz):
    global _sm
    if len(TX_PINS) == 2 and TX_PINS[1] != TX_PINS[0] + 1:
        raise ValueError("TX_PINS must be two consecutive GPIOs, e.g. (0, 1)")
    for p in TX_PINS:
        try:
            machine.Pin(p, machine.Pin.OUT, value=0, drive=machine.Pin.DRIVE_3)  # 12 mA
        except (AttributeError, TypeError, ValueError):
            machine.Pin(p, machine.Pin.OUT, value=0)
    prog = _squarewave_2pin if len(TX_PINS) == 2 else _squarewave_1pin
    # 2-instruction loop -> carrier = PIO clock / 2. On a Pico 2 W at the
    # default 150 MHz, 120 kHz divides exactly, so WWVB is 60.000 kHz.
    _sm = rp2.StateMachine(0, prog, freq=2 * carrier_hz, set_base=machine.Pin(TX_PINS[0]))
    _sm.active(1)
    _gate_off()


def _teardown():
    global _sm
    _gate_off()
    if _sm is not None:
        _sm.active(0)
        _sm = None


def _hold_until(deadline_us, level):
    """Set the carrier to level ('high'/'low') and wait until deadline_us
    (a ticks_us() value). Returns False if stopped."""
    if level == "high":
        _gate_on()
    else:
        _gate_off()
    while True:
        remaining = utime.ticks_diff(deadline_us, utime.ticks_us())
        if remaining <= 0:
            return not _stop_flag
        if _stop_flag:
            return False
        if remaining > 2000:
            utime.sleep_us(min(remaining - 1000, 5000))


def _play_pulse(pulse, second_us):
    """Play one second's pulse from timecode.py, starting at second_us."""
    if "pattern" in pulse:  # MSF
        steps = pulse["pattern"]
    elif "first_ms" in pulse:  # JJY
        steps = [(pulse["first_ms"], pulse["first_level"]),
                 (pulse["second_ms"], pulse["second_level"])]
    else:  # WWVB, DCF77, BPC: low for low_ms, then high for the rest
        low_ms = pulse.get("low_ms", 0)
        steps = [(low_ms, "low"), (pulse.get("high_ms", 1000 - low_ms), "high")]
    t = second_us
    for ms, level in steps:
        if ms <= 0:
            continue
        t = utime.ticks_add(t, ms * 1000)
        if not _hold_until(t, level):
            return False
    return True


def _transmit_thread(protocol, frame_epoch, frame_start_us):
    global _playing, _stop_flag, _position, _pending_adjust_us
    try:
        _carrier_init(timecode.CARRIER_HZ[protocol])
        if not _hold_until(frame_start_us, "low"):  # masked until the first frame
            return
        while not _stop_flag:
            _position = (frame_epoch, frame_start_us)
            pulses = timecode.encode(protocol, frame_epoch)["pulses"]
            for i, pulse in enumerate(pulses):
                if not _play_pulse(pulse, utime.ticks_add(frame_start_us, i * 1_000_000)):
                    return
            adj, _pending_adjust_us = _pending_adjust_us, 0
            frame_start_us = utime.ticks_add(frame_start_us, len(pulses) * 1_000_000 + adj)
            frame_epoch += len(pulses)
    except Exception as e:
        print("Transmit error:", e)
    finally:
        _teardown()
        _position = None
        _stop_flag = False
        _playing = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def next_frame_boundary(protocol, epoch_s):
    n = timecode.FRAME_LEN_S.get(protocol, 60)
    return -(-int(epoch_s) // n) * n  # round up to a multiple of n


def start(protocol, frame_epoch, frame_start_ticks_us):
    """Transmit on the second core, with frame `frame_epoch` (a frame
    boundary, UTC epoch seconds) beginning at tick frame_start_ticks_us."""
    global _playing, _stop_flag
    if _playing or protocol not in timecode.ENCODERS:
        return False
    _playing = True
    _stop_flag = False
    _thread.start_new_thread(_transmit_thread, (protocol, frame_epoch, frame_start_ticks_us))
    return True


def stop(wait_ms=0):
    global _stop_flag
    if not _playing:
        return
    _stop_flag = True
    t0 = utime.ticks_ms()
    while _playing and utime.ticks_diff(utime.ticks_ms(), t0) < wait_ms:
        utime.sleep_ms(10)


def is_transmitting():
    return _playing


def position():
    """(frame_epoch_s, frame_start_ticks_us) of the frame being sent, or None."""
    return _position


def adjust(delta_us):
    """Shift frames from the next boundary on by delta_us (positive = later)."""
    global _pending_adjust_us
    _pending_adjust_us = int(delta_us)
