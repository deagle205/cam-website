#!/usr/bin/env python3
"""
Raspberry Pi 5 Live Camera with Roboflow CCTV Person Detection
OV5647 ArduCam with Day/Night modes - Custom Model Support
"""
from dotenv import load_dotenv
import cv2
import time
import requests
import base64
from picamera2 import Picamera2
import numpy as np
from io import BytesIO
from PIL import Image
import os
import threading
import RPi.GPIO as GPIO
import time
import serial
from math import log10

ser= serial.Serial('/dev/ttyACM0', 9600, timeout=1)
time.sleep(2)

interval = 300

def binary_to_DB(binary_string):
    decimal_value = int(binary_string, 2)
   
    voltage = decimal_value * (5.0 / 1023.0)
   
    if voltage < 0.001:
        voltage = 0.001
       
    db = 20 * log10(voltage / 0.00631)
   
    if db < 0:
        db = 0
    if db > 120:
        db = 120
       
    return db

   

SERVER_URL = os.getenv("SERVER_URL")
CAMERA_API_KEY = os.getenv("CAMERA_API_KEY")

# Roboflow API Configuration
API_URL = os.getenv("API_URL")
API_KEY =  os.getenv("API_KEY")

# Configuration
DISPLAY_WINDOW = True  # Set to False if running headless
INFERENCE_INTERVAL = 0.5  # Seconds between API calls
CONFIDENCE_THRESHOLD = 0.5  # Minimum confidence to display detections

# Virtual line configurationq
LINE_POSITION = 320  # X position of vertical line (center of 640px frame)
CROSSING_THRESHOLD = 30  # Pixels object must cross to register

# Tracking
tracked_objects = {}  # Store object positions
inside_count = 0  # Count of objects currently inside

# Camera modes
NIGHT_MODE = {
    "AeEnable": True,
    "AwbEnable": False,
    "ExposureTime": 33000,
    "AnalogueGain": 8.0,
    "Brightness": 0.1,
    "Contrast": 1.2,
    "Saturation": 0.8
}

DAY_MODE = {
    "AeEnable": True,
    "AwbEnable": True,
    "ExposureTime": 20000,
    "AnalogueGain": 4.0,
    "Brightness": 0.2,
    "Contrast": 1.1,
    "Saturation": 1.0
}

def setup_camera(mode="day"):
    """Initialize ArduCam OV5647 with selectable day/night settings"""
    picam2 = Picamera2()

    # List available cameras (helpful for debugging)
    cameras = picam2.global_camera_info()
    print(f"Available cameras: {cameras}")

    # Select mode settings
    controls = NIGHT_MODE if mode == "night" else DAY_MODE

    # Configure for OV5647
    config = picam2.create_preview_configuration(
        main={"size": (640, 480)},
        controls=controls
    )
    picam2.configure(config)
    picam2.start()
    time.sleep(3)  # Allow camera to warm up and adjust to lighting
    print(f"OV5647 ArduCam initialized in {mode.upper()} mode")
    return picam2

def switch_camera_mode(picam2, mode):
    """Switch between day and night mode"""
    controls = NIGHT_MODE if mode == "night" else DAY_MODE
    picam2.set_controls(controls)
    print(f"Switched to {mode.upper()} mode")
    return mode

def frame_to_base64(frame):
    """Convert frame to base64 for API upload - handles OV5647 color format"""
    # Frame comes in as RGB from picamera2, convert to proper format for PIL
    pil_image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

    # Save to bytes buffer
    buffered = BytesIO()
    pil_image.save(buffered, format="JPEG", quality=85)

    # Encode to base64
    img_str = base64.b64encode(buffered.getvalue()).decode()
    return img_str

