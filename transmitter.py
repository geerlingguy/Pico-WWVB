"""
transmitter.py — drives a GPIO pin as a low-frequency OOK (on/off keyed)
carrier for DCF77, MSF, WWVB, JJY, or BPC, gated by timecode.py's
per-second pulse frames.

Adapted directly from this project family's existing Pico AM-radio
transmitter (pico_am_radio.py / Web-iinteractive/radio.py): a PIO state
machine free-runs a square wave on the output pin, and "gating" means
handing the pin between PIO control (carrier visible) and plain GPIO-low
control (carrier masked) — exactly the same technique, just retuned to
much lower carrier frequencies (40-77.5 kHz instead of ~1 MHz) and much
slower symbol timing (whole seconds instead of audio-rate note toggling).

Antenna note: solder ~20-30cm of wire to the chosen GPIO pin (GP0 by
default, following the AM-radio repo's reasoning: it's the top-left
corner pin, so a stray touch only risks a harmless neighboring GPIO
rather than a power/ground rail). Coiling the wire into a small loop
(a few turns around a pen or similar) concentrates the near-field
magnetic coupling these clocks' ferrite-rod antennas pick up, and tends
to work better than a straight wire at these frequencies.
"""

import rp2
import machine
import utime
import _thread

import timecode

TONE_GPIO = 0  # GP0 (physical pin 1) — see antenna note above

_sm = None
_carrier_hz = 60000
_playing = False
_stop_flag = False
_current_protocol = None
_current_carrier_hz = None
_lock = _thread.allocate_lock()


@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW)
def _squarewave():
    wrap_target()
    set(pins, 1)
    set(pins, 0)
    wrap()


def _carrier_init(carrier_hz):
    """(Re)start the PIO state machine at the given carrier frequency."""
    global _sm, _carrier_hz
    if _sm is not None:
        _sm.active(0)
    _carrier_hz = carrier_hz
    sys_clk = machine.freq()
    target_pio_hz = 2 * carrier_hz  # 2-instruction toggle loop
    target_pio_hz = min(target_pio_hz, sys_clk)
    _sm = rp2.StateMachine(
        0,
        _squarewave,
        freq=target_pio_hz,
        set_base=machine.Pin(TONE_GPIO),
    )
    _sm.active(1)
    machine.Pin(TONE_GPIO, machine.Pin.OUT, value=0)  # start masked (carrier off)


def _gate_on():
    """Hand the pin to PIO so the carrier is visible (ASK 'high')."""
    machine.Pin(TONE_GPIO).init(machine.Pin.ALT, alt=6)  # ALT6 = PIO0


def _gate_off():
    """Drive the pin low via plain GPIO, masking the carrier (ASK 'low')."""
    machine.Pin(TONE_GPIO, machine.Pin.OUT, value=0)


def _teardown():
    global _sm
    _gate_off()
    if _sm is not None:
        _sm.active(0)
        _sm = None


# ---------------------------------------------------------------------------
# Pulse playback — walks one second's pulse descriptor from timecode.py,
# holding the carrier at the right level for the right number of
# milliseconds, checked against a monotonic clock so drift doesn't
# accumulate across a whole frame.
# ---------------------------------------------------------------------------

def _hold_until(deadline_us, level):
    """Set the carrier to `level` ('high'/'low') and busy-wait/sleep until
    deadline_us (a utime.ticks_us() value), checking _stop_flag."""
    if level == "high":
        _gate_on()
    else:
        _gate_off()
    while utime.ticks_diff(deadline_us, utime.ticks_us()) > 0:
        if _stop_flag:
            return False
        remaining = utime.ticks_diff(deadline_us, utime.ticks_us())
        if remaining > 2000:
            utime.sleep_us(min(remaining - 1000, 5000))
    return not _stop_flag


