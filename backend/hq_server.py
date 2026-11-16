"""
HQ server (port 8001).
- Receives readings from miner phones (POST /readings)
- Runs 11 fake miners so the control room looks full
- Computes derived metrics (fatigue, trend, hypoxia, status), ambient gas per zone, alerts
- Pushes a snapshot to the dashboard every second on WS /hq
- Simulation endpoints for the demo drawer
Run:  python hq_server.py
"""
import asyncio, random, time
from collections import deque
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ZONES = ["Shaft Entry", "Level 2 Haulage", "Stope A", "Refuge Chamber"]
NORMAL = {"o2": 20.9, "co": 2.0, "ch4": 0.1}
ambient = {z: dict(NORMAL) for z in ZONES}
ambient_target = {z: dict(NORMAL) for z in ZONES}
zone_alarm = set()

FAKE_ZONES = {2: "Shaft Entry", 3: "Stope A", 4: "Stope A", 5: "Level 2 Haulage", 6: "Stope A",
              7: "Level 2 Haulage", 8: "Shaft Entry", 9: "Refuge Chamber", 10: "Level 2 Haulage",
              11: "Shaft Entry", 12: "Stope A"}
ESC = {"green": 0, "grey": 1, "amber": 1, "red": 2}

miners, alerts, commands, clients = {}, [], {}, set()
alert_seq = 0


def new_miner(mid, zone, real=False):
    base = random.uniform(65, 85)
    miners[mid] = dict(id=mid, name=mid.replace("-", " ").title(), zone=zone, real=real,
                       bpm=base if not real else 0.0, spo2=97.0 if not real else 0.0, quality="good",
                       last_seen=0.0 if real else time.time(), hist=deque(maxlen=900), archive=deque(maxlen=20000),
                       fatigue=0.0, status="green", prev_status="green", trend="flat", hypoxia=0.0,
                       cause=None, base_hr=base, mode=None)


new_miner("MINER-01", "Level 2 Haulage", real=True)
for i, z in FAKE_ZONES.items():
    new_miner(f"MINER-{i:02d}", z)


def add_alert(severity, message, miner_id=None, zone=None, cause=None):
    global alert_seq
    alert_seq += 1
    alerts.insert(0, dict(id=alert_seq, ts=time.time(), severity=severity, miner_id=miner_id,
                          zone=zone, message=message, cause=cause, ack=False, action=None))
    del alerts[60:]


# ---------- derived metrics ----------
def derive(m):
    now = time.time()
    stale = now - m["last_seen"] > 10
    bpm, spo2 = m["bpm"], m["spo2"]
    amb = ambient[m["zone"]]
    env = amb["o2"] < 19.5 or amb["co"] > 35 or amb["ch4"] > 1.0

    # Exertion / fatigue index (demo-speed: ~45 s above 145 BPM = 100%)
    if bpm > 145:
        m["fatigue"] = min(100.0, m["fatigue"] + 100 / 45)
    else:
        m["fatigue"] = max(0.0, m["fatigue"] - 0.3)

    # Hypoxia score: SpO2 low AND HR raised
    hyp = 0.0
    if 0 < spo2 < 94:
        hyp = min(100.0, (94 - spo2) * 12 + (30 if bpm > 100 else 0))
    m["hypoxia"] = hyp

    # Trend vs last 5 minutes
    h = [p[1] for p in m["hist"] if p[0] > now - 300]
    m["trend"] = "flat"
    if len(h) >= 10:
        d = sum(h[-5:]) / 5 - sum(h) / len(h)
        m["trend"] = "up" if d > 5 else "down" if d < -5 else "flat"

    if stale or m["quality"] == "disconnected":
        m["status"] = "grey"
    elif (0 < spo2 < 90) or bpm > 170 or hyp >= 70:
        m["status"] = "red"
    elif (0 < spo2 < 94) or bpm > 145 or m["fatigue"] >= 60 or env:
        m["status"] = "amber"
    else:
        m["status"] = "green"

    # Is it the environment or the person?
    m["cause"] = None
    if m["status"] in ("red", "amber"):
        if env:
            m["cause"] = "environmental"
        elif hyp > 0:
            m["cause"] = "medical"
        elif bpm > 145:
            m["cause"] = "exertion"

    if m["status"] != m["prev_status"]:
        if ESC[m["status"]] > ESC[m["prev_status"]] or (m["status"] == "grey"):
            sev = "critical" if m["status"] == "red" else "warning"
            already = any(a["miner_id"] == m["id"] and a["severity"] == sev and not a["ack"] for a in alerts)
            if not already:
                if m["status"] == "grey":
                    add_alert("warning", f"{m['name']}: signal lost. Last known zone: {m['zone']}",
                              m["id"], m["zone"], "signal")
                else:
                    add_alert(sev, f"{m['name']} in {m['zone']}: HR {bpm:.0f}, SpO2 {spo2:.0f}%",
                              m["id"], m["zone"], m["cause"])
        m["prev_status"] = m["status"]


