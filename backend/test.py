import asyncio
import numpy as np
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from bleak import BleakClient
from scipy.signal import cheby2, butter, filtfilt, find_peaks, lfilter
from collections import deque
import uvicorn
import time
import struct

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins (fine for development)
    allow_credentials=True,
    allow_methods=["*"],  # Allows POST, GET, etc.
    allow_headers=["*"],
)

# --- 1. Global State & Configuration (DO NOT TOUCH) ---
BLE_ADDRESS = "C5:91:ED:08:1D:7D"
CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e" 

is_monitoring = False
ble_client = None
data_buffer = deque(maxlen=500) # Holds last 5 seconds of raw IR data
connected_websockets = set()

last_good_bpm = 0
last_good_spo2 = 0
last_good_time = 0.0
BPM_HOLD_TIMEOUT = 3.0

notification_count = 0
sample_count = 0

# --- 2. Signal Processing Functions ---
# 4th Order Butterworth bandpass filter (not too aggressive + filters out baseline distortion well)
B_BP, A_BP = butter(N=4, Wn=[0.8, 12], btype='bandpass', fs=100)
DISPLAY_POINTS = 200   # points sent to Flutter (2 s)

def process_display_wave(raw_ir, raw_red, fs=100):
    """
    raw_ir: numpy array of raw IR samples
    raw_red: numpy array of raw Red samples
    """
    if len(raw_ir) < 150: 
        return 0, (raw_ir - np.mean(raw_ir)), (raw_red - np.mean(raw_red))

    # 1. Remove DC and Filter IR 
    ir_f = lfilter(B_BP, A_BP, raw_ir - np.mean(raw_ir))

    # 2. Remove DC and Filter RED 
    red_f = lfilter(B_BP, A_BP, raw_red - np.mean(raw_red))

    # 3. Find peaks on the IR signal
    peaks, _ = find_peaks(
        ir_f, 
        distance=50,       
        prominence=0.05,    
        height=0           
    )

    if len(peaks) < 3:
        return 0, ir_f, red_f

    # 4. Calculate BPM
    intervals = np.diff(peaks)
    bpm = 60.0 * fs / np.median(intervals)

    if bpm < 40 or bpm > 200:
        return 0, ir_f, red_f

    # Return BPM, Peaks, Clean IR, Clean Red
    return bpm, ir_f, red_f

def lms_motion_rejection(ppg_ir, ppg_red, accel_signal, filter_length=5):
    """
    Stable LMS Adaptive Filter applied to both IR and Red signals.
    """
    n_samples = len(ppg_ir)
    
    # 1. Remove DC offset from accelerometer
    accel_signal = accel_signal - np.mean(accel_signal)
    
    # 2. Normalize signals to prevent overflow
    ir_std = np.std(ppg_ir) + 1e-6
    red_std = np.std(ppg_red) + 1e-6
    accel_std = np.std(accel_signal) + 1e-6
    
    ir_norm = ppg_ir / ir_std
    red_norm = ppg_red / red_std
    accel_norm = accel_signal / accel_std
    
    # 3. Calculate a stable mu based on signal power
    power = np.mean(accel_norm**2) + 1e-6
    mu = 0.01 / (filter_length * power) 
    
    w = np.zeros(filter_length)
    clean_ir = np.zeros(n_samples)
    clean_red = np.zeros(n_samples)
    
    accel_padded = np.pad(accel_norm, (filter_length, 0), mode='edge')
    
    for i in range(n_samples):
        x = accel_padded[i : i + filter_length]
        
        # Estimate noise from accel
        est_noise = np.dot(w, x)
        
        # Subtract from both channels
        clean_ir[i] = ir_norm[i] - est_noise
        clean_red[i] = red_norm[i] - est_noise
        
        # Guard against NaN/Infinity
        if np.isnan(clean_ir[i]) or np.isinf(clean_ir[i]):
            clean_ir[i] = 0
            clean_red[i] = 0
            continue
            
        # Update weights based on the IR error (the main signal)
        error = clean_ir[i]
        w = w + 2 * mu * error * x
        
        # Hard clamp on weights to absolutely prevent flipping
        w = np.clip(w, -2.0, 2.0)
        
    # De-normalize back to original scale
    return clean_ir * ir_std, clean_red * red_std

def compute_spo2(ir_raw, red_raw, ir_f, red_f):
    """
    ir_raw, red_raw: Unfiltered signals (for DC)
    ir_f, red_f: Filtered signals (for AC)
    """
    if len(ir_raw) < 200:
        return 0

    # 1. DC COMPONENTS: Use the RAW signal
    # We use the mean of the raw signal to capture the static tissue absorption.
    dc_ir  = np.mean(ir_raw[-200:])
    dc_red = np.mean(red_raw[-200:])

    # 2. AC COMPONENTS: Use the FILTERED signal
    # The filtered signal is centered around 0, so max-min gives the true heartbeat amplitude.
    ac_ir  = np.max(ir_f[-200:]) - np.min(ir_f[-200:])
    ac_red = np.max(red_f[-200:]) - np.min(red_f[-200:])

    # Guard against division by zero
    if dc_ir <= 0 or dc_red <= 0 or ac_ir <= 0:
        return 0

    # 3. Ratio of Ratios
    R = (ac_red / dc_red) / (ac_ir / dc_ir)

    # 4. Empirical Calibration
    a, b, c = 1.5958422, -34.6596622, 112.6898759
    spo2 = a * R * R + b * R + c

    # Clamp to realistic values
    return max(70.0, min(100.0, spo2))

