import asyncio
import numpy as np
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from bleak import BleakClient
from scipy.signal import cheby2, butter, filtfilt, find_peaks, lfilter
from collections import deque
import uvicorn
import time

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins (fine for development)
    allow_credentials=True,
    allow_methods=["*"],  # Allows POST, GET, etc.
    allow_headers=["*"],
)

# --- 1. Global State & Configuration ---
BLE_ADDRESS = "C5:91:ED:08:1D:7D"
CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

is_monitoring = False
ble_client = None
data_buffer = deque(maxlen=500) # Holds last 5 seconds of raw IR data
connected_websockets = set()

# --- 2. Signal Processing Functions ---
B_BP, A_BP = cheby2(N=4, rs=40, Wn=[0.5, 12], btype='bandpass', fs=100)

def double_moving_average(data, window1=5, window2=5):
    """Applies a double moving average to smooth the signal."""
    if len(data) < window1:
        return data
    # First pass
    ma1 = np.convolve(data, np.ones(window1)/window1, mode='same')
    # Second pass
    ma2 = np.convolve(ma1, np.ones(window2)/window2, mode='same')
    return ma2

def butter_bandpass(lowcut, highcut, fs, order=4):
    nyq = 0.5 * fs
    low = lowcut / nyq
    high = highcut / nyq
    b, a = butter(order, [low, high], btype='band')
    return b, a

def apply_bandpass(data, lowcut=0.5, highcut=4.0, fs=100):
    """Applies a bandpass filter to isolate heart rate frequencies."""
    if len(data) < 15: # Need enough data for the filter to work
        return data
    b, a = butter_bandpass(lowcut, highcut, fs)
    return filtfilt(b, a, data)

def process_ppg(raw_ir, fs=100):
    """
    raw_ir: numpy array of raw IR samples
    fs: sample rate (100 Hz)
    """
    if len(raw_ir) < 200: # Need 2 seconds
        return 0, np.array([]), (raw_ir - np.mean(raw_ir))

    # A. Bandpass Filter (0.5 - 12 Hz)
    # Use lfilter for real-time streaming (not filtfilt)
    filtered_bp = lfilter(B_BP, A_BP, raw_ir)
    
    # B. 50ms Moving Average Filter (5 samples)
    clean_wave = np.convolve(filtered_bp, np.ones(3)/3, mode='same')

    # TODO C. Motion artifact correction with accelerometer 

    peaks, properties = find_peaks(
        clean_wave, 
        distance=50,       # Minimum 0.5s between beats (120 BPM max)
        prominence=0.1,    # Very sensitive, let the filter do the work
        height=0           # Must be a positive peak
    )

    if len(peaks) < 3:
        return 0, np.array([]), clean_wave

    # D. Calculate BPM using median interval
    intervals = np.diff(peaks)
    bpm = 60.0 * fs / np.median(intervals)

    if bpm < 40 or bpm > 200:
        return 0, np.array([]), clean_wave

    return bpm, peaks, clean_wave

def compute_spo2(ir_raw, red_raw):
    """
    Matches the Arduino logic: DC = avg raw, AC = peak-to-peak of the last 2 seconds of raw
    """
    if len(ir_raw) < 200 or len(red_raw) < 200:
        return 0

    # 1. Use the last 2 seconds (200 samples) for calculation
    # This matches your Arduino SPO2_CALC_PERIOD = 2000
    ir_window = ir_raw[-200:]
    red_window = red_raw[-200:]

    # 2. DC components (average of the raw window)
    dc_ir  = np.mean(ir_window)
    dc_red = np.mean(red_window)

    # 3. AC components (peak-to-peak of the raw window, exactly like Arduino)
    ac_ir  = np.max(ir_window) - np.min(ir_window)
    ac_red = np.max(red_window) - np.min(red_window)

    # Guard against division by zero
    if dc_ir <= 0 or dc_red <= 0 or ac_ir <= 0:
        return 0

    # Ratio of Ratios
    R = (ac_red / dc_red) / (ac_ir / dc_ir)

    # Empirical calibration curve
    a, b, c = 1.5958422, -34.6596622, 112.6898759
    spo2 = a * R * R + b * R + c

    # Clamp
    spo2 = max(70.0, min(100.0, spo2))
    
    return spo2

def signal_quality(ir_raw, ir_f):
    """Rough contact/quality check. TUNE thresholds on the real sensor."""
    dc = float(np.mean(ir_raw))
    if dc < 20000:            # finger off / no contact -> IR level collapses
        return "disconnected"
    if dc > 0 and (np.max(ir_f) - np.min(ir_f)) / dc > 0.05:   # big swings = motion/sweat
        return "sweat"
    return "good"

# --- 3. The BLE Background Task ---
def ble_notification_handler(sender, data):
    """Triggered 100 times a second by the XIAO."""
    try:
        decoded = data.decode('utf-8').strip()
        parts = decoded.split(',')
        if len(parts) == 2:
            raw_ir = float(parts[0])
            raw_red = float(parts[1])
            # Add to our buffer
            data_buffer.append((raw_ir, raw_red))
    except Exception as e:
        print(f"Data parse error: {e}")

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
    global is_monitoring
    
    while is_monitoring:
        # Wait until we have enough data
        if len(data_buffer) >= 100:
            # Extract just the IR data for processing
            ir_raw = np.array([x[0] for x in data_buffer])
            red_raw = np.array([x[1] for x in data_buffer])

            # 2. Calculate BPM 
            bpm, peaks, ir_f = process_ppg(ir_raw, fs=100)

            # 3. Calculate SpO2
            if bpm == 0:
                spo2 = 0
            else:
                spo2 = compute_spo2(ir_raw, red_raw)

            # centered_ir = ir_raw - np.mean(ir_raw)

            # 4. Broadcast to all connected Flutter clients
            payload = {
                "miner_id": "MINER-01",
                "ts": time.time(),
                "quality": signal_quality(ir_raw, ir_f),
                "bpm": bpm,
                "filtered_data": ir_f.tolist()[-200:], # Send last 50 points
                "spo2": spo2
            }
            
            for ws in connected_websockets.copy():
                try:
                    await ws.send_json(payload)
                except:
                    connected_websockets.remove(ws)
                    
        await asyncio.sleep(0.1) # Process 10 times a second

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