def tick():
    # ambient drifts toward target
    for z in ZONES:
        for k in NORMAL:
            ambient[z][k] += (ambient_target[z][k] - ambient[z][k]) * 0.1
        a = ambient[z]
        danger = a["o2"] < 19.5 or a["co"] > 35 or a["ch4"] > 1.0
        if danger and z not in zone_alarm:
            zone_alarm.add(z)
            add_alert("critical", f"{z}: O2 {a['o2']:.1f}%, CO {a['co']:.0f} ppm. Evacuate zone.",
                      None, z, "environmental")
        elif not danger:
            zone_alarm.discard(z)

    for m in miners.values():
        if not m["real"] and m["mode"] != "deadzone":
            env = ambient[m["zone"]]["o2"] < 19.5
            hr_t = 165 if m["mode"] == "overexert" else (110 if env else m["base_hr"])
            sp_t = 89 if env else 97
            m["bpm"] += (hr_t - m["bpm"]) * 0.15 + random.gauss(0, 1.2)
            m["spo2"] += (sp_t - m["spo2"]) * 0.10 + random.gauss(0, 0.2)
            m["spo2"] = min(100.0, m["spo2"])
            m["last_seen"] = time.time()
        derive(m)
        if time.time() - m["last_seen"] <= 10 and m["bpm"] > 0:
            m["hist"].append((time.time(), m["bpm"], m["spo2"]))


def snapshot():
    out = []
    for m in miners.values():
        out.append(dict(id=m["id"], name=m["name"], zone=m["zone"], real=m["real"],
                        bpm=round(m["bpm"], 1), spo2=round(m["spo2"], 1), quality=m["quality"],
                        status=m["status"], fatigue=round(m["fatigue"], 1), trend=m["trend"],
                        hypoxia=round(m["hypoxia"], 1), cause=m["cause"],
                        spark=[round(p[1], 1) for p in list(m["hist"])[-60:]]))
    live = [m for m in out if m["status"] != "grey" and m["bpm"] > 0]
    kpi = dict(total=len(out), accounted=len([m for m in out if m["status"] != "grey"]),
               critical=len([a for a in alerts if a["severity"] == "critical" and not a["ack"]]),
               warnings=len([a for a in alerts if a["severity"] == "warning" and not a["ack"]]),
               avg_hr=round(sum(m["bpm"] for m in live) / len(live), 1) if live else 0,
               min_spo2=round(min((m["spo2"] for m in live if m["spo2"] > 0), default=0), 1))
    return dict(miners=out, ambient={z: {k: round(v, 2) for k, v in a.items()} for z, a in ambient.items()},
                alerts=alerts[:30], kpi=kpi, ts=time.time())


async def broadcast_loop():
    while True:
        tick()
        snap = snapshot()
        for ws in list(clients):
            try:
                await ws.send_json(snap)
            except Exception:
                clients.discard(ws)
        await asyncio.sleep(1)


@app.on_event("startup")
async def _start():
    asyncio.create_task(broadcast_loop())


# ---------- phone -> HQ ----------
class Reading(BaseModel):
    miner_id: str
    zone: Optional[str] = None
    bpm: float
    spo2: float
    quality: str = "good"
    ts: float


@app.post("/readings")
async def readings(batch: List[Reading]):
    """Accepts a batch (live reading or offline backlog)."""
    if not batch:
        return {"ok": True}
    for r in batch:
        if r.miner_id not in miners:
            new_miner(r.miner_id, r.zone or ZONES[0], real=True)
        miners[r.miner_id]["archive"].append(r.dict())
    r = max(batch, key=lambda x: x.ts)
    m = miners[r.miner_id]
    if r.zone in ambient:
        m["zone"] = r.zone
    if r.bpm > 0:
        m["bpm"] = r.bpm
    if r.spo2 > 0:
        m["spo2"] = r.spo2
    m["quality"] = r.quality
    m["last_seen"] = time.time()
    return {"ok": True, "received": len(batch)}


@app.get("/commands/{miner_id}")
async def get_commands(miner_id: str):
    """Phone polls this; returns and clears pending HQ commands."""
    return {"commands": commands.pop(miner_id, [])}


# ---------- dashboard -> HQ ----------
class Action(BaseModel):
    action: str  # "dispatch" | "haptic"


@app.post("/alerts/{alert_id}/ack")
async def ack(alert_id: int):
    for a in alerts:
        if a["id"] == alert_id:
            a["ack"] = True
    return {"ok": True}


@app.post("/alerts/{alert_id}/action")
async def act(alert_id: int, body: Action):
    for a in alerts:
        if a["id"] == alert_id:
            a["ack"] = True
            a["action"] = "Safety lead dispatched" if body.action == "dispatch" else "Haptic buzz sent"
            if a["miner_id"]:
                commands.setdefault(a["miner_id"], []).append(body.action)
    return {"ok": True}


@app.post("/sim/{scenario}")
async def sim(scenario: str):
    if scenario == "gas_leak":
        ambient_target["Stope A"] = {"o2": 17.0, "co": 55.0, "ch4": 1.8}
    elif scenario == "overexert":
        miners["MINER-03"]["mode"] = "overexert"
    elif scenario == "deadzone":
        miners["MINER-05"]["mode"] = "deadzone"
    elif scenario == "reset":
        for z in ZONES:
            ambient_target[z] = dict(NORMAL)
        for m in miners.values():
            m["mode"] = None
            m["fatigue"] = 0.0
        alerts.clear()
    return {"ok": True, "scenario": scenario}


@app.websocket("/hq")
async def hq_ws(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        await ws.send_json(snapshot())
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        clients.discard(ws)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