def signal_quality(ir_raw, ir_f):
    """Rough contact/quality check. TUNE thresholds on the real sensor."""
    dc = float(np.mean(ir_raw))
    if dc < 20000:            # finger off / no contact -> IR level collapses
        return "disconnected"
    if dc > 0 and ir_f is not None and len(ir_f) > 0 and (np.max(ir_f) - np.min(ir_f)) / dc > 0.05:
        return "sweat"
    return "good"

# --- 3. The BLE Background Task ---
def ble_notification_handler(sender, data):
    global notification_count, sample_count
    try:
        notification_count += 1

        # We expect exactly 14 bytes
        if len(data) != 14:
            return

        # Unpack the 14 bytes:
        # < = Little Endian
        # l = long (4 bytes) for IR
        # l = long (4 bytes) for Red
        # h = short (2 bytes) for X
        # h = short (2 bytes) for Y
        # h = short (2 bytes) for Z
        raw_ir, raw_red, accel_x, accel_y, accel_z = struct.unpack('<llhhh', data)

        # Convert to float for the buffer
        corrected_ir = float(raw_ir)
        corrected_red = float(raw_red)

        data_buffer.append((
            raw_ir,
            raw_red,
            accel_x,
            accel_y,
            accel_z,
            corrected_ir,
            corrected_red
        ))

        sample_count += 1

        if sample_count % 100 == 0:
            print(
                f"[SENSOR DATA #{sample_count}] "
                f"IR={raw_ir} | Red={raw_red} | "
                f"X={accel_x} | Y={accel_y} | Z={accel_z}"
            )

    except Exception as e:
        print(f"[DATA PARSE ERROR] {e}")

async def ble_background_loop():
    """Runs in the background, managing the BLE connection."""
    global ble_client, is_monitoring
    ble_client = BleakClient(BLE_ADDRESS)
    
    try:
        await ble_client.connect()
        print("Connected to XIAO via BLE")
        await ble_client.start_notify(CHAR_UUID, ble_notification_handler)
        
        while is_monitoring and ble_client.is_connected:
            await asyncio.sleep(0.1) # Just keep the loop alive
            
    except Exception as e:
        print(f"BLE Error: {e}")
    finally:
        if ble_client and ble_client.is_connected:
            await ble_client.disconnect()
        print("BLE Disconnected")

# --- 4. The Processing & WebSocket Broadcast Task ---
async def processing_loop():
    """Runs in the background, processing data and sending to Flutter."""
    global is_monitoring, last_good_bpm, last_good_spo2, last_good_time, ble_client

    while is_monitoring:
        if len(data_buffer) >= 50:
            ir_raw = np.array([x[0] for x in data_buffer])
            red_raw = np.array([x[1] for x in data_buffer])
            now = time.time()
            new_bpm = 0
            wave_to_send = []

            if signal_quality(ir_raw, np.array([])) == "disconnected":
                wave_to_send = []
                bpm_to_send = 0
                spo2_to_send = 0
                last_good_bpm = 0
                last_good_spo2 = 0
                last_good_time = 0.0
                quality = "disconnected"
            else:
                ax = np.array([x[2] for x in data_buffer])
                ay = np.array([x[3] for x in data_buffer])
                az = np.array([x[4] for x in data_buffer])

                accel_mag = np.sqrt(ax**2 + ay**2 + az**2)
                ir_clean, red_clean = lms_motion_rejection(ir_raw, red_raw, accel_mag, filter_length=5)

                new_bpm, ir_f, red_f = process_display_wave(ir_clean, red_clean, fs=100)

                if len(ir_f) > 0:
                    wave_to_send = ir_f.tolist()
                else:
                    wave_to_send = (ir_raw - np.mean(ir_raw)).tolist()[-200:]

                if new_bpm > 0:
                    last_good_bpm = new_bpm
                    bpm_to_send = new_bpm
                    last_good_time = now
                    last_good_spo2 = compute_spo2(ir_clean, red_clean, ir_f, red_f)
                    spo2_to_send = last_good_spo2
                    quality = "good"
                else:
                    if last_good_time > 0 and (now - last_good_time) < BPM_HOLD_TIMEOUT:
                        bpm_to_send = last_good_bpm
                        spo2_to_send = last_good_spo2
                        quality = "holding"
                    else:
                        bpm_to_send = 0
                        spo2_to_send = 0
                        quality = "disconnected"

            payload = {
                "miner_id": "MINER-01",
                "ts": now,
                "quality": quality,
                "bpm": bpm_to_send,
                "filtered_data": wave_to_send,
                "spo2": spo2_to_send
            }

            if ble_client and ble_client.is_connected and new_bpm > 0:
                feedback_str = f"{int(new_bpm)},{int(spo2_to_send)}\n"
                try:
                    await ble_client.write_gatt_char(RX_UUID, feedback_str.encode('utf-8'))
                except Exception as e:
                    print(f"Feedback error: {e}")

            for ws in connected_websockets.copy():
                try:
                    await ws.send_json(payload)
                except Exception:
                    connected_websockets.discard(ws)

        await asyncio.sleep(0.1)

# --- 5. FastAPI Routes ---

@app.post("/start")
async def start_monitoring():
    global is_monitoring
    if is_monitoring:
        return {"status": "already running"}
        
    is_monitoring = True
    # Launch both background tasks
    asyncio.create_task(ble_background_loop())
    asyncio.create_task(processing_loop())
    return {"status": "started"}

@app.post("/stop")
async def stop_monitoring():
    global is_monitoring
    is_monitoring = False
    return {"status": "stopping"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_websockets.add(websocket)
    print("Flutter connected to WebSocket")
    
    try:
        while True:
            # Keep the connection open
            await websocket.receive_text()
    except:
        connected_websockets.remove(websocket)
        print("Flutter disconnected")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)