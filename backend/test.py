import asyncio
import numpy as np
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from bleak import BleakClient
from scipy.signal import cheby2, butter, filtfilt, find_peaks, lfilter, sosfiltfilt, savgol_filter
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

# --- 1. Global State & Configuration ---
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
# Two separate paths so we get BOTH fast numbers and a good-looking waveform:
#   METRICS path (BPM / SpO2): causal lfilter, uses the newest sample -> "instant" readings
#   DISPLAY path (waveform):   zero-phase filtfilt + light Savitzky-Golay -> keeps the
#                              dicrotic notch / true pulse shape, at the cost of a small lag
B_BP, A_BP = cheby2(N=2, rs=40, Wn=[0.5, 12], btype='bandpass', fs=100)

# Display-path filter: same 4th-order Cheby II 0.5-12 Hz design, but as SOS (numerically
# safer) and run forward+backward (zero phase). 12 Hz keeps the dicrotic notch intact.
SOS_DISPLAY = cheby2(N=4, rs=40, Wn=[0.5, 12], btype='bandpass', fs=100, output='sos')
SG_WINDOW = 7          # 70 ms at 100 Hz (odd number). Bigger = smoother, flatter notch
SG_POLY = 3            # polynomial order. Higher = keeps more detail
DISPLAY_LAG = 25       # samples (250 ms): newest samples have filtfilt edge error, so we
                       # draw the wave this far behind "now". BPM/SpO2 are NOT delayed.
DISPLAY_POINTS = 200   # points sent to Flutter (2 s)

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
    # Remove the DC level first. Raw IR sits at ~50,000+, so without this the filter
    # starts from zero, sees a giant step, and rings at the start of every 5 s window.
    # That ringing was creating a fake peak and making BPM glitch low (~53) every few updates.
    filtered_bp = lfilter(B_BP, A_BP, raw_ir - np.mean(raw_ir))
    
    # B. 50ms Moving Average Filter (5 samples)
    clean_wave = np.convolve(filtered_bp, np.ones(3)/3, mode='same')

    # TODO C. Motion artifact correction with accelerometer 

    peaks, properties = find_peaks(
        clean_wave, 
        distance=50,       # Minimum 0.5s between beats (120 BPM max)
        prominence=0.05,    # Very sensitive, let the filter do the work
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

def process_display_wave(raw_ir, fs=100):
    """
    Waveform for the Flutter chart only (BPM/SpO2 do not use this).
    Returns the last DISPLAY_POINTS samples, ending DISPLAY_LAG samples before "now".
    """
    if len(raw_ir) < DISPLAY_POINTS + DISPLAY_LAG + 50:
        return 0, np.array([])

    # 1. Remove DC so the filter has no start-up transient
    x = raw_ir - np.mean(raw_ir)

    # 2. Zero-phase bandpass: forward + backward pass cancels phase delay,
    #    so the notch and upstroke stay at their true positions in time
    x = sosfiltfilt(SOS_DISPLAY, x)

    # 3. Light Savitzky-Golay smoothing (instead of the 3-point moving average).
    #    Fits a small polynomial per window, so it cleans noise without flattening peaks.
    # x = savgol_filter(x, SG_WINDOW, SG_POLY)

    # 4. Drop the unreliable newest edge, then take the display slice
    end = len(x) - DISPLAY_LAG
    start = max(0, end - DISPLAY_POINTS)
    clean_wave = x[start:end]

    peaks, _ = find_peaks(
        clean_wave, 
        distance=50,       # Minimum 0.5s between beats (120 BPM max)
        prominence=0.1,    # Very sensitive, let the filter do the work
        height=0           # Must be a positive peak
    )

    if len(peaks) < 3:
        return 0, clean_wave

    # D. Calculate BPM using median interval
    intervals = np.diff(peaks)
    bpm = 60.0 * fs / np.median(intervals)

    if bpm < 40 or bpm > 200:
        return 0, clean_wave

    return bpm, clean_wave

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
        if len(data_buffer) >= 250:
            ir_raw = np.array([x[0] for x in data_buffer])
            red_raw = np.array([x[1] for x in data_buffer])

            # 1. Get BPM and the display wave
            new_bpm, display_wave = process_display_wave(ir_raw, fs=100)
            now = time.time()

            # 2. ALWAYS update the wave, even if BPM is 0
            if len(display_wave) > 0:
                wave_to_send = display_wave.tolist()
            else:
                # If the filter failed, at least send the centered raw wave
                wave_to_send = (ir_raw - np.mean(ir_raw)).tolist()[-200:]

            # 3. Handle BPM Logic
            if new_bpm > 0:
                last_good_bpm = new_bpm
                bpm_to_send = new_bpm
                last_good_time = now
                last_good_spo2 = compute_spo2(ir_raw, red_raw)
                spo2_to_send = last_good_spo2
                quality = "good"
            else:
                if (now - last_good_time) < BPM_HOLD_TIMEOUT:
                    bpm_to_send = last_good_bpm
                    spo2_to_send = last_good_spo2
                    quality = "holding" 
                else:
                    # Timeout expired, but we STILL send the wave
                    bpm_to_send = 0
                    spo2_to_send = 0
                    quality = "disconnected"

            # 4. Broadcast to Flutter
            payload = {
                "miner_id": "MINER-01",
                "ts": now,
                "quality": quality,
                "bpm": bpm_to_send,
                "filtered_data": wave_to_send,  # <--- ALWAYS SENT
                "spo2": spo2_to_send
            }

            if ble_client and ble_client.is_connected and new_bpm > 0:
                # Format as text: "75,98\n"
                feedback_str = f"{int(new_bpm)},{int(spo2_to_send)}\n"
                try:
                    await ble_client.write_gatt_char(RX_UUID, feedback_str.encode('utf-8'))
                except Exception as e:
                    print(f"Feedback error: {e}")
            
            for ws in connected_websockets.copy():
                try:
                    await ws.send_json(payload)
                except:
                    connected_websockets.remove(ws)
                    
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