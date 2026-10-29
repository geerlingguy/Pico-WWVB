"""
transmitter.py — drives GPIO pin(s) as a low-frequency OOK (on/off keyed)
carrier for DCF77, MSF, WWVB, JJY, or BPC, gated by timecode.py's
per-second pulse frames.

A PIO state machine free-runs a square wave on the output pin(s), and
"gating" means handing the pin(s) between PIO control (carrier on) and
plain GPIO-low control (carrier off).

Antenna: the field a radio clock's ferrite rod picks up is magnetic, so
the coil needs current flowing through it — i.e. a closed loop, not a
wire with a free end.

  Differential (default, TX_PINS = (0, 1)): GP0 -> 220R -> coil -> GP1.
    The two pins toggle in antiphase, so the coil sees a 6.6 V swing
    instead of 3.3 V (about twice the current, twice the field).
  Single-ended (TX_PINS = (0,)): GP0 -> 220R -> coil -> GND.

Never connect a coil without the series resistor: a small air coil is
close to a dead short at 60 kHz.
"""

import rp2
import machine
import utime
import _thread

import timecode

try:
    import config as _cfg
    TX_PINS = tuple(getattr(_cfg, "TX_PINS", (0,)))
except ImportError:
    TX_PINS = (0,)  # original single-pin GP0 behavior

_ALT_PIO0 = getattr(machine.Pin, "ALT_PIO0", 6)

_sm = None
_playing = False
_stop_flag = False
_current_protocol = None
_current_carrier_hz = None
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


def _pins_low():
    for p in TX_PINS:
        machine.Pin(p, machine.Pin.OUT, value=0)


def _carrier_init(carrier_hz):
    """(Re)start the PIO state machine at the given carrier frequency."""
    global _sm
    if len(TX_PINS) == 2 and TX_PINS[1] != TX_PINS[0] + 1:
        raise ValueError("TX_PINS must be two consecutive GPIOs, e.g. (0, 1)")
    if _sm is not None:
        _sm.active(0)
    for p in TX_PINS:
        try:
            machine.Pin(p, machine.Pin.OUT, value=0, drive=machine.Pin.DRIVE_3)  # 12 mA
        except (AttributeError, TypeError, ValueError):
            machine.Pin(p, machine.Pin.OUT, value=0)
    prog = _squarewave_2pin if len(TX_PINS) == 2 else _squarewave_1pin
    # 2-instruction loop → carrier = PIO clock / 2. On a Pico 2 W at the
    # default 150 MHz, 120 kHz divides exactly (1250), so WWVB is 60.000 kHz.
    _sm = rp2.StateMachine(0, prog, freq=2 * carrier_hz,
                           set_base=machine.Pin(TX_PINS[0]))
    _sm.active(1)
    _pins_low()  # start masked (carrier off)


def _gate_on():
    for p in TX_PINS:
        machine.Pin(p).init(machine.Pin.ALT, alt=_ALT_PIO0)


def _gate_off():
    _pins_low()


def _teardown():
    global _sm
    _gate_off()
    if _sm is not None:
        _sm.active(0)
        _sm = None


# ---------------------------------------------------------------------------
# Pulse playback — walks one second's pulse descriptor from timecode.py,
# holding the carrier at the right level until absolute tick deadlines so
# drift doesn't accumulate across a frame.
# ---------------------------------------------------------------------------

def _hold_until(deadline_us, level):
    """Set the carrier to `level` ('high'/'low') and wait until deadline_us
    (a utime.ticks_us() value), checking _stop_flag."""
    if level == "high":
        _gate_on()
    else:
        _gate_off()
    while True:
        remaining = utime.ticks_diff(deadline_us, utime.ticks_us())
        if remaining <= 0:
            break
        if _stop_flag:
            return False
        if remaining > 2000:
            utime.sleep_us(min(remaining - 1000, 5000))
    return not _stop_flag


def _play_pulse(pulse, second_start_us):
    if "pattern" in pulse:
        t = second_start_us
        for ms, level in pulse["pattern"]:
            t = utime.ticks_add(t, ms * 1000)
            if not _hold_until(t, level):
                return False
        return True

    if "first_ms" in pulse:
        t1 = utime.ticks_add(second_start_us, pulse["first_ms"] * 1000)
        if not _hold_until(t1, pulse["first_level"]):
            return False
        t2 = utime.ticks_add(t1, pulse["second_ms"] * 1000)
        return _hold_until(t2, pulse["second_level"])

    # default shape: low for low_ms, then high for the rest of the second
    low_ms = pulse.get("low_ms", 0)
    high_ms = pulse.get("high_ms", 1000 - low_ms)
    if low_ms > 0:
        t1 = utime.ticks_add(second_start_us, low_ms * 1000)
        if not _hold_until(t1, "low"):
            return False
    t2 = utime.ticks_add(second_start_us, (low_ms + high_ms) * 1000)
    return _hold_until(t2, "high")


