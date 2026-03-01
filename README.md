# ComfyUI-DN_AimdoMonitor

A ComfyUI custom node for diagnosing and evaluating `comfy_aimdo`'s behavior at runtime. Rather than being a general VRAM monitor, it specifically validates whether aimdo's internal allocation tracking matches what the AMD driver actually reports via `hipMemGetInfo` - exposing hook failures, accounting drift, and - on Windows - silent VRAM paging to system RAM.

---

## Requirements

| Requirement | Notes |
|---|---|
| **AMD GPU** | Any ROCm-supported card. |
| **ROCm** | Must be installed and working with PyTorch. |
| **PyTorch with ROCm** | `torch.cuda.mem_get_info()` must be functional. |
| **ComfyUI** | Any recent version. |
| **comfy_aimdo** | Must be installed and loaded. |

**OS:** Windows and Linux both supported. The WDDM paging detection is Windows-only by nature and simply won't trigger on Linux.

---

## Installation

1. Clone or download this repository into your ComfyUI custom nodes folder:

```
ComfyUI/custom_nodes/
```

The folder must contain:
```
ComfyUI-DN_AimdoMonitor/
├── __init__.py
└── js/
    └── DN_AimdoMonitor.js
```

2. Restart ComfyUI normally:

```bash
python main.py
```

---

## Usage

### Floating button

A **[Start VRAM Poll]** button appears in the bottom-right corner of the ComfyUI interface. Click it to start polling; it turns red and becomes **[Stop VRAM Poll]**. The button is draggable and remembers its position across reloads.

While polling, stats are printed to the console every 0.5 seconds (only when values change or drift exceeds 256 MB):

```
[aimdo_poll] aimdo=4200MB  hip_used=4350MB  drift=+150MB
```

### Workflow nodes

Two passthrough nodes are available for inline snapshots at specific points in a workflow:

- **Aimdo VRAM Monitor (Latent)** - connects inline on a `LATENT` wire
- **Aimdo VRAM Monitor (Model)** - connects inline on a `MODEL` wire

Both print a detailed snapshot to the console when the node executes:

```
[aimdo_monitor] after_model_load
[aimdo_monitor]   aimdo total_vram_usage :    4200 MB
[aimdo_monitor]   hipMemGetInfo used     :    4350 MB
[aimdo_monitor]   hipMemGetInfo free     :   11834 MB
[aimdo_monitor]   drift (aimdo - hip)    :    +150 MB
[aimdo_monitor]   OK: tracking within 512MB
```

### Session summary

When polling is stopped - either via the button or on ComfyUI shutdown - a summary is printed to the console:

```

[aimdo_monitor] -- Session Summary --
[aimdo_monitor]   Samples collected  :     104
[aimdo_monitor]   Avg drift          :    +429 MB
[aimdo_monitor]   Max drift          :    +660 MB
[aimdo_monitor]   Drift direction    : Slightly negative - normal, driver overhead not tracked by aimdo
[aimdo_monitor]   Drift > 512MB      : 23 time(s)
[aimdo_monitor]   Drift > 1GB        : 0 time(s)
[aimdo_monitor]   WDDM paging events : 0
[aimdo_monitor]   Verdict            : Slightly degraded - drift > 512MB, aimdo mostly working but not fully accurate
[aimdo_monitor] ------------------------------------
```

Possible verdicts:

| Verdict | Meaning |
|---|---|
| `Ok - working as intended` | Low drift throughout, hooks intercepting correctly. |
| `Slightly degraded - drift > 512MB...` | Drift exceeded 512MB at least occasionally, mostly working but not fully accurate. |
| `Poor - drift exceeded 1GB frequently...` | Drift exceeded 1GB in more than 30% of samples, or max drift spiked above 2GB. Accounting unreliable. |
| `Fail - hooks partially failing...` | aimdo was 0 for a significant portion of the session. |
| `Fail - hooks not intercepting...` | aimdo reported 0 for nearly the entire session while the GPU was in use. |

Drift direction tells you which way aimdo is off. A slightly negative result is normal on Windows due to driver overhead that aimdo does not track. Negative (avg > 1GB) means aimdo is meaningfully undercounting. Strongly negative (avg > 2GB) means something is seriously wrong with hook interception.

---

## Console warnings

| Message | Meaning |
|---|---|
| `*** AIMDO NOT ACTIVE: aimdo=0 but hip shows nMB used!` | aimdo hooks are not intercepting allocations - check that `comfy_aimdo` is installed and loaded correctly. |
| `*** WDDM PAGING TO RAM ***` | *(Windows only)* VRAM is full and allocations are overflowing into system RAM. Generation will be slow. |

---