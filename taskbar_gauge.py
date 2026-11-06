"""工作列硬體儀表：在每個工作列的系統匣左側顯示 CPU/RAM、GPU/VRAM、硬碟 三組開口同心圓儀表。"""
import json
import os
import sys
import ctypes
from ctypes import wintypes

import psutil
import win32api
import win32con
import win32event
import win32gui
import win32pdh
import winerror

from PySide6.QtCore import Qt, QTimer, QRectF, QEvent
from PySide6.QtGui import QPainter, QPen, QColor, QPainterPath, QFont, QFontMetricsF
from PySide6.QtWidgets import QApplication, QWidget, QMenu, QToolTip

try:
    import pynvml
    pynvml.nvmlInit()
    _GPU = None
except Exception:
    pynvml = None
    _GPU = None

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CFG = {
    "update_ms": 1000, "reposition_ms": 500, "animation": True, "theme": "auto",
    "max_disks": 4, "gauge_pad_dip": 2, "gauge_gap_dip": 12, "primary_tray_gap_dip": 6,
    "show_on": "secondary",  # all / primary / secondary
    "secondary_right_offset_dip": 125, "hide_when_fullscreen": True, "gpu_index": 0,
}


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    try:
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


CFG = load_cfg()
GB = 1024 ** 3
MB = 1024 ** 2

# ---------- Win32 輔助 ----------
user32 = ctypes.windll.user32
shcore = ctypes.windll.shcore
user32.MonitorFromPoint.restype = wintypes.HMONITOR
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]


def monitor_dpi(hmon):
    d, e = ctypes.c_uint(96), ctypes.c_uint(96)
    try:
        shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(d), ctypes.byref(e))
    except Exception:
        pass
    return d.value or 96


def monitor_rect(hmon):
    return win32api.GetMonitorInfo(hmon)["Monitor"]