def run_inference(frame):
    """Send frame to Roboflow API and get predictions"""
    try:
        img_base64 = frame_to_base64(frame)
        response = requests.post(
            API_URL,
            params={
                "api_key": API_KEY,
                "confidence": int(CONFIDENCE_THRESHOLD * 100)
            },
            data=img_base64,
            headers={
                "Content-Type": "application/x-www-form-urlencoded"
            },
            timeout=5
        )
        if response.status_code == 200:
            return response.json()
        else:
            print(f"API Error: {response.status_code} - {response.text}")
            return None
    except requests.exceptions.Timeout:
        print("API request timed out")
        return None
    except Exception as e:
        print(f"Inference error: {e}")
        return None

def draw_detections(frame, predictions):
    """Draw bounding boxes and labels on frame"""
    if not predictions or 'predictions' not in predictions:
        return frame

    for pred in predictions['predictions']:
        x = int(pred['x'] - pred['width'] / 2)
        y = int(pred['y'] - pred['height'] / 2)
        w = int(pred['width'])
        h = int(pred['height'])
        center_x = int(pred['x'])
        center_y = int(pred['y'])

        confidence = pred['confidence']
        label = pred.get('class', 'detection')

        if confidence >= CONFIDENCE_THRESHOLD:
            # Draw bounding box
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

            # Draw center point
            cv2.circle(frame, (center_x, center_y), 5, (0, 0, 255), -1)

            # Draw label with confidence
            label_text = f"{label}: {confidence:.2f}"
            label_size, _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)
            cv2.rectangle(frame, (x, y - label_size[1] - 10),
                         (x + label_size[0], y), (0, 255, 0), -1)
            cv2.putText(frame, label_text, (x, y - 5),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2)

    return frame

def check_line_crossing(predictions):
    """Check if objects crossed the virtual line and update count"""
    global tracked_objects, inside_count

    if not predictions or 'predictions' not in predictions:
        return

    current_frame_ids = set()

    for pred in predictions['predictions']:
        if pred['confidence'] < CONFIDENCE_THRESHOLD:
            continue

        # Use center of bounding box
        center_x = int(pred['x'])
        center_y = int(pred['y'])
        obj_class = pred.get('class', 'object')

        # Create unique ID based on position and class
        obj_id = f"{obj_class}_{center_y // 50}"  # Group by vertical region
        current_frame_ids.add(obj_id)

        # Check if object was tracked before
        if obj_id in tracked_objects:
            prev_x = tracked_objects[obj_id]

            # Check for crossing from left to right (ENTERING)
            if prev_x < LINE_POSITION - CROSSING_THRESHOLD and center_x > LINE_POSITION + CROSSING_THRESHOLD:
                inside_count += 1
                print(f">>> {obj_class} ENTERED | Inside count: {inside_count}")

            # Check for crossing from right to left (EXITING)
            elif prev_x > LINE_POSITION + CROSSING_THRESHOLD and center_x < LINE_POSITION - CROSSING_THRESHOLD:
                inside_count = max(0, inside_count - 1)
                print(f"<<< {obj_class} EXITED | Inside count: {inside_count}")

        # Update position
        tracked_objects[obj_id] = center_x

    # Clean up objects no longer detected (timeout after 2 seconds worth of frames)
    # Keep tracked_objects small by removing old entries
    ids_to_remove = [oid for oid in tracked_objects if oid not in current_frame_ids]
    for oid in ids_to_remove[:max(0, len(ids_to_remove) - 10)]:  # Keep last 10
        del tracked_objects[oid]