# ---------------------------------------------------------------------------
# Public control
# ---------------------------------------------------------------------------

def is_transmitting():
    return _playing


def current_protocol():
    return _current_protocol


def current_carrier_hz():
    """Actual carrier frequency in use (including any test offset), or None."""
    return _current_carrier_hz


def position():
    """(frame_epoch_s, frame_start_ticks_us) for the frame being sent right
    now, or None. Used by NTP mode to measure and correct drift."""
    return _position


def adjust(delta_us):
    """Shift all frames from the next frame boundary onward by delta_us
    (positive = later). Used to keep a long-running transmission locked to
    NTP as the Pico's crystal drifts."""
    global _pending_adjust_us
    _pending_adjust_us = int(delta_us)


def stop(wait_ms=0):
    """Signal the transmit thread to stop. Optionally wait (up to wait_ms)
    for it to finish masking the carrier."""
    global _stop_flag
    if not _playing:
        return
    _stop_flag = True
    t0 = utime.ticks_ms()
    while _playing and utime.ticks_diff(utime.ticks_ms(), t0) < wait_ms:
        utime.sleep_ms(10)


def _transmit_thread(protocol, frame_epoch_s, frame_start_us, freq_offset_hz):
    """Runs on the second core. frame_epoch_s must be a frame boundary (a
    multiple of the protocol's frame length) and frame_start_us the
    ticks_us() value at which that UTC second begins."""
    global _playing, _stop_flag, _current_protocol, _current_carrier_hz
    global _position, _pending_adjust_us
    _current_protocol = protocol
    try:
        carrier_hz = timecode.CARRIER_HZ[protocol] + freq_offset_hz
        _current_carrier_hz = carrier_hz
        _carrier_init(carrier_hz)

        # Carrier stays masked until the first frame boundary.
        if not _hold_until(frame_start_us, "low"):
            return

        current_epoch = frame_epoch_s
        while not _stop_flag:
            _position = (current_epoch, frame_start_us)
            pulses = timecode.encode(protocol, current_epoch)["pulses"]
            for i, pulse in enumerate(pulses):
                second_us = utime.ticks_add(frame_start_us, i * 1_000_000)
                if not _play_pulse(pulse, second_us):
                    return
            adj, _pending_adjust_us = _pending_adjust_us, 0
            frame_start_us = utime.ticks_add(frame_start_us, len(pulses) * 1_000_000 + adj)
            current_epoch += len(pulses)
    except Exception as e:
        print("Transmit error:", e)
    finally:
        _teardown()
        _position = None
        _current_protocol = None
        _current_carrier_hz = None
        _stop_flag = False
        _playing = False


def next_frame_boundary(protocol, epoch_s):
    n = timecode.FRAME_LEN_S.get(protocol, 60)
    return -(-int(epoch_s) // n) * n  # round up to a multiple of n


def start_at(protocol, frame_epoch_s, frame_start_ticks_us, freq_offset_hz=0):
    """Start transmitting with frame `frame_epoch_s` (a frame boundary, UTC
    epoch seconds) beginning exactly at tick `frame_start_ticks_us`. The
    caller supplies a precise (tick, time) relationship, e.g. from NTP."""
    global _playing, _stop_flag
    if _playing or protocol not in timecode.ENCODERS:
        return False
    _playing = True
    _stop_flag = False
    _thread.start_new_thread(
        _transmit_thread, (protocol, frame_epoch_s, frame_start_ticks_us, freq_offset_hz))
    return True


def start(protocol, start_epoch_s, freq_offset_hz=0):
    """Original API: treat 'right now' as UTC epoch second start_epoch_s and
    begin at the next frame boundary (frames must start on the minute, or
    the clock would decode a time that's off by however many seconds the
    start was misaligned)."""
    anchor_ticks_us = utime.ticks_us()
    anchor_epoch_s = int(start_epoch_s)
    frame_epoch = next_frame_boundary(protocol, anchor_epoch_s)
    frame_start_us = utime.ticks_add(
        anchor_ticks_us, (frame_epoch - anchor_epoch_s) * 1_000_000)
    return start_at(protocol, frame_epoch, frame_start_us, freq_offset_hz)
