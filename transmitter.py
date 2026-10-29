"""
transmitter.py — 60 kHz WWVB carrier on GP0 and GP1, keyed by timecode.py.

A PIO state machine toggles the two pins in antiphase. Keying the carrier
off hands the pins back to plain GPIO, driven low.
"""

import rp2
import machine
import utime
import _thread

import timecode

CARRIER_HZ = 60_000
_pins = (machine.Pin(0), machine.Pin(1))  # physical pins 1 and 2

_running = False
_position = None  # (epoch_s, ticks_us) of the current frame's start
_adjust_us = 0    # applied at the next frame boundary


@rp2.asm_pio(set_init=(rp2.PIO.OUT_LOW, rp2.PIO.OUT_LOW))
def _carrier():
    wrap_target()
    set(pins, 0b01)
    set(pins, 0b10)
    wrap()


def _off():
    for p in _pins:
        p.init(machine.Pin.OUT, value=0)


def _on():
    for p in _pins:
        p.init(machine.Pin.ALT, alt=machine.Pin.ALT_PIO0)


def _wait_until(deadline):
    while True:
        left = utime.ticks_diff(deadline, utime.ticks_us())
        if left <= 0:
            return
        if left > 2000:
            utime.sleep_us(min(left - 1000, 5000))  # spin the last ~1 ms


def _run(epoch, start):
    global _running, _position, _adjust_us
    try:
        _off()
        # 2 instructions per cycle; 120 kHz divides 150 MHz exactly on a Pico 2.
        rp2.StateMachine(0, _carrier, freq=2 * CARRIER_HZ, set_base=_pins[0]).active(1)
        while True:
            _position = (epoch, start)
            for i, low_ms in enumerate(timecode.frame(epoch)):
                second = utime.ticks_add(start, i * 1_000_000)
                _off()
                _wait_until(utime.ticks_add(second, low_ms * 1000))
                _on()
                _wait_until(utime.ticks_add(second, 1_000_000))
            adj, _adjust_us = _adjust_us, 0
            start = utime.ticks_add(start, 60_000_000 + adj)
            epoch += 60
    except Exception as e:
        print("Transmit error:", e)
    finally:
        _off()
        _position = None
        _running = False


def start(epoch, ticks_us):
    """Start sending, with the minute at UTC epoch second `epoch` beginning
    at ticks_us(). Runs on the second core."""
    global _running
    _running = True
    _thread.start_new_thread(_run, (epoch, ticks_us))


def running():
    return _running


def position():
    return _position


def adjust(delta_us):
    """Shift frames from the next minute on by delta_us (positive = later)."""
    global _adjust_us
    _adjust_us = delta_us