def draw_virtual_line(frame):
    """Draw the virtual detection line on frame"""
    height = frame.shape[0]

    # Draw vertical line
    cv2.line(frame, (LINE_POSITION, 0), (LINE_POSITION, height), (255, 0, 0), 3)

    # Draw arrows showing direction
    # Left arrow (OUT)
    cv2.arrowedLine(frame, (LINE_POSITION - 40, 50), (LINE_POSITION - 10, 50), (0, 0, 255), 2)
    cv2.putText(frame, "OUT", (LINE_POSITION - 80, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

    # Right arrow (IN)
    cv2.arrowedLine(frame, (LINE_POSITION + 10, 50), (LINE_POSITION + 40, 50), (0, 255, 0), 2)
    cv2.putText(frame, "IN", (LINE_POSITION + 50, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

    return frame

def send_to_server(count, avg_sound_level):
    try:
        response = requests.post(
            SERVER_URL,
            headers={"x-api-key": CAMERA_API_KEY, "Content-Type": "application/json"},
            json={"buildingId": "Example Location", "count": count, "soundLevel": avg_sound_level}
        )
        print(f"Posted to server: {response.status_code}")
    except Exception as e:
        print(f"Failed to post to server: {e}")

def periodic_send(interval):
    """Send the current inside_count to the server every `interval` seconds."""
    while True:
        current_time = time.time()
        end_time = current_time + interval
        db_readings = []
        while end_time > time.time():
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8').strip()
                try:
                    db_value = float(f'{binary_to_DB(line):.2f}')
                    db_readings.append(db_value)
                except ValueError:
                    pass
            time.sleep(0.1)
           
        send_to_server(inside_count, sum(db_readings) / max(len(db_readings), 1))
       
def main():
    """Update server every interval"""
    threading.Thread(target=periodic_send, args=(interval,), daemon=True).start()
    """Main loop for camera capture and inference"""
    print("Starting Raspberry Pi 5 CCTV Detection")
    print(f"Model: {API_URL}")
    print(f"Inference interval: {INFERENCE_INTERVAL}s")
    print("\nControls:")
    print("  Press 'N' - Switch to NIGHT mode")
    print("  Press 'D' - Switch to DAY mode")
    print("  Press 'Q' - Quit\n")

    # Initialize camera in day mode (normal colors)
    picam2 = setup_camera(mode="day")
    current_mode = "day"

    last_inference_time = 0
    latest_predictions = None
    frame_count = 0


    try:
        while True:
            # Capture frame from camera
            frame = picam2.capture_array()

            # Fix color channels for OV5647 - convert RGB to BGR for OpenCV
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            frame_count += 1

            # Run inference at specified intervals
            current_time = time.time()
            if current_time - last_inference_time >= INFERENCE_INTERVAL:
                print(f"Running inference on frame {frame_count}...")
                latest_predictions = run_inference(frame)
                if latest_predictions and 'predictions' in latest_predictions:
                    num_detections = len(latest_predictions['predictions'])
                    print(f"  Detected {num_detections} object(s)")
                    for i, pred in enumerate(latest_predictions['predictions'], 1):
                        conf = pred['confidence']
                        cls = pred.get('class', 'object')
                        print(f"    {i}. {cls} (confidence: {conf:.2f})")

                    # Check for line crossings
                    check_line_crossing(latest_predictions)

                last_inference_time = current_time

            # Draw virtual line
            frame = draw_virtual_line(frame)

            # Draw detections on frame
            if latest_predictions:
                frame = draw_detections(frame, latest_predictions)

            # Add frame counter and status
            fps_text = f"Frame: {frame_count} | Mode: {current_mode.upper()} | Inside: {inside_count}"
            cv2.putText(frame, fps_text, (10, 30),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

            status_text = "Analyzing..." if (current_time - last_inference_time < 0.1) else "Monitoring"
            cv2.putText(frame, status_text, (10, 60),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

            # Display frame if enabled
            if DISPLAY_WINDOW:
                cv2.imshow('CCTV Detection System', frame)

                # Check for keyboard commands
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q') or key == ord('Q'):
                    print("\nQuitting...")
                    break
                elif key == ord('n') or key == ord('N'):
                    current_mode = switch_camera_mode(picam2, "night")
                elif key == ord('d') or key == ord('D'):
                    current_mode = switch_camera_mode(picam2, "day")
            else:
                time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nInterrupted by user")

    finally:
        picam2.stop()
        if DISPLAY_WINDOW:
            cv2.destroyAllWindows()
        print("Camera stopped and resources cleaned up")

if __name__ == "__main__":
    main()