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
import gc
from math import log10
from flask import Flask, request, jsonify
from datetime import datetime

# ── Dashboard server ──────────────────────────────────────────────────────────
SERVER_URL     = os.getenv("SERVER_URL")
CAMERA_API_KEY = os.getenv("CAMERA_API_KEY")

# ── Roboflow ──────────────────────────────────────────────────────────────────
API_URL = "https://serverless.roboflow.com/ped-ttjij/2"
API_KEY = "H7235hFO9pR0ET91ohz0"

# ── Flask (receives data FROM the ESP32) ──────────────────────────────────────
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000

# ── General config ────────────────────────────────────────────────────────────
DISPLAY_WINDOW       = True
CONFIDENCE_THRESHOLD = 0.5
SEND_INTERVAL        = 300        # seconds between server posts
CLIPS_DIR            = "clips"    # folder where .avi clips are saved
CLIP_FPS             = 10         # must match ESP32 FRAME_DELAY_MS (100ms = 10fps)

# ── Virtual line ──────────────────────────────────────────────────────────────
LINE_VISIBLE       = True
LINE_ORIENTATION   = "vertical"   # "vertical" or "horizontal"
LINE_POSITION      = 320          # X pixel for vertical, Y pixel for horizontal
CROSSING_THRESHOLD = 30
LINE_COLOR         = (255, 0, 0)  # BGR
LINE_THICKNESS     = 3

# ── Shared state ──────────────────────────────────────────────────────────────
tracked_objects  = {}
inside_count     = 0
latest_db        = 0.0
latest_temp_c    = None
latest_humidity  = None
latest_frame     = None       # live frame during a clip
latest_preds     = None
frame_ready      = False
clip_playing     = False      # True while replaying a saved clip
clip_queue       = []         # paths of saved clips waiting to be played
state_lock       = threading.Lock()
clip_queue_lock  = threading.Lock()

os.makedirs(CLIPS_DIR, exist_ok=True)

