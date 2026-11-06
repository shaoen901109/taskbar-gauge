# taskbar-gauge

**English** ・ [繁體中文](README.zh-TW.md)

A small Windows 11 utility that draws three open-ring concentric gauges on the taskbar, just left of the system tray clock:

- **CPU % + RAM**
- **GPU % + VRAM** (NVIDIA only, via NVML)
- **Disk** busy level (up to 4 physical disks; the outer ring is the first disk, ordered by drive id)

Hover the mouse over a gauge to see the full numbers. Right-click to quit.

## Requirements

- Windows 11 (Windows 10 is untested)
- Python 3 with `pythonw` on `PATH` (developed on Python 3.12)
- Packages (`requirements.txt`): `PySide6-Essentials`, `nvidia-ml-py`, `psutil`, `pywin32`
- An NVIDIA GPU is needed for the GPU gauge (other GPUs are not supported)

## Install

```powershell
pip install -r requirements.txt
```

## Usage

Double-click `啟動.bat` (start; runs `pythonw taskbar_gauge.py` in the background). Only one instance runs at a time (named mutex).

To start with Windows, run `安裝開機啟動.bat`; it creates a shortcut in your per-user Startup folder (no administrator rights needed). `移除開機啟動.bat` removes it.

## How it works

Windows 11 has no deskband, so the tool opens one transparent, non-focusable, always-on-top tool window per taskbar:

- Primary taskbar: positioned from the left edge of `TrayNotifyWnd` inside `Shell_TrayWnd`.
- Secondary taskbars (`Shell_SecondaryTrayWnd`) have no such child window, so a fixed offset from the right edge (`secondary_right_offset_dip`) is used.
- Position and z-order are refreshed every `reposition_ms`; gauges on a monitor are hidden while a fullscreen window is in the foreground. Coordinates are physical pixels, scaled per monitor DPI.
- Disk busy level = 100 − PDH `\PhysicalDisk(*)\% Idle Time` (same source as Task Manager).

## Configuration

Edit `config.json` next to `taskbar_gauge.py` and restart the program. Missing keys fall back to defaults.

| Key | Default | Meaning |
|---|---|---|
| `update_ms` | `1000` | sampling interval |
| `reposition_ms` | `500` | reposition / re-topmost interval |
| `animation` | `true` | animate value changes |
| `theme` | `"auto"` | `auto`, `light` or `dark` |
| `max_disks` | `4` | number of physical disks shown |
| `gauge_pad_dip` | `2` | padding around gauges (DIP) |
| `gauge_gap_dip` | `12` | spacing between gauges (DIP) |
| `primary_tray_gap_dip` | `6` | gap to the tray on the primary taskbar (DIP) |
| `secondary_right_offset_dip` | `125` | offset from the right edge on secondary taskbars (DIP) |
| `show_on` | `"primary"` | `all`, `primary` or `secondary` |
| `hide_when_fullscreen` | `true` | hide while a fullscreen window is in front |
| `gpu_index` | `0` | which NVIDIA GPU to read |

## Known limitations

- The secondary-taskbar offset is a fixed value; if you change the clock format or tray width, adjust `secondary_right_offset_dip`.
- Light/dark theme is detected only when sampling.
- GPU support is NVIDIA only.

## Troubleshooting

If the gauges disappear, look at `error.log` next to the script. Timer callbacks and painting are wrapped in try/except; errors are logged (the same error at most once per 60 seconds; the log rotates to `error.log.old` above 512 KB).

## Uninstall

Run `移除開機啟動.bat` to remove the startup shortcut, right-click a gauge to quit, then delete the folder.

## License

[MIT](LICENSE)
