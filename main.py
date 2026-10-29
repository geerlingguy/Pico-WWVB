"""
main.py — Longwave Pico transmitter, boot entry point.

Two modes:

  1. NTP (default): set WIFI_SSID/WIFI_PASSWORD in config.py. The Pico
     joins your network, syncs over NTP, transmits PROTOCOL automatically
     (24/7 or during TX_HOURS_UTC), and re-syncs hourly to correct
     crystal drift.

  2. Access point (fallback when WIFI_SSID is empty): the Pico opens a
     Wi-Fi network named "LongwavePico" and serves a web page at
     http://192.168.4.1 that sets the Pico's clock from your phone's
     clock and starts/stops transmission.
"""

import time
import network
import socket
import machine

import timecode
import transmitter

AP_SSID = "LongwavePico"
AP_PASSWORD = ""  # open network — this is a local utility, not a security boundary
AP_IP = "192.168.4.1"

# Tracks whether the Pico's clock has been set from a real source (a
# connecting phone's auto-sync, or a manual entry) since the last power
# cycle. The RP2040/RP2350 has no battery-backed RTC, so this always
# starts False on a fresh boot — see the module docstring above.
_clock_synced = False
_last_sync_epoch = None  # Pico's own utime.time() reading at last sync


def set_pico_clock(epoch_s):
    """Set the Pico's hardware RTC to the given UTC epoch second (Unix
    epoch — confirmed the rp2 MicroPython port uses 1970, same as the
    epoch timecode.py already assumes throughout, so no conversion is
    needed here). Once set, utime.time() keeps advancing on its own from
    this point, independent of Wi-Fi staying connected."""
    global _clock_synced, _last_sync_epoch
    y, mo, d, hh, mm, ss, wd, _yday = time.gmtime(epoch_s)
    # machine.RTC().datetime() tuple order: (year, month, day, weekday,
    # hours, minutes, seconds, subseconds) — note weekday comes before
    # the time fields here, unlike time.gmtime()'s tuple order.
    rtc_weekday = wd  # time.gmtime() weekday is already 0=Mon..6=Sun, matching RTC's expectation
    machine.RTC().datetime((y, mo, d, rtc_weekday, hh, mm, ss, 0))
    _clock_synced = True
    _last_sync_epoch = time.time()


# ---------------------------------------------------------------------------
# Wi-Fi AP + web UI fallback (standalone, no PC needed)
# ---------------------------------------------------------------------------

def start_ap():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(ssid=AP_SSID, password=AP_PASSWORD, security=0)
    for _ in range(20):
        if ap.active():
            break
        time.sleep(0.1)
    print("AP started:", AP_SSID, "— connect and open http://" + AP_IP)
    return ap


PROTOCOL_LABELS = {
    "DCF77": "DCF77 — Germany, 77.5 kHz",
    "MSF": "MSF — UK, 60 kHz",
    "WWVB": "WWVB — USA, 60 kHz (UTC)",
    "JJY": "JJY — Japan, 40/60 kHz",
    "BPC": "BPC — China, 68.5 kHz",
}