app = Flask(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Flask routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/sensor", methods=["POST"])
def sensor():
    global latest_temp_c, latest_humidity
    data = request.get_json(force=True, silent=True) or {}
    temp = data.get("temperature")
    hum  = data.get("humidity")
    if temp is not None and hum is not None:
        with state_lock:
            latest_temp_c   = float(temp)
            latest_humidity = float(hum)
        print(f"[Sensor] Temp: {temp}°C  Hum: {hum}%")
    return jsonify({"status": "ok"}), 200


@app.route("/motion_clip", methods=["POST"])
def motion_clip():
    """
    Receive interleaved MJPEG + I2S audio stream from ESP32, save to disk,
    then queue for playback once the clip is complete.

    Binary wire format:
        [4 bytes LE uint32 frame_len] [frame_len bytes JPEG]
        [4 bytes LE uint32 audio_len] [audio_len bytes int32 PCM]
        ... repeating ...
        [4 bytes 0xFFFFFFFF]  <- end marker
    """
    global latest_frame, latest_db, latest_preds, frame_ready

    raw = request.environ.get("wsgi.input")
    if raw is None:
        return jsonify({"error": "no wsgi.input"}), 400

    buf = b""

    def read_bytes(n):
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

    # ── Prepare video writer ──────────────────────────────────────────────────
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    clip_path  = os.path.join(CLIPS_DIR, f"clip_{timestamp}.avi")
    writer     = None   # initialised on first frame so we know the frame size

    print(f"[Clip] Motion clip started → {clip_path}")
    last_inference_time = 0.0
    INFERENCE_INTERVAL  = 0.5

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
            print(f"[Clip] Implausible length {length} — aborting")
            break

        payload = read_bytes(length)
        if payload is None:
            break

        if payload[:2] == b'\xff\xd8':
            # ── JPEG frame ────────────────────────────────────────────────────
            img_array = np.frombuffer(payload, dtype=np.uint8)
            frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if frame is not None:

                # Initialise writer on first frame so we know the resolution
                if writer is None:
                    h, w   = frame.shape[:2]
                    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
                    writer = cv2.VideoWriter(clip_path, fourcc, CLIP_FPS, (w, h))
                    print(f"[Clip] Writer opened: {w}x{h} @ {CLIP_FPS}fps")

                # Draw overlays onto the frame before saving so the clip is annotated
                annotated = frame.copy()
                annotated = draw_virtual_line(annotated)
                with state_lock:
                    preds_snap = latest_preds
                if preds_snap:
                    annotated = draw_detections(annotated, preds_snap)
                with state_lock:
                    tc = latest_temp_c
                    hm = latest_humidity
                    db = latest_db
                annotated = draw_sensor_overlay(annotated, tc, hm, db)
                writer.write(annotated)

                # Update live preview — drop frame if display hasn't caught up
                with state_lock:
                    if not frame_ready:
                        latest_frame = annotated.copy()
                        frame_ready  = True

                # Roboflow inference
                now = time.time()
                if now - last_inference_time >= INFERENCE_INTERVAL:
                    preds = run_inference(frame)
                    last_inference_time = now
                    if preds:
                        with state_lock:
                            latest_preds = preds
                        check_line_crossing(preds)
        else:
            # ── I2S audio chunk ───────────────────────────────────────────────
            if len(payload) >= 4:
                samples_32 = np.frombuffer(payload, dtype=np.int32)
                samples_f  = (samples_32.astype(np.int64) >> 8).astype(np.float32)
                rms = np.sqrt(np.mean(samples_f ** 2)) if len(samples_f) > 0 else 0.0
                if rms > 0:
                    db = 20 * log10(rms / 8_388_608.0) + 120
                    db = max(0.0, min(120.0, db))
                    with state_lock:
                        latest_db = db

    # ── Finalise clip ─────────────────────────────────────────────────────────
    if writer is not None:
        writer.release()
        print(f"[Clip] Saved → {clip_path}")
        with clip_queue_lock:
            clip_queue.append(clip_path)
    else:
        print("[Clip] No frames received — nothing saved")

    return jsonify({"status": "ok"}), 200


# ─────────────────────────────────────────────────────────────────────────────
# Roboflow inference
# ─────────────────────────────────────────────────────────────────────────────

def frame_to_base64(frame):
    pil_image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    buffered   = BytesIO()
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
    if not LINE_VISIBLE:
        return frame
    h, w = frame.shape[:2]
    if LINE_ORIENTATION == "vertical":
        cv2.line(frame, (LINE_POSITION, 0), (LINE_POSITION, h), LINE_COLOR, LINE_THICKNESS)
        cv2.arrowedLine(frame, (LINE_POSITION - 40, 50), (LINE_POSITION - 10, 50), (0, 0, 255), 2)
        cv2.putText(frame, "OUT", (LINE_POSITION - 80, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        cv2.arrowedLine(frame, (LINE_POSITION + 10, 50), (LINE_POSITION + 40, 50), (0, 255, 0), 2)
        cv2.putText(frame, "IN",  (LINE_POSITION + 50, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    else:
        cv2.line(frame, (0, LINE_POSITION), (w, LINE_POSITION), LINE_COLOR, LINE_THICKNESS)
        cv2.arrowedLine(frame, (50, LINE_POSITION - 10), (50, LINE_POSITION - 40), (0, 0, 255), 2)
        cv2.putText(frame, "OUT", (60, LINE_POSITION - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        cv2.arrowedLine(frame, (50, LINE_POSITION + 10), (50, LINE_POSITION + 40), (0, 255, 0), 2)
        cv2.putText(frame, "IN",  (60, LINE_POSITION + 35), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    return frame


def draw_sensor_overlay(frame, temp_c, humidity, db):
    y        = frame.shape[0] - 15
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
        if LINE_ORIENTATION == "vertical":
            oid = f"{cls}_{cy // 50}"
            pos = cx
        else:
            oid = f"{cls}_{cx // 50}"
            pos = cy
        current_ids.add(oid)
        if oid in tracked_objects:
            prev_pos = tracked_objects[oid]
            if prev_pos < LINE_POSITION - CROSSING_THRESHOLD and pos > LINE_POSITION + CROSSING_THRESHOLD:
                inside_count += 1
                print(f">>> {cls} ENTERED | Inside: {inside_count}")
            elif prev_pos > LINE_POSITION + CROSSING_THRESHOLD and pos < LINE_POSITION - CROSSING_THRESHOLD:
                inside_count = max(0, inside_count - 1)
                print(f"<<< {cls} EXITED  | Inside: {inside_count}")
        tracked_objects[oid] = pos
    for oid in [o for o in list(tracked_objects) if o not in current_ids]:
        del tracked_objects[oid]


# ─────────────────────────────────────────────────────────────────────────────
# Clip playback — called from display loop on main thread
# ─────────────────────────────────────────────────────────────────────────────

def play_clip(clip_path):
    """Play back a saved clip in the display window at the original frame rate."""
    global clip_playing
    clip_playing = True

    cap = cv2.VideoCapture(clip_path)
    if not cap.isOpened():
        print(f"[Playback] Could not open {clip_path}")
        clip_playing = False
        return

    fps       = cap.get(cv2.CAP_PROP_FPS) or CLIP_FPS
    delay_ms  = max(1, int(1000 / fps))
    frame_num = 0
    total     = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    clip_name = os.path.basename(clip_path)

    print(f"[Playback] Playing {clip_name} ({total} frames @ {fps:.1f}fps)")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_num += 1
        # Banner so user knows this is a replay not a live feed
        banner = f"REPLAY: {clip_name}  [{frame_num}/{total}]  (Q=skip)"
        cv2.putText(frame, banner, (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 2)

        cv2.imshow("CCTV Detection System", frame)
        key = cv2.waitKey(delay_ms) & 0xFF
        if key in (ord("q"), ord("Q")):
            print("[Playback] Skipped by user")
            break

    cap.release()
    clip_playing = False
    print(f"[Playback] Finished {clip_name}")


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
# Periodic memory cleanup
# ─────────────────────────────────────────────────────────────────────────────

def periodic_cleanup():
    while True:
        time.sleep(60)
        gc.collect()
        with state_lock:
            global tracked_objects
            if len(tracked_objects) > 100:
                tracked_objects.clear()
                print("[Cleanup] tracked_objects overflow — cleared")
        print(f"[Cleanup] GC collected | tracked_objects: {len(tracked_objects)}")


# ─────────────────────────────────────────────────────────────────────────────
# Display loop (runs in main thread)
# ─────────────────────────────────────────────────────────────────────────────

def display_loop():
    global frame_ready

    frame_count  = 0
    current_mode = "day"

    while True:

        # ── Check for a queued clip to replay ────────────────────────────────
        pending = None
        with clip_queue_lock:
            if clip_queue:
                pending = clip_queue.pop(0)

        if pending:
            play_clip(pending)
            continue   # loop back — check for more queued clips before going live

        # ── Live feed ─────────────────────────────────────────────────────────
        with state_lock:
            frame       = latest_frame.copy() if latest_frame is not None else None
            preds       = latest_preds
            db          = latest_db
            temp_c      = latest_temp_c
            humidity    = latest_humidity
            frame_ready = False   # consumed — Flask may write next frame

        if frame is None:
            placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(placeholder, "Waiting for ESP32-CAM...", (120, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
            cv2.imshow("CCTV Detection System", placeholder)
            cv2.waitKey(100)
            continue

        frame_count += 1
        status = f"LIVE  Frame:{frame_count} | Inside:{inside_count} | Mode:{current_mode.upper()}"
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
    print(f"  Send every : {SEND_INTERVAL}s")
    print(f"  Clips dir  : {os.path.abspath(CLIPS_DIR)}\n")

    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

    threading.Thread(
        target=lambda: app.run(
            host=FLASK_HOST,
            port=FLASK_PORT,
            threaded=True,
            use_reloader=False,
        ),
        daemon=True
    ).start()

    threading.Thread(target=periodic_send,    daemon=True).start()
    threading.Thread(target=periodic_cleanup, daemon=True).start()

    if DISPLAY_WINDOW:
        display_loop()
    else:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    print("Stopped.")


if __name__ == "__main__":
    main()
