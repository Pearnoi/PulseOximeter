import asyncio
import numpy as np
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from bleak import BleakClient
from scipy.signal import butter, filtfilt, find_peaks, lfilter
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

# def compute_bpm(ir_f, ir_raw):
#     # 1. FINGER PRESENCE CHECK
#     if np.mean(ir_raw) < 10000: 
#         return 0, np.array([]), ir_f

#     # We need at least 2.5 seconds of data
#     if len(ir_f) < 250:
#         return 0, np.array([]), ir_f
    
#     # 2. AUTO-CORRELATION
#     # Remove the DC offset (mean) so we only correlate the pulsatile wave
#     signal = ir_f - np.mean(ir_f)
    
#     # Calculate auto-correlation
#     corr = np.correlate(signal, signal, mode='full')
#     corr = corr[len(corr)//2:] # Keep only the positive lags
    
#     # We only care about heart rates between 40 BPM and 200 BPM
#     # At 100Hz, 40 BPM = 1.5 seconds = 150 samples
#     # At 100Hz, 200 BPM = 0.3 seconds = 30 samples
#     min_lag = 30
#     max_lag = 150
    
#     if len(corr) < max_lag:
#         return 0, np.array([]), ir_f
        
#     # Find the peak in the auto-correlation within the valid BPM range
#     valid_corr = corr[min_lag:max_lag]
    
#     # Check if the signal is too weak/noisy
#     if np.max(valid_corr) < np.max(corr) * 0.1:
#          return 0, np.array([]), ir_f
         
#     peak_lag = np.argmax(valid_corr) + min_lag
    
#     # 3. CALCULATE BPM
#     bpm = 60.0 * 100 / peak_lag
    
#     # 4. Find peaks for the visual graph (using the calculated lag)
#     # We use the lag to set the distance between peaks
#     peaks, _ = find_peaks(ir_f, distance=int(peak_lag * 0.8), prominence=0.1)
    
#     if bpm < 40 or bpm > 200:
#         return 0, peaks, ir_f
        
#     return bpm, peaks, ir_f

def process_ppg(raw_ir, fs=100):
    """
    raw_ir: numpy array of raw IR samples
    fs: sample rate (100 Hz)
    """
    # 1. High-pass filter to remove baseline drift (matches Arduino HP_ALPHA=0.95)
    # This removes the slow wave you see in your screenshot
    b_hp, a_hp = [0.95, -0.95], [1, -0.95]
    hp = lfilter(b_hp, a_hp, raw_ir)

    # 2. Low-pass filter to smooth out electrical noise (matches Arduino LP_ALPHA=0.15)
    # This removes the jagged edges on the wave
    b_lp, a_lp = [0.15], [1, -0.85]
    clean_wave = lfilter(b_lp, a_lp, hp)

    # 3. Find peaks dynamically based on the wave's own amplitude
    # Use the last 3 seconds (300 samples) to avoid edge effects
    window = clean_wave[-300:]
    amp_range = np.max(window) - np.min(window)
    
    # If the wave is completely flat (no finger), return 0
    if amp_range < 0.05:
        return 0, np.array([]), clean_wave

    # Prominence = 20% of the peak-to-peak amplitude
    prominence = amp_range * 0.2
    
    peaks, _ = find_peaks(window, distance=50, prominence=prominence)
    
    # 4. Calculate BPM
    if len(peaks) < 3:
        bpm = 0
    else:
        # Use the last 4 peaks (3 intervals) for a stable reading
        recent_peaks = peaks[-4:] 
        intervals = np.diff(recent_peaks)
        
        # Use the median to ignore any single weird interval
        median_interval = np.median(intervals)
        bpm = 60.0 * fs / median_interval
        
        if bpm < 40 or bpm > 150:
            bpm = 0

    return bpm, peaks, clean_wave

# def compute_spo2(ir_raw, red_raw, ir_filtered, red_filtered):
#     """
#     Compute SpO2 using the standard Ratio-of-Ratios method.

#     R = (AC_red / DC_red) / (AC_ir / DC_ir)

#     AC = peak-to-peak amplitude of the pulsatile (filtered) signal
#     DC = mean of the raw (unfiltered) signal
#     """
#     if len(ir_raw) < 100 or len(red_raw) < 100:
#         return 0

#     if np.mean(ir_raw) < 10000:
#         return 0

#     # AC components: peak-to-peak of the filtered (pulsatile) signal
#     ac_ir  = np.max(ir_filtered)  - np.min(ir_filtered)
#     ac_red = np.max(red_filtered) - np.min(red_filtered)

#     # DC components: mean of the raw signal (baseline)
#     dc_ir  = np.mean(ir_raw)
#     dc_red = np.mean(red_raw)

#     # Guard against division by zero
#     if dc_ir <= 0 or dc_red <= 0 or ac_ir <= 0:
#         return 0

#     # Ratio of Ratios
#     R = (ac_red / dc_red) / (ac_ir / dc_ir)

#     # Empirical calibration curve (quadratic, fit to MAX30102 datasheet)
#     a, b, c = 1.5958422, -34.6596622, 112.6898759
#     spo2 = a * R * R + b * R + c

#     print(f"AC_IR: {ac_ir:.2f} | DC_IR: {dc_ir:.2f} | AC_RED: {ac_red:.2f} | DC_RED: {dc_red:.2f} | R: {R:.2f}")

#     # Clamp to physiologically plausible range
#     spo2 = max(70.0, min(100.0, spo2))
#     return spo2

def compute_spo2(ir_raw, red_raw, ir_filtered, red_filtered):
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

            # 1. Filter + MVA
            # ir_f  = apply_bandpass(double_moving_average(ir_raw))
            # red_f = apply_bandpass(double_moving_average(red_raw))
            
            # 2. Calculate BPM 
            bpm, peaks, ir_f = process_ppg(ir_raw, fs=100)

            b_hp, a_hp = [0.95, -0.95], [1, -0.95]
            b_lp, a_lp = [0.15], [1, -0.85]
            red_f = lfilter(b_lp, a_lp, lfilter(b_hp, a_hp, red_raw))

            # 3. Calculate SpO2
            if bpm == 0:
                spo2 = 0
            else:
                spo2 = compute_spo2(ir_raw, red_raw, ir_f, red_f)

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