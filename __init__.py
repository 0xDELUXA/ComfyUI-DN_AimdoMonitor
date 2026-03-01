import torch
import threading
import time
import atexit
import server
from aiohttp import web

try:
    from comfy_aimdo import control
    AIMDO_AVAILABLE = control.lib is not None
except Exception as e:
    AIMDO_AVAILABLE = False
    print(f"[aimdo_monitor] could not import comfy_aimdo: {e}")

MB = 1024 * 1024

def _fmt(n):
    return f"{n // MB:>7} MB"

def _snapshot(label="poll"):
    if not AIMDO_AVAILABLE:
        print("[aimdo_monitor] aimdo not available")
        return

    try:
        aimdo_usage = control.get_total_vram_usage()
        hip_free, hip_total = torch.cuda.mem_get_info(0)
        hip_used = hip_total - hip_free
        drift = int(aimdo_usage) - hip_used

        print(f"[aimdo_monitor] {label}")
        print(f"[aimdo_monitor]   aimdo total_vram_usage : {_fmt(aimdo_usage)}")
        print(f"[aimdo_monitor]   hipMemGetInfo used     : {_fmt(hip_used)}")
        print(f"[aimdo_monitor]   hipMemGetInfo free     : {_fmt(hip_free)}")
        print(f"[aimdo_monitor]   drift (aimdo - hip)    : {drift // MB:>+7} MB")

        if aimdo_usage == 0 and hip_used > 500 * MB:
            print(f"[aimdo_monitor]   *** AIMDO NOT ACTIVE: aimdo=0 but hip shows {hip_used//MB}MB used!")
            print(f"[aimdo_monitor]   *** Hooks are NOT intercepting allocations in this process.")

        control.analyze()
    except Exception as e:
        print(f"[aimdo_monitor] could not read GPU info: {e}")


# --- Polling state ---
_poll_thread = None
_poll_stop = threading.Event()
_poll_lock = threading.Lock()

# --- Session stats ---
_stats = {
    "samples": 0,
    "drift_sum": 0,
    "drift_abs_sum": 0,
    "drift_max": 0,
    "drift_over_512": 0,
    "drift_over_1024": 0,
    "wddm_events": 0,
    "aimdo_zero_while_hip_used": 0,
    "drift_negative": 0,
    "drift_positive": 0,
}

def _reset_stats():
    for k in _stats:
        _stats[k] = 0

def _print_summary():
    s = _stats
    if s["samples"] == 0:
        return

    avg_drift = s["drift_abs_sum"] // s["samples"]
    hook_fail_ratio = s["aimdo_zero_while_hip_used"] / s["samples"]
    bad_drift_ratio = s["drift_over_1024"] / s["samples"]
    degraded_drift_ratio = s["drift_over_512"] / s["samples"]

    if hook_fail_ratio > 0.8:
        verdict = "Fail - hooks not intercepting, aimdo reported 0 while GPU was in use"
    elif hook_fail_ratio > 0.2:
        verdict = "Fail - hooks partially failing, aimdo was 0 for a significant portion of the session"
    elif bad_drift_ratio > 0.3 or s["drift_max"] > 2048 * MB:
        verdict = "Poor - drift exceeded 1GB frequently or spiked above 2GB, aimdo accounting unreliable"
    elif degraded_drift_ratio > 0.05 or s["drift_max"] > 512 * MB:
        verdict = "Slightly degraded - drift > 512MB, aimdo mostly working but not fully accurate"
    else:
        verdict = "Ok - working as intended"

    if s["wddm_events"] > 0:
        verdict += f"  [!] WDDM paging occurred {s['wddm_events']} time(s), VRAM overflowed to system RAM"

    neg, pos = s["drift_negative"], s["drift_positive"]
    total_directional = neg + pos
    if total_directional == 0:
        direction = "None - drift was always zero"
    elif neg / max(total_directional, 1) > 0.9 and avg_drift > 2048 * MB:
        direction = "Strongly negative - aimdo significantly undercounting"
    elif pos / max(total_directional, 1) > 0.9 and avg_drift > 2048 * MB:
        direction = "Strongly positive - aimdo significantly overcounting"
    elif neg / max(total_directional, 1) > 0.9 and avg_drift > 1024 * MB:
        direction = "Negative - aimdo undercounting"
    elif neg > pos:
        direction = "Slightly negative - normal, driver overhead not tracked by aimdo"
    else:
        direction = "Mixed"

    print("")
    print("[aimdo_monitor] -- Session Summary --")
    print(f"[aimdo_monitor]   Samples collected  : {s['samples']}")
    print(f"[aimdo_monitor]   Avg drift          : {avg_drift // MB:>+7} MB")
    print(f"[aimdo_monitor]   Max drift          : {s['drift_max'] // MB:>+7} MB")
    print(f"[aimdo_monitor]   Drift direction    : {direction}")
    print(f"[aimdo_monitor]   Drift > 512MB      : {s['drift_over_512']} time(s)")
    print(f"[aimdo_monitor]   Drift > 1GB        : {s['drift_over_1024']} time(s)")
    print(f"[aimdo_monitor]   WDDM paging events : {s['wddm_events']}")
    print(f"[aimdo_monitor]   Verdict            : {verdict}")
    print("[aimdo_monitor] ------------------------------------")

