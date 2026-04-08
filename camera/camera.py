#!/usr/bin/env python3

from dotenv import load_dotenv
load_dotenv()

import cv2
import time
import requests
import base64
import numpy as np
from io import BytesIO
from PIL import Image
import os
import threading
import struct
from math import log10
from flask import Flask, request, jsonify

# ── Dashboard server ──────────────────────────────────────────────────────────
SERVER_URL     = os.getenv("SERVER_URL")
CAMERA_API_KEY = os.getenv("CAMERA_API_KEY")

# ── Roboflow ──────────────────────────────────────────────────────────────────
API_URL = os.getenv("https://serverless.roboflow.com/people-detection-o4rdr/12")
API_KEY = os.getenv("H7235hFO9pR0ET91ohz0")

# ── Flask (receives data FROM the ESP32) ──────────────────────────────────────
FLASK_HOST = "0.0.0.0"
FLASK_PORT = int(os.getenv("24.208.193.184", "5000"))  

# ── General config ────────────────────────────────────────────────────────────
DISPLAY_WINDOW       = True
CONFIDENCE_THRESHOLD = 0.5
SEND_INTERVAL        = 300   # seconds between server posts

# ── Virtual line ──────────────────────────────────────────────────────────────
LINE_POSITION      = 320
CROSSING_THRESHOLD = 30

# ── Shared state (written by Flask threads, read by display thread) ───────────
tracked_objects  = {}
inside_count     = 0
latest_db        = 0.0
latest_temp_c    = None
latest_humidity  = None
latest_frame     = None          # most recent decoded JPEG from ESP32
latest_preds     = None          # most recent Roboflow result
state_lock       = threading.Lock()