def find_taskbars():
    """回傳 [(hwnd, rect, hmon, tray_left或None, is_primary)]"""
    res = []

    def cb(h, _):
        cls = win32gui.GetClassName(h)
        if cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd") and win32gui.IsWindowVisible(h):
            r = win32gui.GetWindowRect(h)
            pt = wintypes.POINT((r[0] + r[2]) // 2, (r[1] + r[3]) // 2)
            hmon = user32.MonitorFromPoint(pt, 2)
            tray_left = None
            try:
                tn = win32gui.FindWindowEx(h, 0, "TrayNotifyWnd", None)
                if tn and win32gui.IsWindowVisible(tn):
                    tr = win32gui.GetWindowRect(tn)
                    if tr[2] - tr[0] > 20:
                        tray_left = tr[0]
            except Exception:
                pass
            res.append((h, r, hmon, tray_left, cls == "Shell_TrayWnd"))
        return True

    win32gui.EnumWindows(cb, None)
    return res


DESKTOP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def fullscreen_monitor():
    """前景視窗若為全螢幕，回傳它所在螢幕 HMONITOR，否則 None。"""
    try:
        fg = win32gui.GetForegroundWindow()
        if not fg or not win32gui.IsWindowVisible(fg):
            return None
        if win32gui.GetClassName(fg) in DESKTOP_CLASSES:
            return None
        if win32gui.IsIconic(fg):
            return None
        r = win32gui.GetWindowRect(fg)
        hmon = user32.MonitorFromWindow(fg, 2)
        m = monitor_rect(hmon)
        if r[0] <= m[0] and r[1] <= m[1] and r[2] >= m[2] and r[3] >= m[3]:
            return int(hmon)
    except Exception:
        pass
    return None


def light_theme():
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 1
    except Exception:
        return False


# ---------- 取樣 ----------
class DiskIO:
    """各實體硬碟的讀寫忙碌度＝100 − PhysicalDisk「% Idle Time」，與工作管理員的「作用時間」同源。
    （psutil 在 Windows 的 read_time/write_time 幾乎不動，不能用。）"""

    def __init__(self):
        self.q = None
        self.cs = []

    def _open(self):
        _, inst = win32pdh.EnumObjectItems(None, None, "PhysicalDisk", win32pdh.PERF_DETAIL_WIZARD)
        q = win32pdh.OpenQuery()
        cs = []
        for i in inst:
            if i == "_Total":
                continue
            letters = " ".join(x for x in i.split()[1:]) or f"磁碟 {i.split()[0]}"
            cs.append((letters, [win32pdh.AddEnglishCounter(q, rf"\PhysicalDisk({i})\{n}")
                                 for n in ("% Idle Time", "Disk Read Bytes/sec", "Disk Write Bytes/sec")]))
        cs.sort(key=lambda x: x[0])
        win32pdh.CollectQueryData(q)
        self.q, self.cs = q, cs

    def sample(self, limit):
        try:
            if self.q is None:
                self._open()
                return [(n, 0.0, 0.0, 0.0) for n, _ in self.cs][:limit]
            win32pdh.CollectQueryData(self.q)
            out = []
            for name, c in self.cs:
                idle, rd, wr = (win32pdh.GetFormattedCounterValue(x, win32pdh.PDH_FMT_DOUBLE)[1] for x in c)
                out.append((name, max(0.0, min(100.0, 100.0 - idle)), rd, wr))
            return out[:limit]
        except Exception:
            self.q = None  # 硬碟增減或計數器失效 → 下次重建
            return []


def gpu_sample():
    if pynvml is None:
        return None
    try:
        h = pynvml.nvmlDeviceGetHandleByIndex(CFG["gpu_index"])
        u = pynvml.nvmlDeviceGetUtilizationRates(h).gpu
        m = pynvml.nvmlDeviceGetMemoryInfo(h)
        try:
            t = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
        except Exception:
            t = None
        return u, m.used, m.total, t
    except Exception:
        return None


def gb_txt(v):
    g = v / GB
    return f"{g:.0f}" if g >= 10 else f"{g:.1f}"


class Sampler:
    def __init__(self):
        psutil.cpu_percent(None)
        self.disk_io = DiskIO()
        self.disk_io.sample(CFG["max_disks"])
        self.data = None
        self.update()

    def update(self):
        cpu = psutil.cpu_percent(None)
        vm = psutil.virtual_memory()
        g = gpu_sample()
        disks = self.disk_io.sample(CFG["max_disks"])
        d = {}
        # gauge 1: CPU / RAM
        d["cpu"] = dict(
            rings=[cpu / 100, vm.used / vm.total],
            lines=[(f"{cpu:.0f}%", 0), (f"{gb_txt(vm.used)}/{vm.total / GB:.0f}", 1)],
            tip=f"CPU 使用率：{cpu:.0f}%\nRAM：{vm.used / GB:.1f} / {vm.total / GB:.1f} GB（{vm.percent:.0f}%）")
        if g:
            u, vu, vt, t = g
            tt = f"\n溫度：{t}°C" if t is not None else ""
            d["gpu"] = dict(
                rings=[u / 100, vu / vt],
                lines=[(f"{u:.0f}%", 0), (f"{gb_txt(vu)}/{vt / GB:.0f}", 1)],
                tip=f"GPU 使用率：{u:.0f}%\nVRAM：{vu / GB:.1f} / {vt / GB:.1f} GB（{vu / vt * 100:.0f}%）{tt}")
        else:
            d["gpu"] = dict(rings=[0, 0], lines=[("--", 0), ("", 1)], tip="GPU：讀不到 NVML")
        d["disk"] = dict(
            rings=[x[1] / 100 for x in disks], lines=None,
            tip="硬碟讀寫忙碌度（由外而內）\n" + "\n".join(
                f"{x[0]}  {x[1]:.0f}%　讀 {x[2] / MB:.1f}／寫 {x[3] / MB:.1f} MB/s" for x in disks))
        self.data = d


# ---------- 繪圖 ----------
START_DEG = 225.0   # 左下 45°；缺口置中於正下方（225°→315°）
SWEEP_DEG = 270.0


class GaugeBar(QWidget):
    ORDER = ("cpu", "gpu", "disk")

    def __init__(self, sampler, hmon):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setMouseTracking(True)
        self.sampler = sampler
        self.hmon = hmon
        self.shown_vals = {}   # 動畫中的顯示值
        self.light = False
        self.last_geom = None
        self.setWindowTitle("工作列硬體儀表")
        self.winId()
        h = int(self.winId())
        ex = win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(h, win32con.GWL_EXSTYLE,
                               ex | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW)

    # 動畫：把目前顯示值往目標靠近，回傳是否還在動
    def step(self):
        moving = False
        for key in self.ORDER:
            tgt = self.sampler.data[key]["rings"]
            cur = self.shown_vals.get(key)
            if cur is None or len(cur) != len(tgt) or not CFG["animation"]:
                self.shown_vals[key] = list(tgt)
                moving = True
                continue
            for i, t in enumerate(tgt):
                diff = t - cur[i]
                if abs(diff) > 0.002:
                    cur[i] += diff * 0.35
                    moving = True
                else:
                    cur[i] = t
        return moving

    def paintEvent(self, e):
        try:
            self._paint()
        except Exception:
            log_error("paint")

    def _paint(self):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        w, h = self.width(), self.height()
        cell = w / 3.0
        S = min(cell, h)
        fg = QColor(30, 30, 30) if self.light else QColor(245, 245, 245)
        sub = QColor(110, 110, 110) if self.light else QColor(150, 150, 155)
        track = QColor(0, 0, 0, 40) if self.light else QColor(255, 255, 255, 45)
        for gi, key in enumerate(self.ORDER):
            g = self.sampler.data[key]
            vals = self.shown_vals.get(key, g["rings"])
            cx, cy = gi * cell + cell / 2, h / 2
            n = len(vals)
            r_out = 0.455 * S
            if n <= 2:
                step = 0.135 * S
                pen_w = 0.085 * S
            else:
                r_min = 0.17 * S
                step = (r_out - r_min) / max(n - 1, 1)
                pen_w = min(step * 0.5, 0.075 * S)
            for i, v in enumerate(vals):
                r = r_out - i * step
                rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
                path_t = QPainterPath()
                path_t.arcMoveTo(rect, START_DEG)
                path_t.arcTo(rect, START_DEG, -SWEEP_DEG)
                p.setPen(QPen(track, pen_w * 0.7, Qt.SolidLine, Qt.RoundCap))
                p.drawPath(path_t)
                v = max(0.0, min(1.0, v))
                if v > 0.004:
                    path_v = QPainterPath()
                    path_v.arcMoveTo(rect, START_DEG)
                    path_v.arcTo(rect, START_DEG, -SWEEP_DEG * v)
                    p.setPen(QPen(fg, pen_w, Qt.SolidLine, Qt.RoundCap))
                    p.drawPath(path_v)
            if g["lines"]:
                inner_w = 0.56 * S
                ly = [cy - 0.085 * S, cy + 0.115 * S]
                for li, (txt, tone) in enumerate(g["lines"]):
                    if not txt:
                        continue
                    f = QFont("Segoe UI")
                    f.setBold(li == 0)
                    px = (0.20 if li == 0 else 0.165) * S
                    f.setPixelSize(max(6, int(round(px))))
                    fm = QFontMetricsF(f)
                    while fm.horizontalAdvance(txt) > inner_w and f.pixelSize() > 6:
                        f.setPixelSize(f.pixelSize() - 1)
                        fm = QFontMetricsF(f)
                    p.setFont(f)
                    p.setPen(fg if li == 0 else sub)
                    if li == 1 and "/" in txt:   # 已用白、總量灰
                        used, tot = txt.split("/", 1)
                        wu = fm.horizontalAdvance(used + "/")
                        wt = fm.horizontalAdvance(tot)
                        x0 = cx - (wu + wt) / 2
                        base = ly[li] + fm.ascent() / 2 - fm.descent() / 2
                        p.setPen(fg)
                        p.drawText(int(round(x0)), int(round(base)), used)
                        p.setPen(sub)
                        p.drawText(int(round(x0 + fm.horizontalAdvance(used))), int(round(base)), "/" + tot)
                    else:
                        tw = fm.horizontalAdvance(txt)
                        base = ly[li] + fm.ascent() / 2 - fm.descent() / 2
                        p.drawText(int(round(cx - tw / 2)), int(round(base)), txt)
        p.end()

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            gi = min(2, max(0, int(e.pos().x() / (self.width() / 3.0))))
            QToolTip.showText(e.globalPos(), self.sampler.data[self.ORDER[gi]]["tip"], self)
            return True
        return super().event(e)

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction("結束").triggered.connect(QApplication.quit)
        m.exec(e.globalPos())

    def place(self, x, y, w, h):
        g = (x, y, w, h)
        if g != self.last_geom:
            self.last_geom = g
            win32gui.SetWindowPos(int(self.winId()), win32con.HWND_TOPMOST, x, y, w, h,
                                  win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW)
        else:
            self.raise_topmost()

    def raise_topmost(self):
        win32gui.SetWindowPos(int(self.winId()), win32con.HWND_TOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOACTIVATE | win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)


class Manager:
    def __init__(self, app):
        self.app = app
        self.sampler = Sampler()
        self.bars = {}      # hwnd(taskbar) -> GaugeBar
        self.light = light_theme() if CFG["theme"] == "auto" else CFG["theme"] == "light"
        self.t_sample = QTimer()
        self.t_sample.timeout.connect(guarded(self.sample, "sample"))
        self.t_sample.start(CFG["update_ms"])
        self.t_anim = QTimer()
        self.t_anim.timeout.connect(guarded(self.animate, "animate"))
        self.t_anim.start(33)
        self.t_pos = QTimer()
        self.t_pos.timeout.connect(guarded(self.reposition, "reposition"))
        self.t_pos.start(CFG["reposition_ms"])
        guarded(self.reposition, "reposition")()

    def sample(self):
        self.sampler.update()
        if CFG["theme"] == "auto":
            self.light = light_theme()
        for b in self.bars.values():
            b.light = self.light
        self.animate()

    def animate(self):
        for b in self.bars.values():
            if b.isVisible() and (b.step() or True):
                b.update()

    def reposition(self):
        tbs = find_taskbars()
        fs = fullscreen_monitor() if CFG["hide_when_fullscreen"] else None
        alive = set()
        show_on = CFG["show_on"]
        for hwnd, r, hmon, tray_left, primary in tbs:
            if (show_on == "primary" and not primary) or (show_on == "secondary" and primary):
                continue
            alive.add(hwnd)
            dpi = monitor_dpi(hmon)
            k = dpi / 96.0
            th = r[3] - r[1]
            pad = int(round(CFG["gauge_pad_dip"] * k))
            gh = th - 2 * pad
            gap = int(round(CFG["gauge_gap_dip"] * k))
            gw = gh * 3 + gap * 2
            if tray_left is not None and primary:
                right = tray_left - int(round(CFG["primary_tray_gap_dip"] * k))
            else:
                right = r[2] - int(round(CFG["secondary_right_offset_dip"] * k))
            x, y = right - gw, r[1] + pad
            bar = self.bars.get(hwnd)
            if bar is None:
                bar = GaugeBar(self.sampler, hmon)
                bar.light = self.light
                self.bars[hwnd] = bar
                bar.step()
            if fs is not None and int(hmon) == fs:
                if bar.isVisible():
                    bar.hide()
                    bar.last_geom = None
                continue
            if not bar.isVisible():
                bar.show()
                bar.last_geom = None
            bar.place(x, y, gw, gh)
        for hwnd in list(self.bars):
            if hwnd not in alive:
                self.bars.pop(hwnd).close()


LOG_PATH = os.path.join(HERE, "error.log")
_last_err = {}


def log_error(where):
    """寫入 error.log；同一處的同一錯誤 60 秒內只記一次，避免灌爆。"""
    import time
    import traceback
    tb = traceback.format_exc()
    key = (where, tb.strip().splitlines()[-1] if tb.strip() else "")
    now = time.time()
    if now - _last_err.get(key, 0) < 60:
        return
    _last_err[key] = now
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 512 * 1024:
            os.replace(LOG_PATH, LOG_PATH + ".old")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} [{where}]\n{tb}\n")
    except Exception:
        pass


def guarded(fn, where):
    """計時器回呼保護：任何例外（鎖定畫面時 Win32 呼叫失敗等）只記錄，不讓程式結束。"""
    def wrapper(*a):
        try:
            fn(*a)
        except Exception:
            log_error(where)
    return wrapper


def main():
    if sys.stderr is None:  # pythonw 沒有主控台：把未捕捉的錯誤導到 error.log
        sys.stderr = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    import faulthandler
    faulthandler.enable(sys.stderr)  # 原生層崩潰也留下堆疊
    sys.excepthook = lambda *ei: log_error("excepthook")
    mutex = win32event.CreateMutex(None, False, "Local\\TaskbarGaugeSingleInstance")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        return
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    mgr = Manager(app)  # noqa: F841
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