def _play_pulse(pulse, second_start_us):
    """Play one second's pulse, timed relative to second_start_us (a
    utime.ticks_us() value marking the start of this second). Returns
    False if interrupted by _stop_flag."""
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
        if not _hold_until(t2, pulse["second_level"]):
            return False
        return True

    # default shape: low for low_ms, then high for the rest of the second
    low_ms = pulse.get("low_ms", 0)
    high_ms = pulse.get("high_ms", 1000 - low_ms)
    if low_ms > 0:
        t1 = utime.ticks_add(second_start_us, low_ms * 1000)
        if not _hold_until(t1, "low"):
            return False
    else:
        _gate_on()  # no low phase at all (e.g. DCF77 minute gap, BPC marker)
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
    """The actual carrier frequency in use right now, including any test
    offset applied via start()'s freq_offset_hz — None if not
    transmitting."""
    return _current_carrier_hz


def stop():
    """Signal the transmit thread to stop. Returns immediately; the
    thread finishes its current second and masks the carrier."""
    global _stop_flag
    _stop_flag = True


def _transmit_thread(protocol, start_epoch_s, anchor_ticks_us, anchor_epoch_s, freq_offset_hz):
    """Runs on the second core. Transmits `protocol` continuously,
    starting so that the frame beginning at `start_epoch_s` (a UTC epoch
    second) is played out exactly on time. anchor_ticks_us/anchor_epoch_s
    are a matched (monotonic-clock, wall-clock) pair captured by the
    caller as close as possible to the actual "tick" moment (a keypress
    or a web request's arrival) — capturing them in start(), before the
    new thread even begins, keeps that anchor as tight as possible rather
    than drifting by however long thread scheduling happens to take.

    freq_offset_hz shifts the carrier up (or down) from the protocol's
    real broadcast frequency — e.g. +500000 turns DCF77's 77.5 kHz into
    577.5 kHz — without changing anything about the bit timing itself.
    This is a test/reception aid: cheap RTL-SDR dongles typically can't
    tune below ~500 kHz, so shifting the whole signal up into range lets
    you verify the actual on/off pulse pattern on a spectrum display or
    in software, even though no real clock would be listening up there.
    Pass 0 for normal, on-frequency transmission."""
    global _playing, _stop_flag, _current_protocol, _current_carrier_hz
    _playing = True
    _stop_flag = False
    _current_protocol = protocol
    try:
        carrier_hz = timecode.CARRIER_HZ[protocol] + freq_offset_hz
        _current_carrier_hz = carrier_hz
        _carrier_init(carrier_hz)

        offset_us = (start_epoch_s - anchor_epoch_s) * 1_000_000
        frame_start_us = utime.ticks_add(anchor_ticks_us, offset_us)

        current_epoch = start_epoch_s
        while not _stop_flag:
            frame = timecode.encode(protocol, current_epoch)
            pulses = frame["pulses"]
            for i, pulse in enumerate(pulses):
                second_us = utime.ticks_add(frame_start_us, i * 1_000_000)
                if not _play_pulse(pulse, second_us):
                    return
            frame_start_us = utime.ticks_add(frame_start_us, len(pulses) * 1_000_000)
            current_epoch += len(pulses)
    except Exception as e:
        print("Transmit error:", e)
    finally:
        _teardown()
        _playing = False
        _stop_flag = False
        _current_protocol = None
        _current_carrier_hz = None


def start(protocol, start_epoch_s, freq_offset_hz=0):
    """Start transmitting `protocol` on the second core, beginning at
    `start_epoch_s` (UTC epoch seconds). No-op if already transmitting.

    freq_offset_hz (default 0, meaning transmit on the real frequency)
    shifts the carrier for reception testing — see _transmit_thread's
    docstring for why you'd want this.

    Captures the (monotonic tick, wall clock) anchor pair right here,
    synchronously, before handing off to the transmit thread — call this
    as early as possible after the moment you want to treat as "now"
    (e.g. immediately after a keypress or as soon as a request arrives),
    since everything after this call inherits whatever delay happened
    before it."""
    if _playing:
        return False
    if protocol not in timecode.ENCODERS:
        return False
    anchor_ticks_us = utime.ticks_us()
    anchor_epoch_s = utime.time()
    _thread.start_new_thread(
        _transmit_thread,
        (protocol, start_epoch_s, anchor_ticks_us, anchor_epoch_s, freq_offset_hz),
    )
    return True
