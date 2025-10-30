#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import time
from hashlib import sha1
from pathlib import Path
from typing import Dict, List, Optional

import anyio
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ------------------ CONFIG ------------------
ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

DATA_DIR = Path(os.getenv("RECORD_PLAYER_DATA_DIR", str(ROOT / "data"))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DEFAULTS_FILE = DATA_DIR / "default_outputs.json"

OWNTONE_ENDPOINT = os.getenv("OWNTONE_ENDPOINT", "http://127.0.0.1:3689/api")

CORE_SERVICE = "owntone.service"
PIPE_SERVICE = "owntone-record_player-input.service"

POLL_SEC = 1.5
WAIT_ACTIVE_TIMEOUT = 25
WAIT_OUTPUTS_TIMEOUT = 20
# -------------------------------------------

app = FastAPI()

# ========== Static ==========
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")

# ========== SSE ==========
_subscribers: List[asyncio.Queue] = []
_sub_lock = asyncio.Lock()

async def sse_subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    async with _sub_lock:
        _subscribers.append(q)
    return q

async def sse_unsubscribe(q: asyncio.Queue):
    async with _sub_lock:
        if q in _subscribers:
            _subscribers.remove(q)

async def sse_broadcast(payload: dict):
    async with _sub_lock:
        qs = list(_subscribers)
    for q in qs:
        try:
            q.put_nowait(payload)
        except Exception:
            await sse_unsubscribe(q)

def _sse_event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"

@app.get("/api/events")
async def events(request: Request):
    q = await sse_subscribe()
    try:
        core = await _is_active_async(CORE_SERVICE)
        pipe = await _is_active_async(PIPE_SERVICE)
        await q.put({"type": "status", "core_active": core, "pipe_active": pipe, "both_active": core and pipe})
        if core and pipe:
            outs = await _list_outputs_raw()
            defaults = _read_defaults_map()
            for o in outs:
                oid = str(int(o.get("id")))
                o["default"] = oid in defaults
                o["default_volume"] = defaults.get(oid)
            await q.put({"type": "outputs", "outputs": outs})
    except Exception:
        pass

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                msg = await q.get()
                yield _sse_event(msg)
        finally:
            await sse_unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")

# ========== Defaults persistence ==========
def _read_defaults_map() -> Dict[str, int]:
    """
    Returns dict of {"<output_id>": default_volume_int}
    Supports migration from legacy list format.
    """
    if DEFAULTS_FILE.exists():
        try:
            data = json.loads(DEFAULTS_FILE.read_text())
            if isinstance(data, dict):
                # Validate values
                out = {}
                for k, v in data.items():
                    try:
                        out[str(int(k))] = max(0, min(100, int(v)))
                    except Exception:
                        pass
                return out
            if isinstance(data, list):
                # legacy: list of ids -> default vol 50
                return {str(int(x)): 50 for x in data}
        except Exception:
            pass
    return {}

def _write_defaults_map(m: Dict[str, int]) -> None:
    # Clean + clamp
    cleaned = {str(int(k)): max(0, min(100, int(v))) for k, v in m.items()}
    DEFAULTS_FILE.write_text(json.dumps(cleaned, indent=2, sort_keys=True), encoding="utf-8")

# ========== Systemd (async via thread) ==========
async def _run_systemctl(*args: str) -> subprocess.CompletedProcess:
    def _run():
        return subprocess.run(
            ["/bin/systemctl", *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False
        )
    return await anyio.to_thread.run_sync(_run)

async def _is_active_async(unit: str) -> bool:
    p = await _run_systemctl("is-active", unit)
    return p.returncode == 0 and p.stdout.strip() == "active"

async def _wait_active_async(unit: str, timeout_s: int) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await _is_active_async(unit):
            return True
        await asyncio.sleep(0.5)
    return False

# ========== Owntone HTTP (async) ==========
def _http() -> httpx.AsyncClient:
    return app.state.http

async def _owntone_get(path: str) -> Dict:
    r = await _http().get(path)
    r.raise_for_status()
    return r.json()

async def _owntone_put(path: str, payload: Dict) -> Dict:
    r = await _http().put(path, json=payload)
    r.raise_for_status()
    return r.json() if r.headers.get("content-length") not in (None, "0") else {}

async def _owntone_post(path: str, payload: Optional[Dict] = None) -> Dict:
    r = await _http().post(path, json=payload or {})
    r.raise_for_status()
    return r.json() if r.headers.get("content-length") not in (None, "0") else {}

async def _list_outputs_raw() -> List[Dict]:
    data = await _owntone_get("/outputs")
    return data.get("outputs", []) if isinstance(data, dict) else []

def _outputs_fp(outs: List[Dict]) -> str:
    minimal = [
        {
            "id": int(o.get("id")),
            "selected": bool(o.get("selected", False)),
            "volume": int(o.get("volume", 0)),
            "name": str(o.get("name", "")),
        }
        for o in sorted(outs, key=lambda x: int(x.get("id", 0)))
    ]
    return sha1(json.dumps(minimal, sort_keys=True).encode()).hexdigest()

# ========== API: status / outputs ==========
@app.get("/api/status")
async def status():
    core = await _is_active_async(CORE_SERVICE)
    pipe = await _is_active_async(PIPE_SERVICE)
    return {"core_active": core, "pipe_active": pipe, "both_active": core and pipe}

@app.get("/api/outputs")
async def outputs():
    defaults = _read_defaults_map()
    outs = await _list_outputs_raw()
    for o in outs:
        oid = str(int(o.get("id")))
        o.setdefault("volume", 0)
        o.setdefault("selected", False)
        o["default"] = oid in defaults
        o["default_volume"] = defaults.get(oid)
    return {"outputs": outs}

@app.put("/api/outputs/{out_id}")
async def update_output(out_id: int, body: Dict):
    oid = str(int(out_id))
    defaults = _read_defaults_map()

    # default flag / default_volume persistence
    touch_defaults = False
    if "default" in body:
        if body["default"]:
            # becoming a default: choose a volume
            if "default_volume" in body:
                defaults[oid] = max(0, min(100, int(body["default_volume"])))
            else:
                # if current output volume known, use it; else 50
                try:
                    outs = await _list_outputs_raw()
                    cur = next((o for o in outs if str(int(o.get("id"))) == oid), None)
                    defaults[oid] = max(0, min(100, int(cur.get("volume", 50)))) if cur else 50
                except Exception:
                    defaults[oid] = 50
        else:
            defaults.pop(oid, None)
        touch_defaults = True

    if "default_volume" in body:
        # Allow setting default volume directly (creates default entry if missing)
        defaults[oid] = max(0, min(100, int(body["default_volume"])))
        touch_defaults = True

    if touch_defaults:
        _write_defaults_map(defaults)

    # forward live selection/volume to Owntone if present
    if "selected" in body or "volume" in body:
        try:
            if "selected" in body:
                await _owntone_put(f"/outputs/{out_id}", {"selected": bool(body["selected"])})
            if "volume" in body:
                v = int(body["volume"])
                await _owntone_put(f"/outputs/{out_id}", {"volume": max(0, min(100, v))})
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Owntone update failed: {e}")

    # push updated outputs (with default flags/volumes)
    try:
        outs = await _list_outputs_raw()
        defs = _read_defaults_map()
        for o in outs:
            k = str(int(o.get("id")))
            o["default"] = k in defs
            o["default_volume"] = defs.get(k)
        await sse_broadcast({"type": "outputs", "outputs": outs})
    except Exception:
        pass

    return {"ok": True}

# ========== API: defaults (optional helpers) ==========
@app.get("/api/defaults")
async def get_defaults():
    return {"defaults": _read_defaults_map()}  # {"id": volume}

@app.put("/api/defaults")
async def set_defaults(body: Dict):
    # expects {"defaults": {"1": 65, "7": 30}}
    defaults = body.get("defaults")
    if not isinstance(defaults, dict):
        raise HTTPException(status_code=400, detail="Expected body { defaults: {id: volume, ...} }")
    _write_defaults_map(defaults)

    try:
        outs = await _list_outputs_raw()
        defs = _read_defaults_map()
        for o in outs:
            k = str(int(o.get("id")))
            o["default"] = k in defs
            o["default_volume"] = defs.get(k)
        await sse_broadcast({"type": "outputs", "outputs": outs})
    except Exception:
        pass

    return {"ok": True}

# ========== API: start / stop ==========
@app.post("/api/start")
async def start():
    await _run_systemctl("start", CORE_SERVICE)
    if not await _wait_active_async(CORE_SERVICE, WAIT_ACTIVE_TIMEOUT):
        await sse_broadcast({"type": "status", "core_active": False, "pipe_active": await _is_active_async(PIPE_SERVICE), "both_active": False})
        return JSONResponse({"ok": False, "error": "owntone.service failed to start"}, status_code=500)

    await _run_systemctl("start", PIPE_SERVICE)
    if not await _wait_active_async(PIPE_SERVICE, WAIT_ACTIVE_TIMEOUT):
        await _run_systemctl("stop", CORE_SERVICE)
        await sse_broadcast({"type": "status", "core_active": False, "pipe_active": False, "both_active": False})
        return JSONResponse({"ok": False, "error": "pipe service failed to start"}, status_code=500)

    # wait for outputs
    outs: List[Dict] = []
    deadline = time.monotonic() + WAIT_OUTPUTS_TIMEOUT
    while time.monotonic() < deadline:
        try:
            outs = await _list_outputs_raw()
            if outs:
                break
        except Exception:
            pass
        await asyncio.sleep(0.5)

    if not outs:
        await _run_systemctl("stop", CORE_SERVICE)
        await sse_broadcast({"type": "status", "core_active": False, "pipe_active": False, "both_active": False})
        return JSONResponse({"ok": False, "error": "no outputs discovered"}, status_code=500)

    # enable defaults: set volume first, then select
    defs = _read_defaults_map()
    try:
        for o in outs:
            k = str(int(o.get("id")))
            if k in defs:
                dv = max(0, min(100, int(defs[k])))
                await _owntone_put(f"/outputs/{k}", {"volume": dv})
                await _owntone_put(f"/outputs/{k}", {"selected": True})
    except Exception as e:
        await _run_systemctl("stop", CORE_SERVICE)
        await sse_broadcast({"type": "status", "core_active": False, "pipe_active": False, "both_active": False})
        return JSONResponse({"ok": False, "error": f"failed to enable defaults: {e}"}, status_code=500)

    # broadcast fresh state
    try:
        for o in outs:
            k = str(int(o.get("id")))
            o["default"] = k in defs
            o["default_volume"] = defs.get(k)
        await sse_broadcast({"type": "status", "core_active": True, "pipe_active": True, "both_active": True})
        await sse_broadcast({"type": "outputs", "outputs": outs})
    except Exception:
        pass

    return {"ok": True}

@app.post("/api/stop")
async def stop():
    await _run_systemctl("stop", CORE_SERVICE)
    await asyncio.sleep(0.5)
    core = await _is_active_async(CORE_SERVICE)
    pipe = await _is_active_async(PIPE_SERVICE)
    await sse_broadcast({"type": "status", "core_active": core, "pipe_active": pipe, "both_active": core and pipe})
    if not (core and pipe):
        await sse_broadcast({"type": "outputs", "outputs": []})
    return {"ok": True, "core_active": core, "pipe_active": pipe}

# ========== Watcher ==========
def _attach_defaults(outs: List[Dict]) -> List[Dict]:
    defs = _read_defaults_map()
    for o in outs:
        k = str(int(o.get("id")))
        o["default"] = k in defs
        o["default_volume"] = defs.get(k)
    return outs

async def _watch_loop():
    prev_status = None
    prev_fp = None
    while True:
        try:
            core = await _is_active_async(CORE_SERVICE)
            pipe = await _is_active_async(PIPE_SERVICE)
            status_now = {"core_active": core, "pipe_active": pipe, "both_active": core and pipe}
            if status_now != prev_status:
                prev_status = status_now
                await sse_broadcast({"type": "status", **status_now})

            if status_now["both_active"]:
                outs = _attach_defaults(await _list_outputs_raw())
                fp = _outputs_fp(outs)
                if fp != prev_fp:
                    prev_fp = fp
                    await sse_broadcast({"type": "outputs", "outputs": outs})
            else:
                if prev_fp is not None:
                    prev_fp = None
                    await sse_broadcast({"type": "outputs", "outputs": []})
        except Exception:
            pass
        await asyncio.sleep(POLL_SEC)

@app.on_event("startup")
async def _startup():
    app.state.http = httpx.AsyncClient(base_url=OWNTONE_ENDPOINT, timeout=5.0)
    app.state.watch_task = asyncio.create_task(_watch_loop())

@app.on_event("shutdown")
async def _shutdown():
    task: asyncio.Task = getattr(app.state, "watch_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    client: httpx.AsyncClient = getattr(app.state, "http", None)
    if client:
        await client.aclose()