def build_page(status_msg="", error_msg=""):
    transmitting = transmitter.is_transmitting()
    current_proto = transmitter.current_protocol() or ""
    current_hz = transmitter.current_carrier_hz()
    if transmitting and current_hz is not None:
        real_hz = timecode.CARRIER_HZ.get(current_proto, current_hz)
        if current_hz != real_hz:
            state_text = "TRANSMITTING (TEST: %d Hz, real is %d Hz)" % (current_hz, real_hz)
        else:
            state_text = "TRANSMITTING (%d Hz)" % current_hz
    else:
        state_text = "STOPPED"
    state_color = "#22c55e" if transmitting else "#94a3b8"

    options_html = ""
    for proto, label in PROTOCOL_LABELS.items():
        selected = " selected" if proto == current_proto else ""
        options_html += '<option value="%s"%s>%s</option>\n' % (proto, selected, label)

    now = time.localtime()
    default_date = "%04d-%02d-%02d" % (now[0], now[1], now[2])
    default_time = "%02d:%02d" % (now[3], now[4])

    start_disabled = "disabled" if transmitting else ""
    stop_disabled = "disabled" if not transmitting else ""

    status_block = ('<p class="status-msg">%s</p>' % status_msg) if status_msg else ""
    error_block = ('<p class="error-msg">%s</p>' % error_msg) if error_msg else ""

    offset_options_html = ""
    for h in range(-12, 15):
        label = "UTC" if h == 0 else ("UTC+%d" % h if h > 0 else "UTC%d" % h)
        selected = " selected" if h == 0 else ""
        offset_options_html += '<option value="%d"%s>%s</option>\n' % (h, selected, label)

    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Longwave Pico</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #0a0906; color: #e8e4d9;
    font-family: 'IBM Plex Mono', 'SF Mono', Consolas, monospace;
    display: flex; justify-content: center; align-items: flex-start;
    min-height: 100vh; padding: 2rem 1rem;
  }}
  .card {{
    background: #14120f; border: 1px solid #2c2820; border-radius: 0.5rem;
    padding: 1.75rem; width: 100%; max-width: 480px;
  }}
  h1 {{ font-size: 1.3rem; font-weight: 700; margin-bottom: 0.2rem; color: #e8a33d; }}
  .subtitle {{ color: #948d7c; font-size: 0.8rem; margin-bottom: 1.25rem; }}
  .badges {{ display: flex; gap: 0.5rem; margin-bottom: 1.25rem; flex-wrap: wrap; }}
  .badge {{
    display: inline-block; padding: 0.3rem 0.9rem; border-radius: 999px;
    font-weight: 700; font-size: 0.75rem; background: #0a0906;
    letter-spacing: 0.05em; border: 2px solid #94a3b8; color: #94a3b8;
  }}
  .state-badge {{ color: {state_color}; border-color: {state_color}; }}
  .sync-badge.synced {{ color: #22c55e; border-color: #22c55e; }}
  .sync-badge.syncing {{ color: #e8a33d; border-color: #e8a33d; }}
  .sync-badge.failed {{ color: #e07a5f; border-color: #e07a5f; }}
  .section {{ margin-bottom: 1.1rem; }}
  label {{ display: block; font-size: 0.75rem; color: #948d7c; margin-bottom: 0.3rem; }}
  select, input {{
    width: 100%; padding: 0.55rem 0.7rem; background: #1c1a15; color: #e8e4d9;
    border: 1px solid #2c2820; border-radius: 0.4rem; font-size: 0.95rem;
    font-family: inherit;
  }}
  select:disabled, input:disabled {{ opacity: 0.4; }}
  .row2 {{ display: flex; gap: 0.6rem; }}
  .row2 > div {{ flex: 1; }}
  .go-btn {{
    width: 100%; padding: 1rem; margin-top: 0.5rem; font-size: 1rem; font-weight: 700;
    letter-spacing: 0.04em; background: #e8a33d; color: #1a1204; border: none;
    border-radius: 0.4rem; cursor: pointer;
  }}
  .go-btn:disabled {{ background: #2c2820; color: #948d7c; }}
  .stop-btn {{
    width: 100%; padding: 0.7rem; margin-top: 0.6rem; font-weight: 700;
    background: #7a2020; color: #fff; border: none; border-radius: 0.4rem; cursor: pointer;
  }}
  .stop-btn:disabled {{ background: #2c2820; color: #948d7c; }}
  .hint {{
    background: #1c1a15; border: 1px solid #2c2820; border-radius: 0.4rem;
    padding: 0.7rem 0.9rem; font-size: 0.78rem; color: #948d7c; line-height: 1.6;
    margin: 0.9rem 0;
  }}
  .status-msg {{ color: #7ec98f; font-size: 0.8rem; margin-top: 0.5rem; }}
  .error-msg {{ color: #e07a5f; font-size: 0.8rem; margin-top: 0.5rem; }}
  hr {{ border: none; border-top: 1px solid #2c2820; margin: 1rem 0; }}
  details {{ margin-top: 0.4rem; }}
  summary {{
    cursor: pointer; font-size: 0.78rem; color: #948d7c; padding: 0.4rem 0;
  }}
  #sync-time {{ font-size: 0.72rem; color: #6b6455; margin-top: 0.3rem; }}
</style>
</head>
<body>
<div class="card">
  <h1>Longwave Pico</h1>
  <p class="subtitle">Standalone time-code transmitter — GP0 antenna</p>
  <div class="badges">
    <div class="badge state-badge">{state_text}</div>
    <div class="badge sync-badge syncing" id="sync-badge">SYNCING…</div>
  </div>
  <div id="sync-time"></div>

  <form method="POST" action="/start" id="start-form">
    <div class="section">
      <label for="protocol">Protocol</label>
      <select name="protocol" id="protocol" {start_disabled}>
        {options_html}
      </select>
    </div>

    <details>
      <summary>Frequency test mode (for SDR reception testing)</summary>
      <div class="section">
        <label for="freq_offset">Shift carrier up by</label>
        <select name="freq_offset" id="freq_offset" {start_disabled}>
          <option value="0" selected>Off — transmit on the real frequency</option>
          <option value="500000">+500 kHz (e.g. DCF77 → 577.5 kHz, WWVB → 560 kHz)</option>
          <option value="1000000">+1 MHz</option>
        </select>
      </div>
      <div class="hint">
        No clock will ever pick this up — it's for checking the actual
        on/off pulse pattern on an SDR or spectrum display when your
        receiver can't tune down to the real frequency (many cheap
        RTL-SDR dongles bottom out around 500 kHz). The bit timing is
        identical either way; only the carrier frequency moves.
      </div>
    </details>

    <div class="hint" id="auto-hint">
      This page reads your phone's own clock automatically — no need to
      watch a clock or tap on a tick. Once synced (above), just pick a
      protocol and hit Start.
    </div>

    <button class="go-btn" type="submit" id="start-btn" {start_disabled}>Start transmitting</button>
    {status_block}{error_block}

    <details>
      <summary>Manual time entry (only if auto-sync fails)</summary>
      <div class="row2 section">
        <div>
          <label for="date">Date to transmit</label>
          <input type="date" name="date" id="date" value="{default_date}">
        </div>
        <div>
          <label for="time">Time (HH:MM), your local time</label>
          <input type="time" name="time" id="time" value="{default_time}">
        </div>
      </div>
      <div class="section">
        <label for="offset">Your current UTC offset (e.g. UTC-4 for EDT)</label>
        <select name="offset" id="offset">
          {offset_options_html}
        </select>
      </div>
      <div class="hint">
        Only needed if the automatic sync above fails. Enter the date/time
        exactly as your own clock reads right now, pick your current UTC
        offset, then hit Start immediately.
      </div>
    </details>
  </form>

  <hr>

  <form method="POST" action="/stop">
    <button class="stop-btn" type="submit" {stop_disabled}>Stop transmitting</button>
  </form>
</div>

<script>
// Automatic time sync: measure round-trip time to the Pico and estimate
// its clock offset from this device's own (accurate, network-synced)
// clock, the same way NTP does it over a single local hop. Runs once on
// page load; no button needed.
async function syncClock() {{
  const badge = document.getElementById('sync-badge');
  const timeEl = document.getElementById('sync-time');
  try {{
    // First round trip: just measure latency to this specific Pico over
    // this specific Wi-Fi link (the /sync response body isn't otherwise
    // used — what matters is timing the round trip itself).
    const measureStart = Date.now();
    await fetch('/sync', {{cache: 'no-store'}});
    const roundTripMs = Date.now() - measureStart;

    // Second request: tell the Pico what time it is right now, adjusted
    // for half the measured round-trip (the Pico will receive this
    // request roughly that long after we send it, on a symmetric link).
    const sendAt = Date.now();
    const correctedEpochS = Math.round((sendAt + roundTripMs / 2) / 1000);
    const setResp = await fetch('/settime', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
      body: 'epoch=' + correctedEpochS
    }});
    const setData = await setResp.json();
    if (setData.ok) {{
      badge.textContent = 'CLOCK SYNCED';
      badge.className = 'badge sync-badge synced';
      timeEl.textContent = 'Round-trip: ' + roundTripMs + 'ms';
      document.getElementById('start-btn').disabled = false;
    }} else {{
      throw new Error(setData.error || 'unknown error');
    }}
  }} catch (e) {{
    badge.textContent = 'SYNC FAILED';
    badge.className = 'badge sync-badge failed';
    timeEl.textContent = 'Use manual entry below.';
  }}
}}
syncClock();
</script>
</body>
</html>""".format(
        state_color=state_color, state_text=state_text, options_html=options_html,
        default_date=default_date, default_time=default_time,
        offset_options_html=offset_options_html,
        start_disabled=start_disabled, stop_disabled=stop_disabled,
        status_block=status_block, error_block=error_block,
    )


def _parse_post_body(body_bytes):
    result = {}
    try:
        for pair in body_bytes.decode().split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                result[k] = v.replace("+", " ").replace("%3A", ":")
    except Exception:
        pass
    return result


def _send_response(conn, body, status="200 OK"):
    if isinstance(body, str):
        body = body.encode()
    header = (
        "HTTP/1.1 " + status + "\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "Content-Length: " + str(len(body)) + "\r\n"
        "Connection: close\r\n\r\n"
    )
    conn.send(header.encode())
    pos = 0
    while pos < len(body):
        conn.send(body[pos:pos + 512])
        pos += 512


def _send_redirect(conn, location="/"):
    header = (
        "HTTP/1.1 302 Found\r\n"
        "Location: " + location + "\r\n"
        "Connection: close\r\n\r\n"
    )
    conn.send(header.encode())


def _send_json(conn, body_json):
    body = body_json.encode()
    header = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: " + str(len(body)) + "\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "Connection: close\r\n\r\n"
    )
    conn.send(header.encode())
    conn.send(body)


def _local_date_time_to_epoch(date_str, time_str, utc_offset_hours):
    """date_str: 'YYYY-MM-DD', time_str: 'HH:MM', both as the person's own
    local clock will read them. utc_offset_hours is what they selected in
    the form (e.g. -4 for EDT). Converts to a UTC epoch: UTC = local -
    offset. From there, timecode.py derives each protocol's own reference
    time (CET/BST/UTC/JST/CST) exactly as it always has."""
    y, mo, d = [int(x) for x in date_str.split("-")]
    hh, mm = [int(x) for x in time_str.split(":")]
    local_epoch = timecode.epoch_from_utc_parts(y, mo, d, hh, mm, 0)
    return local_epoch - utc_offset_hours * 3600


def run_web_mode():
    start_ap()
    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    srv = socket.socket()
    srv.bind(addr)
    srv.listen(2)
    srv.settimeout(0.5)
    print("HTTP server listening on", AP_IP, ":80")

    status_msg = ""
    error_msg = ""

    while True:
        try:
            conn, _addr = srv.accept()
        except OSError:
            continue

        # (transmitter.start(), called below once we've parsed the
        # request, captures its own precise anchor synchronously before
        # spawning the transmit thread — see transmitter.py — so the
        # "tap GO on the tick" instant is captured as close to actual
        # request arrival as parsing overhead allows.)

        try:
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = conn.recv(256)
                if not chunk:
                    break
                request += chunk

            body = b""
            if b"Content-Length:" in request:
                try:
                    cl_line = [l for l in request.split(b"\r\n") if b"Content-Length:" in l][0]
                    cl = int(cl_line.split(b":")[1].strip())
                    header_end = request.index(b"\r\n\r\n") + 4
                    body = request[header_end:]
                    while len(body) < cl:
                        body += conn.recv(cl - len(body))
                except Exception:
                    pass

            first_line = request.split(b"\r\n")[0].decode()
            parts = first_line.split(" ")
            method = parts[0] if parts else "GET"
            path_with_query = parts[1] if len(parts) > 1 else "/"
            path = path_with_query.split("?", 1)[0]

            status_msg = ""
            error_msg = ""

            if method == "GET" and path in ("/", "/index.html"):
                _send_response(conn, build_page())

            elif method == "GET" and path == "/sync":
                # Used purely as a round-trip latency probe by the page's
                # auto-sync JS (see build_page()) — the response content
                # itself doesn't matter, only how long the request took.
                _send_json(conn, '{"ok": true}')

            elif method == "POST" and path == "/settime":
                params = _parse_post_body(body)
                try:
                    corrected_epoch_s = int(params.get("epoch", ""))
                    set_pico_clock(corrected_epoch_s)
                    _send_json(conn, '{"ok": true}')
                except Exception as e:
                    _send_json(conn, '{"ok": false, "error": "%s"}' % str(e))

            elif method == "GET" and path == "/status":
                y, mo, d, hh, mm, ss, wd, _yd = time.gmtime()
                carrier_hz = transmitter.current_carrier_hz()
                body_json = (
                    '{"synced": %s, "transmitting": %s, "protocol": "%s", '
                    '"carrier_hz": %s, "utc": "%04d-%02d-%02d %02d:%02d:%02d"}'
                ) % (
                    "true" if _clock_synced else "false",
                    "true" if transmitter.is_transmitting() else "false",
                    transmitter.current_protocol() or "",
                    carrier_hz if carrier_hz is not None else "null",
                    y, mo, d, hh, mm, ss,
                )
                _send_json(conn, body_json)

            elif method == "POST" and path == "/start":
                params = _parse_post_body(body)
                protocol = params.get("protocol", "").strip().upper()
                try:
                    freq_offset_hz = int(params.get("freq_offset", "0").strip() or "0")
                except ValueError:
                    freq_offset_hz = 0

                if transmitter.is_transmitting():
                    error_msg = "Already transmitting — stop first."
                    _send_response(conn, build_page(error_msg=error_msg))
                elif protocol not in timecode.ENCODERS:
                    error_msg = "Unknown protocol."
                    _send_response(conn, build_page(error_msg=error_msg))
                elif not _clock_synced:
                    error_msg = "Clock not synced yet — reload the page and wait a moment, or enter time manually below."
                    date_str = params.get("date", "").strip()
                    time_str = params.get("time", "").strip()
                    offset_str = params.get("offset", "0").strip()
                    if date_str and time_str:
                        try:
                            utc_offset_hours = int(offset_str)
                            target_epoch = _local_date_time_to_epoch(date_str, time_str, utc_offset_hours)
                            set_pico_clock(target_epoch)
                            ok = transmitter.start(protocol, target_epoch, freq_offset_hz)
                            if ok:
                                time.sleep(0.1)
                                _send_redirect(conn)
                            else:
                                error_msg = "Could not start transmission."
                                _send_response(conn, build_page(error_msg=error_msg))
                        except Exception:
                            error_msg = "Could not parse manually-entered date/time."
                            _send_response(conn, build_page(error_msg=error_msg))
                    else:
                        _send_response(conn, build_page(error_msg=error_msg))
                else:
                    # Clock is already synced (from the page's automatic
                    # background sync) — just start transmitting using the
                    # Pico's own current, already-accurate clock. No manual
                    # date/time entry or tick-watching needed at all.
                    target_epoch = time.time()
                    ok = transmitter.start(protocol, target_epoch, freq_offset_hz)
                    if ok:
                        time.sleep(0.1)
                        _send_redirect(conn)
                    else:
                        error_msg = "Could not start transmission."
                        _send_response(conn, build_page(error_msg=error_msg))

            elif method == "POST" and path == "/stop":
                transmitter.stop()
                time.sleep(0.2)
                _send_redirect(conn)

            else:
                _send_response(conn, "<h1>404</h1>", status="404 Not Found")

        except Exception as e:
            print("Request error:", e)
        finally:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# NTP mode (config.py with WIFI_SSID set): join Wi-Fi, sync, transmit
# ---------------------------------------------------------------------------

ANCHOR_MAX_AGE_MS = 300_000   # ticks_us wraps every ~17.9 min on rp2
SYNC_RETRY_MS = 30_000

_ntp_status = {
    "ntp_server": None, "last_sync_utc": None, "rtt_ms": None,
    "last_correction_ms": None, "syncs": 0, "errors": 0, "last_error": "",
    "ip": None,
}


def _load_config():
    try:
        import config
    except ImportError:
        return None
    return config if getattr(config, "WIFI_SSID", "") else None


def _wifi_connect(cfg, timeout_s=30):
    wlan = network.WLAN(network.STA_IF)
    if wlan.isconnected():
        return wlan
    try:
        import rp2
        rp2.country(getattr(cfg, "WIFI_COUNTRY", "US"))
    except Exception:
        pass
    try:
        network.hostname(getattr(cfg, "HOSTNAME", "longwave-pico"))
    except Exception:
        pass
    wlan.active(True)
    try:
        wlan.config(pm=network.WLAN.PM_NONE)  # steadier NTP round trips
    except Exception:
        pass
    print("Connecting to Wi-Fi", cfg.WIFI_SSID, "...")
    wlan.connect(cfg.WIFI_SSID, cfg.WIFI_PASSWORD)
    t0 = time.ticks_ms()
    while not wlan.isconnected():
        if time.ticks_diff(time.ticks_ms(), t0) > timeout_s * 1000:
            wlan.disconnect()
            raise OSError("Wi-Fi connect timed out (status %d)" % wlan.status())
        time.sleep_ms(200)
    _ntp_status["ip"] = wlan.ifconfig()[0]
    print("Wi-Fi connected:", _ntp_status["ip"])
    return wlan


def _ntp_sync(cfg):
    """Returns a fresh (ticks_us, epoch_us, rtt_us) anchor, or None."""
    import ntp
    server = getattr(cfg, "NTP_SERVER", "pool.ntp.org")
    _ntp_status["ntp_server"] = server
    try:
        _wifi_connect(cfg)
        anchor = ntp.query(server)
    except Exception as e:
        _ntp_status["errors"] += 1
        _ntp_status["last_error"] = str(e)
        print("NTP sync failed:", e)
        return None
    set_pico_clock(ntp.epoch_us_now(anchor) // 1_000_000)
    _ntp_status["syncs"] += 1
    _ntp_status["rtt_ms"] = anchor[2] / 1000
    y, mo, d, hh, mm, ss = time.gmtime()[:6]
    _ntp_status["last_sync_utc"] = "%04d-%02d-%02d %02d:%02d:%02d" % (y, mo, d, hh, mm, ss)
    print("NTP sync OK:", _ntp_status["last_sync_utc"], "UTC, rtt %.1f ms" % _ntp_status["rtt_ms"])
    return anchor


def _start_tx(cfg, anchor):
    import ntp
    protocol = cfg.PROTOCOL.upper()
    # First frame boundary at least 200 ms out, to leave time for PIO setup.
    s = -(-(ntp.epoch_us_now(anchor) + 200_000) // 1_000_000)
    frame_epoch = transmitter.next_frame_boundary(protocol, s)
    frame_ticks = ntp.ticks_for_epoch_us(anchor, frame_epoch * 1_000_000)
    if transmitter.start_at(protocol, frame_epoch, frame_ticks):
        print("Transmitting %s from %s UTC on GPIO %s" % (
            protocol, "%02d:%02d:%02d" % time.gmtime(frame_epoch)[3:6], transmitter.TX_PINS))


def _correct_drift(cfg, anchor):
    """Compare when the transmitter *thinks* the current frame started with
    when NTP says it did, and nudge the following frames to match."""
    import ntp
    pos = transmitter.position()
    if pos is None:
        return
    frame_epoch, frame_ticks = pos
    true_ticks = ntp.ticks_for_epoch_us(anchor, frame_epoch * 1_000_000)
    delta_us = time.ticks_diff(true_ticks, frame_ticks)
    _ntp_status["last_correction_ms"] = delta_us / 1000
    print("Drift correction: %+.1f ms" % (delta_us / 1000))
    if abs(delta_us) > 2_000_000:
        print("Large offset — restarting transmission")
        transmitter.stop(wait_ms=3000)
        _start_tx(cfg, anchor)
    else:
        transmitter.adjust(delta_us)


def _in_tx_window(cfg):
    hours = getattr(cfg, "TX_HOURS_UTC", None)
    return (not hours) or (time.gmtime()[3] in hours)


def _status_json(cfg):
    import json
    d = dict(_ntp_status)
    d["utc"] = "%04d-%02d-%02d %02d:%02d:%02d" % time.gmtime()[:6]
    d["protocol"] = cfg.PROTOCOL.upper()
    d["transmitting"] = transmitter.is_transmitting()
    d["carrier_hz"] = transmitter.current_carrier_hz()
    d["tx_pins"] = list(transmitter.TX_PINS)
    d["tx_hours_utc"] = list(getattr(cfg, "TX_HOURS_UTC", None) or [])
    return json.dumps(d)


def _serve_status_once(srv, cfg):
    try:
        conn, _addr = srv.accept()
    except OSError:
        return  # timeout: doubles as the main loop's sleep
    try:
        conn.settimeout(2)
        conn.recv(512)
        _send_json(conn, _status_json(cfg))
    except Exception as e:
        print("Status request error:", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def run_ntp_mode(cfg):
    print("NTP mode: %s via %s" % (cfg.PROTOCOL, getattr(cfg, "NTP_SERVER", "pool.ntp.org")))
    resync_ms = int(getattr(cfg, "NTP_RESYNC_S", 3600)) * 1000
    anchor = None
    anchor_ms = 0
    last_try_ms = None
    srv = None

    while True:
        now = time.ticks_ms()
        age = time.ticks_diff(now, anchor_ms) if anchor else None
        idle = _clock_synced and _in_tx_window(cfg) and not transmitter.is_transmitting()
        need_sync = (anchor is None or age >= resync_ms
                     or (idle and age > ANCHOR_MAX_AGE_MS))
        if need_sync and (last_try_ms is None
                          or time.ticks_diff(now, last_try_ms) >= SYNC_RETRY_MS):
            last_try_ms = now
            fresh = _ntp_sync(cfg)
            if fresh:
                anchor, anchor_ms = fresh, time.ticks_ms()
                if transmitter.is_transmitting():
                    _correct_drift(cfg, anchor)

        want_tx = _clock_synced and _in_tx_window(cfg)
        if want_tx and not transmitter.is_transmitting():
            if anchor and time.ticks_diff(time.ticks_ms(), anchor_ms) <= ANCHOR_MAX_AGE_MS:
                _start_tx(cfg, anchor)
        elif not want_tx and transmitter.is_transmitting():
            print("Outside TX_HOURS_UTC — stopping")
            transmitter.stop(wait_ms=2000)

        if srv is None and getattr(cfg, "STATUS_SERVER", True) and _ntp_status["ip"]:
            try:
                srv = socket.socket()
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                srv.bind(socket.getaddrinfo("0.0.0.0", 80)[0][-1])
                srv.listen(2)
                srv.settimeout(0.5)
                print("Status: http://%s/ (or http://%s.local/)" % (
                    _ntp_status["ip"], getattr(cfg, "HOSTNAME", "longwave-pico")))
            except Exception as e:
                print("Status server unavailable:", e)
                srv = False
        if srv:
            _serve_status_once(srv, cfg)
        else:
            time.sleep_ms(500)


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Longwave Pico booting...")
    _cfg = _load_config()
    if _cfg:
        run_ntp_mode(_cfg)
    else:
        print("WIFI_SSID not set in config.py — starting access-point mode.")
        run_web_mode()