atexit.register(_print_summary)

def _poll_loop():
    last_aimdo = None
    while not _poll_stop.wait(timeout=0.5):
        if AIMDO_AVAILABLE:
            try:
                aimdo_usage = control.get_total_vram_usage()
                hip_free, hip_total = torch.cuda.mem_get_info(0)
                hip_used = hip_total - hip_free
                drift = int(aimdo_usage) - hip_used
                abs_drift = abs(drift)

                wddm_paging = hip_free == 0
                wddm_flag = "  *** WDDM PAGING TO RAM ***" if wddm_paging else ""

                # accumulate stats
                _stats["samples"] += 1
                _stats["drift_abs_sum"] += abs_drift
                if abs_drift > _stats["drift_max"]:
                    _stats["drift_max"] = abs_drift
                if abs_drift > 512 * MB:
                    _stats["drift_over_512"] += 1
                if abs_drift > 1024 * MB:
                    _stats["drift_over_1024"] += 1
                if wddm_paging:
                    _stats["wddm_events"] += 1
                if aimdo_usage == 0 and hip_used > 500 * MB:
                    _stats["aimdo_zero_while_hip_used"] += 1
                if drift < 0:
                    _stats["drift_negative"] += 1
                elif drift > 0:
                    _stats["drift_positive"] += 1

                if aimdo_usage != last_aimdo or abs_drift > 256 * MB or wddm_paging:
                    print(f"[aimdo_poll] aimdo={aimdo_usage//MB}MB  hip_used={hip_used//MB}MB  drift={drift//MB:+d}MB{wddm_flag}")
                    last_aimdo = aimdo_usage
            except Exception as e:
                print(f"[aimdo_poll] error reading GPU info: {e}")

def _start_polling():
    global _poll_thread
    with _poll_lock:
        if _poll_thread is not None and _poll_thread.is_alive():
            return
        _reset_stats()
        _poll_stop.clear()
        _poll_thread = threading.Thread(target=_poll_loop, daemon=True)
        _poll_thread.start()
        print("[aimdo_monitor] Polling started (0.5s interval)")

def _stop_polling():
    with _poll_lock:
        if _poll_thread is None or not _poll_thread.is_alive():
            return
        _poll_stop.set()
        _print_summary()
        _reset_stats()
        print("[aimdo_monitor] Polling stopped")


# --- API endpoints for the frontend button ---
@server.PromptServer.instance.routes.post("/aimdo_monitor/start")
async def aimdo_start(request):
    _start_polling()
    return web.json_response({"polling": True})

@server.PromptServer.instance.routes.post("/aimdo_monitor/stop")
async def aimdo_stop(request):
    _stop_polling()
    return web.json_response({"polling": False})

@server.PromptServer.instance.routes.get("/aimdo_monitor/status")
async def aimdo_status(request):
    active = _poll_thread is not None and _poll_thread.is_alive()
    return web.json_response({"polling": active, "available": AIMDO_AVAILABLE})


# --- ComfyUI workflow nodes ---

class AimdoVRAMMonitor:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "passthrough": ("LATENT",),
                "label": ("STRING", {"default": "checkpoint"}),
            }
        }
    RETURN_TYPES = ("LATENT",)
    RETURN_NAMES = ("passthrough",)
    FUNCTION = "monitor"
    CATEGORY = "DN/aimdo"
    OUTPUT_NODE = True

    def monitor(self, passthrough, label):
        _snapshot(label=label)
        return (passthrough,)


class AimdoVRAMMonitorModel:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "label": ("STRING", {"default": "after_model_load"}),
            }
        }
    RETURN_TYPES = ("MODEL",)
    RETURN_NAMES = ("model",)
    FUNCTION = "monitor"
    CATEGORY = "DN/aimdo"
    OUTPUT_NODE = True

    def monitor(self, model, label):
        _snapshot(label=label)
        return (model,)


NODE_CLASS_MAPPINGS = {
    "AimdoVRAMMonitor": AimdoVRAMMonitor,
    "AimdoVRAMMonitorModel": AimdoVRAMMonitorModel,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AimdoVRAMMonitor": "Aimdo VRAM Monitor DN (Latent)",
    "AimdoVRAMMonitorModel": "Aimdo VRAM Monitor DN (Model)",
}

WEB_DIRECTORY = "./js"

print(f"[aimdo_monitor] Loaded. aimdo available: {AIMDO_AVAILABLE}. Use the sidebar button to start polling.")