app = Flask(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Flask routes — ESP32 pushes data here
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/sensor", methods=["POST"])
def sensor():
    """Receive DHT22 temperature + humidity from ESP32."""
    global latest_temp_c, latest_humidity
    data = request.get_json(force=True, silent=True) or {}
    temp = data.get("temperature")
    hum  = data.get("humidity")
    if temp is not None and hum is not None:
        with state_lock:
            latest_temp_c  = float(temp)
            latest_humidity = float(hum)
        print(f"[Sensor] Temp: {temp}°C  Hum: {hum}%")
    return jsonify({"status": "ok"}), 200


@app.route("/motion_clip", methods=["POST"])
def motion_clip():
    """
    Receive interleaved MJPEG + I2S audio stream from ESP32.

    Binary wire format (matches ESP32 sketch exactly):
        [4 bytes LE uint32 frame_len] [frame_len bytes JPEG]
        [4 bytes LE uint32 audio_len] [audio_len bytes int32 PCM]
        ... repeating ...
        [4 bytes 0xFFFFFFFF]  ← end marker

    IMPORTANT: Flask/Werkzeug buffers request.stream by default.
    We access wsgi.input directly and set stream_factory to avoid buffering.
    The app must be run with use_reloader=False and threaded=True.
    """
    global latest_frame, latest_db, latest_preds

    # Read directly from the WSGI input — avoids Werkzeug's content-length buffering
    raw = request.environ.get("wsgi.input")
    if raw is None:
        return jsonify({"error": "no wsgi.input"}), 400

    buf = b""

    def read_bytes(n):
        """Read exactly n bytes from the raw WSGI stream, buffering across TCP packets."""
        nonlocal buf
        while len(buf) < n:
            try:
                chunk = raw.read(min(8192, n - len(buf)))
            except Exception:
                return None
            if not chunk:
                return None
            buf += chunk
        out, buf = buf[:n], buf[n:]
        return out

    print("[Clip] Motion clip started")
    last_inference_time = 0.0
    INFERENCE_INTERVAL  = 0.5   # max one Roboflow call per 500 ms during a clip

    while True:
        hdr = read_bytes(4)
        if hdr is None or len(hdr) < 4:
            print("[Clip] Stream ended unexpectedly")
            break

        length = struct.unpack("<I", hdr)[0]

        if length == 0xFFFFFFFF:
            print("[Clip] End marker received")
            break

        if length == 0 or length > 200_000:
            print(f"[Clip] Implausible length {length} — stream likely desynced, aborting")
            break

        payload = read_bytes(length)
        if payload is None:
            break

        if payload[:2] == b'\xff\xd8':
            # ── JPEG video frame ──────────────────────────────────────────────
            img_array = np.frombuffer(payload, dtype=np.uint8)
            frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if frame is not None:
                with state_lock:
                    latest_frame = frame.copy()

                # Throttle Roboflow calls — inference is slower than 10fps
                now = time.time()
                if now - last_inference_time >= INFERENCE_INTERVAL:
                    preds = run_inference(frame)
                    last_inference_time = now
                    if preds:
                        # Hold lock for the shortest possible window
                        with state_lock:
                            latest_preds = preds
                        # check_line_crossing needs state_lock too — call outside
                        check_line_crossing(preds)
        else:
            # ── I2S audio chunk (int32 → shift to 16-bit range for dB) ────────
            if len(payload) >= 4:
                samples_32 = np.frombuffer(payload, dtype=np.int32)
                # INMP441 data sits in the top 24 bits of the 32-bit I2S frame;
                # arithmetic right-shift by 8 preserves sign, gives 24-bit values
                # then scale to float for RMS (don't cast to int16 — clips signal)
                samples_f = (samples_32.astype(np.int64) >> 8).astype(np.float32)
                rms = np.sqrt(np.mean(samples_f ** 2)) if len(samples_f) > 0 else 0.0
                if rms > 0:
                    # Normalise against 24-bit full scale (2^23)
                    db = 20 * log10(rms / 8_388_608.0) + 120
                    db = max(0.0, min(120.0, db))
                    with state_lock:
                        latest_db = db

    print("[Clip] Motion clip complete")
    return jsonify({"status": "ok"}), 200


# ─────────────────────────────────────────────────────────────────────────────
# Roboflow inference
# ─────────────────────────────────────────────────────────────────────────────

def frame_to_base64(frame):
    pil_image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    buffered = BytesIO()
    pil_image.save(buffered, format="JPEG", quality=85)
    return base64.b64encode(buffered.getvalue()).decode()


def run_inference(frame):
    try:
        img_base64 = frame_to_base64(frame)
        response = requests.post(
            API_URL,
            params={"api_key": API_KEY, "confidence": int(CONFIDENCE_THRESHOLD * 100)},
            data=img_base64,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=5,
        )
        return response.json() if response.status_code == 200 else None
    except Exception as e:
        print(f"[Roboflow] Error: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Drawing helpers
# ─────────────────────────────────────────────────────────────────────────────

def draw_detections(frame, predictions):
    if not predictions or "predictions" not in predictions:
        return frame
    for pred in predictions["predictions"]:
        if pred["confidence"] < CONFIDENCE_THRESHOLD:
            continue
        x  = int(pred["x"] - pred["width"]  / 2)
        y  = int(pred["y"] - pred["height"] / 2)
        w  = int(pred["width"])
        h  = int(pred["height"])
        cx = int(pred["x"])
        cy = int(pred["y"])
        label = f"{pred.get('class', 'object')}: {pred['confidence']:.2f}"
        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.circle(frame, (cx, cy), 5, (0, 0, 255), -1)
        lsz, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)
        cv2.rectangle(frame, (x, y - lsz[1] - 10), (x + lsz[0], y), (0, 255, 0), -1)
        cv2.putText(frame, label, (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2)
    return frame


def draw_virtual_line(frame):
    h = frame.shape[0]
    cv2.line(frame, (LINE_POSITION, 0), (LINE_POSITION, h), (255, 0, 0), 3)
    cv2.arrowedLine(frame, (LINE_POSITION - 40, 50), (LINE_POSITION - 10, 50), (0, 0, 255), 2)
    cv2.putText(frame, "OUT", (LINE_POSITION - 80, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    cv2.arrowedLine(frame, (LINE_POSITION + 10, 50), (LINE_POSITION + 40, 50), (0, 255, 0), 2)
    cv2.putText(frame, "IN",  (LINE_POSITION + 50, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    return frame


def draw_sensor_overlay(frame, temp_c, humidity, db):
    y = frame.shape[0] - 15
    temp_f   = (temp_c * 9 / 5 + 32) if temp_c is not None else None
    temp_str = f"Temp: {temp_c:.1f}C / {temp_f:.1f}F" if temp_c is not None else "Temp: --"
    hum_str  = f"Hum: {humidity:.1f}%" if humidity is not None else "Hum: --"
    db_str   = f"Sound: {db:.1f} dB"
    cv2.putText(frame, f"  {temp_str}   {hum_str}   {db_str}  ",
                (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
    return frame


# ─────────────────────────────────────────────────────────────────────────────
# Line crossing
# ─────────────────────────────────────────────────────────────────────────────

def check_line_crossing(predictions):
    global tracked_objects, inside_count
    if not predictions or "predictions" not in predictions:
        return
    current_ids = set()
    for pred in predictions["predictions"]:
        if pred["confidence"] < CONFIDENCE_THRESHOLD:
            continue
        cx  = int(pred["x"])
        cy  = int(pred["y"])
        cls = pred.get("class", "object")
        oid = f"{cls}_{cy // 50}"
        current_ids.add(oid)
        if oid in tracked_objects:
            prev_x = tracked_objects[oid]
            if prev_x < LINE_POSITION - CROSSING_THRESHOLD and cx > LINE_POSITION + CROSSING_THRESHOLD:
                inside_count += 1
                print(f">>> {cls} ENTERED | Inside: {inside_count}")
            elif prev_x > LINE_POSITION + CROSSING_THRESHOLD and cx < LINE_POSITION - CROSSING_THRESHOLD:
                inside_count = max(0, inside_count - 1)
                print(f"<<< {cls} EXITED  | Inside: {inside_count}")
        tracked_objects[oid] = cx
    stale = [oid for oid in tracked_objects if oid not in current_ids]
    for oid in stale[: max(0, len(stale) - 10)]:
        del tracked_objects[oid]


# ─────────────────────────────────────────────────────────────────────────────
# Periodic server post
# ─────────────────────────────────────────────────────────────────────────────

def send_to_server(count, avg_db, temp_c, humidity):
    try:
        payload = {
            "buildingId":  "Example Location",
            "count":       count,
            "soundLevel":  avg_db,
            "temperature": temp_c,
            "humidity":    humidity,
        }
        resp = requests.post(
            SERVER_URL,
            headers={"x-api-key": CAMERA_API_KEY, "Content-Type": "application/json"},
            json=payload,
            timeout=5,
        )
        print(f"[Server] Posted → {resp.status_code} | {payload}")
    except Exception as e:
        print(f"[Server] Post failed: {e}")


def periodic_send():
    while True:
        time.sleep(SEND_INTERVAL)
        with state_lock:
            count    = inside_count
            db       = latest_db
            temp_c   = latest_temp_c
            humidity = latest_humidity
        send_to_server(count, db, temp_c, humidity)


# ─────────────────────────────────────────────────────────────────────────────
# Display loop (runs in main thread)
# ─────────────────────────────────────────────────────────────────────────────

def display_loop():
    """Show the latest frame received from ESP32 with overlays."""
    frame_count  = 0
    current_mode = "day"

    while True:
        with state_lock:
            frame    = latest_frame.copy() if latest_frame is not None else None
            preds    = latest_preds
            db       = latest_db
            temp_c   = latest_temp_c
            humidity = latest_humidity

        if frame is None:
            # Show placeholder while waiting for first clip
            placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(placeholder, "Waiting for ESP32-CAM...", (120, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
            cv2.imshow("CCTV Detection System", placeholder)
        else:
            frame_count += 1
            frame = draw_virtual_line(frame)
            if preds:
                frame = draw_detections(frame, preds)
            frame = draw_sensor_overlay(frame, temp_c, humidity, db)
            status = f"Frame:{frame_count} | Inside:{inside_count} | Mode:{current_mode.upper()}"
            cv2.putText(frame, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            cv2.imshow("CCTV Detection System", frame)

        key = cv2.waitKey(30) & 0xFF
        if key in (ord("q"), ord("Q")):
            print("\nQuitting...")
            break
        elif key in (ord("n"), ord("N")):
            current_mode = "night"
        elif key in (ord("d"), ord("D")):
            current_mode = "day"

    cv2.destroyAllWindows()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("Starting ESP32-CAM CCTV receiver")
    print(f"  Flask port : {FLASK_PORT}  (ESP32 must point PI_PORT here)")
    print(f"  Roboflow   : {API_URL}")
    print(f"  Send every : {SEND_INTERVAL}s\n")

    # Allow large clip uploads (10s @ 10fps VGA JPEG ≈ 10–30 MB)
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB hard cap

    # Flask runs in a background thread so display loop owns the main thread.
    # use_reloader=False is required when Flask is not in the main thread.
    # threaded=True lets /sensor and /motion_clip be handled concurrently.
    threading.Thread(
        target=lambda: app.run(
            host=FLASK_HOST,
            port=FLASK_PORT,
            threaded=True,
            use_reloader=False,
        ),
        daemon=True
    ).start()

    threading.Thread(target=periodic_send, daemon=True).start()

    if DISPLAY_WINDOW:
        display_loop()
    else:
        # Headless — just keep alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    print("Stopped.")


if __name__ == "__main__":
    main()

