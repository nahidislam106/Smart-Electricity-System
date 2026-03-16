"""
Smart Electricity Churi - Power Theft Detection Dashboard
Real-time GUI that reads serial data from Arduino (INA3221) and displays
all channel parameters plus system analysis with theft alert.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import serial
import serial.tools.list_ports
import threading
import queue
import re
from datetime import datetime


# ─────────────────────────── colour palette ────────────────────────────────
BG          = "#1e1e2e"
PANEL_BG    = "#2a2a3e"
HEADER_BG   = "#313150"
ACCENT      = "#7c6af7"
TEXT        = "#cdd6f4"
TEXT_DIM    = "#6c7086"
OK_GREEN    = "#a6e3a1"
WARN_YELLOW = "#f9e2af"
ERR_RED     = "#f38ba8"
BORDER      = "#45475a"


# ───────────────────────────── serial parser ───────────────────────────────
class SerialParser:
    """
    Parses the fixed-format text blocks produced by the Arduino sketch.
    Each full reading contains CH1 data, CH2 data, and system analysis.
    """

    CH_FIELDS = [
        ("Bus Voltage (V)",       "busV"),
        ("Shunt Voltage (V)",     "shuntV"),
        ("Current (A)",           "current"),
        ("Power (W)",             "power"),
        ("Supply Voltage (V)",    "supplyV"),
        ("Load Resistance (Ohm)", "loadR"),
        ("Shunt Resistance (Ohm)","shuntR"),
        ("Recomputed Power (W)",  "recomputedP"),
        ("Voltage Drop Ratio",    "dropRatio"),
        ("Current Density",       "currentDensity"),
        ("Conductance (S)",       "conductance"),
    ]

    SYS_FIELDS = [
        ("Input Power (CH1) (W)",     "inputPower"),
        ("Output Power (CH2) (W)",    "outputPower"),
        ("Power Error (W)",           "powerError"),
        ("Efficiency Ratio",          "effRatio"),
        ("Efficiency (%)",            "effPct"),
        ("Current Difference (CH1-CH2)", "currentDiff"),
        ("Theft Alert",               "theftAlert"),
    ]

    def __init__(self):
        self.buffer = []
        self.ch1    = {}
        self.ch2    = {}
        self.sys    = {}
        self._section = None   # "ch1" | "ch2" | "sys"

    def feed(self, line: str) -> dict | None:
        """
        Feed one line. Returns a complete snapshot dict when a full cycle is
        parsed, otherwise returns None.
        """
        line = line.strip()
        if not line:
            return None

        # ── section headers ──
        if "CHANNEL 1" in line and "Input" in line:
            self._section = "ch1"
            self.ch1 = {}
            return None
        if "CHANNEL 2" in line and "Output" in line:
            self._section = "ch2"
            self.ch2 = {}
            return None
        if "System Analysis" in line:
            self._section = "sys"
            self.sys = {}
            return None

        # ── separator / unrelated lines ──
        if line.startswith("===") or line.startswith("INA3221"):
            return None

        # ── key: value lines ──
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip().lstrip("0123456789. ")
            val = val.strip()

            if self._section == "ch1":
                self._store(key, val, self.ch1, self.CH_FIELDS)
            elif self._section == "ch2":
                self._store(key, val, self.ch2, self.CH_FIELDS)
            elif self._section == "sys":
                self._store(key, val, self.sys, self.SYS_FIELDS)

        # ── emit snapshot once sys block has all expected fields ──
        if self._section == "sys" and len(self.sys) == len(self.SYS_FIELDS):
            snapshot = {
                "ts":        datetime.now().strftime("%H:%M:%S"),
                "ch1":       dict(self.ch1),
                "ch2":       dict(self.ch2),
                "sys":       dict(self.sys),
            }
            self._section = None
            return snapshot

        return None

    @staticmethod
    def _store(raw_key, raw_val, target, fields):
        for label, attr in fields:
            # match by checking if all words of the short label appear in raw_key
            keywords = label.replace("(","").replace(")","").lower().split()
            if all(w in raw_key.lower() for w in keywords):
                try:
                    target[attr] = float(raw_val)
                except ValueError:
                    target[attr] = raw_val   # e.g. "YES" / "NO"
                break


# ─────────────────────────── main application ──────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Smart Electricity Churi – Power Theft Monitor")
        self.geometry("1100x780")
        self.configure(bg=BG)
        self.resizable(True, True)

        self._serial: serial.Serial | None = None
        self._reader_thread: threading.Thread | None = None
        self._running = False
        self._queue: queue.Queue = queue.Queue()
        self._parser = SerialParser()

        self._build_ui()
        self._start_polling()

    # ───────────────────────── UI construction ─────────────────────────────
    def _build_ui(self):
        self._build_toolbar()
        self._build_status_bar()

        content = tk.Frame(self, bg=BG)
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,10))
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)
        content.rowconfigure(1, weight=0)

        self._panels = {}
        self._panels["ch1"] = self._build_channel_panel(content, "CH1 – Input Side",  0, 0)
        self._panels["ch2"] = self._build_channel_panel(content, "CH2 – Output Side", 0, 1)
        self._build_system_panel(content, row=1)

    def _build_toolbar(self):
        bar = tk.Frame(self, bg=HEADER_BG, pady=8)
        bar.pack(fill=tk.X)

        tk.Label(bar, text="⚡  Power Theft Detection Dashboard",
                 bg=HEADER_BG, fg=ACCENT,
                 font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT, padx=14)

        right = tk.Frame(bar, bg=HEADER_BG)
        right.pack(side=tk.RIGHT, padx=12)

        tk.Label(right, text="Port:", bg=HEADER_BG, fg=TEXT,
                 font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0,4))

        self._port_var = tk.StringVar()
        self._port_combo = ttk.Combobox(right, textvariable=self._port_var,
                                        width=14, state="readonly")
        self._port_combo.pack(side=tk.LEFT, padx=(0,6))
        self._refresh_ports()

        tk.Label(right, text="Baud:", bg=HEADER_BG, fg=TEXT,
                 font=("Segoe UI", 10)).pack(side=tk.LEFT)

        self._baud_var = tk.StringVar(value="9600")
        ttk.Combobox(right, textvariable=self._baud_var,
                     values=["9600","19200","38400","115200"],
                     width=8, state="readonly").pack(side=tk.LEFT, padx=(2,8))

        self._connect_btn = tk.Button(right, text="Connect",
                                      command=self._toggle_connection,
                                      bg=ACCENT, fg="white",
                                      font=("Segoe UI", 9, "bold"),
                                      relief=tk.FLAT, padx=10, pady=4,
                                      cursor="hand2")
        self._connect_btn.pack(side=tk.LEFT, padx=(0,6))

        tk.Button(right, text="↺", command=self._refresh_ports,
                  bg=PANEL_BG, fg=TEXT, font=("Segoe UI", 10),
                  relief=tk.FLAT, padx=6, pady=2,
                  cursor="hand2").pack(side=tk.LEFT)

    def _build_status_bar(self):
        bar = tk.Frame(self, bg=PANEL_BG, pady=4)
        bar.pack(fill=tk.X)

        self._status_lbl = tk.Label(bar, text="● Disconnected",
                                    bg=PANEL_BG, fg=ERR_RED,
                                    font=("Segoe UI", 9))
        self._status_lbl.pack(side=tk.LEFT, padx=12)

        self._ts_lbl = tk.Label(bar, text="Last update: —",
                                bg=PANEL_BG, fg=TEXT_DIM,
                                font=("Segoe UI", 9))
        self._ts_lbl.pack(side=tk.RIGHT, padx=12)

    def _build_channel_panel(self, parent, title, row, col):
        frame = tk.LabelFrame(parent, text=f"  {title}  ",
                              bg=PANEL_BG, fg=ACCENT,
                              font=("Segoe UI", 10, "bold"),
                              bd=1, relief=tk.GROOVE,
                              labelanchor="n")
        frame.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)

        labels_map = {}
        fields = [
            ("Bus Voltage",        "busV",         "V"),
            ("Shunt Voltage",      "shuntV",       "V"),
            ("Current",            "current",      "A"),
            ("Power",              "power",        "W"),
            ("Supply Voltage",     "supplyV",      "V"),
            ("Load Resistance",    "loadR",        "Ω"),
            ("Shunt Resistance",   "shuntR",       "Ω"),
            ("Recomputed Power",   "recomputedP",  "W"),
            ("Voltage Drop Ratio", "dropRatio",    ""),
            ("Current Density",    "currentDensity","A/V"),
            ("Conductance",        "conductance",  "S"),
        ]

        for i, (label, key, unit) in enumerate(fields):
            bg = PANEL_BG if i % 2 == 0 else HEADER_BG
            row_f = tk.Frame(frame, bg=bg)
            row_f.pack(fill=tk.X, padx=4, pady=1)

            tk.Label(row_f, text=label, bg=bg, fg=TEXT_DIM,
                     font=("Segoe UI", 9), width=22,
                     anchor="w").pack(side=tk.LEFT, padx=(6,0))

            val_lbl = tk.Label(row_f, text="—", bg=bg, fg=TEXT,
                               font=("Consolas", 9, "bold"), width=18,
                               anchor="e")
            val_lbl.pack(side=tk.RIGHT, padx=(0,6))

            if unit:
                tk.Label(row_f, text=unit, bg=bg, fg=TEXT_DIM,
                         font=("Segoe UI", 8), width=4,
                         anchor="w").pack(side=tk.RIGHT)

            labels_map[key] = val_lbl

        return labels_map

    def _build_system_panel(self, parent, row):
        frame = tk.LabelFrame(parent, text="  ⚡  System Analysis  ",
                              bg=PANEL_BG, fg=ACCENT,
                              font=("Segoe UI", 10, "bold"),
                              bd=1, relief=tk.GROOVE,
                              labelanchor="n")
        frame.grid(row=row, column=0, columnspan=2,
                   sticky="nsew", padx=6, pady=6)

        frame.columnconfigure(list(range(7)), weight=1)

        metrics = [
            ("Input Power",    "inputPower",  "W"),
            ("Output Power",   "outputPower", "W"),
            ("Power Error",    "powerError",  "W"),
            ("Efficiency",     "effPct",      "%"),
            ("Current Diff",   "currentDiff", "A"),
            ("Eff. Ratio",     "effRatio",    ""),
            ("Theft Alert",    "theftAlert",  ""),
        ]

        self._sys_labels = {}
        for col_i, (label, key, unit) in enumerate(metrics):
            cell = tk.Frame(frame, bg=PANEL_BG, bd=0)
            cell.grid(row=0, column=col_i, sticky="nsew", padx=8, pady=10)

            tk.Label(cell, text=label, bg=PANEL_BG, fg=TEXT_DIM,
                     font=("Segoe UI", 8)).pack()

            lbl = tk.Label(cell, text="—", bg=PANEL_BG, fg=TEXT,
                           font=("Consolas", 11, "bold"))
            lbl.pack()

            if unit:
                tk.Label(cell, text=unit, bg=PANEL_BG, fg=TEXT_DIM,
                         font=("Segoe UI", 8)).pack()

            self._sys_labels[key] = lbl

        # big theft alert banner
        self._alert_banner = tk.Label(frame,
                                      text="",
                                      bg=PANEL_BG,
                                      font=("Segoe UI", 11, "bold"),
                                      pady=4)
        self._alert_banner.grid(row=1, column=0, columnspan=7,
                                sticky="ew", padx=8, pady=(0,8))

    # ──────────────────────── serial connection ────────────────────────────
    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self._port_combo["values"] = ports
        if ports:
            self._port_var.set(ports[0])

    def _toggle_connection(self):
        if self._running:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        port = self._port_var.get()
        baud = int(self._baud_var.get())
        if not port:
            messagebox.showwarning("No Port", "Please select a serial port.")
            return
        try:
            self._serial = serial.Serial(port, baud, timeout=2)
            self._running = True
            self._reader_thread = threading.Thread(
                target=self._read_loop, daemon=True)
            self._reader_thread.start()
            self._connect_btn.config(text="Disconnect", bg=ERR_RED)
            self._status_lbl.config(text=f"● Connected  {port} @ {baud}",
                                    fg=OK_GREEN)
        except serial.SerialException as e:
            messagebox.showerror("Connection Error", str(e))

    def _disconnect(self):
        self._running = False
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None
        self._connect_btn.config(text="Connect", bg=ACCENT)
        self._status_lbl.config(text="● Disconnected", fg=ERR_RED)

    def _read_loop(self):
        """Background thread: read lines from serial and enqueue snapshots."""
        while self._running and self._serial and self._serial.is_open:
            try:
                raw = self._serial.readline()
                line = raw.decode("utf-8", errors="replace")
                snapshot = self._parser.feed(line)
                if snapshot:
                    self._queue.put(snapshot)
            except serial.SerialException:
                self._running = False
                self._queue.put({"error": "Serial connection lost."})
                break

    # ─────────────────────────── UI updates ───────────────────────────────
    def _start_polling(self):
        self._poll()

    def _poll(self):
        try:
            while True:
                item = self._queue.get_nowait()
                if "error" in item:
                    self._disconnect()
                    messagebox.showerror("Serial Error", item["error"])
                else:
                    self._update_display(item)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _update_display(self, snap: dict):
        self._ts_lbl.config(text=f"Last update: {snap['ts']}")

        # channel panels
        for ch_key in ("ch1", "ch2"):
            data = snap.get(ch_key, {})
            panel = self._panels[ch_key]
            for key, lbl in panel.items():
                val = data.get(key)
                if val is None:
                    lbl.config(text="—", fg=TEXT)
                else:
                    lbl.config(text=f"{val:.6f}", fg=TEXT)

        # system panel
        sdata = snap.get("sys", {})
        for key, lbl in self._sys_labels.items():
            val = sdata.get(key)
            if val is None:
                lbl.config(text="—", fg=TEXT)
                continue

            if key == "theftAlert":
                is_theft = (str(val).strip().upper() == "YES" or val == 1.0)
                lbl.config(text="YES" if is_theft else "NO",
                           fg=ERR_RED if is_theft else OK_GREEN)
            elif key == "powerError":
                lbl.config(text=f"{val:.4f}",
                           fg=ERR_RED if val > 0.5 else OK_GREEN)
            elif key == "effPct":
                color = OK_GREEN if val >= 90 else (WARN_YELLOW if val >= 70 else ERR_RED)
                lbl.config(text=f"{val:.2f}", fg=color)
            elif key == "currentDiff":
                lbl.config(text=f"{val:.6f}",
                           fg=WARN_YELLOW if abs(val) > 0.05 else TEXT)
            else:
                lbl.config(text=f"{val:.6f}", fg=TEXT)

        # banner
        theft_val = sdata.get("theftAlert")
        if theft_val is not None:
            is_theft = (str(theft_val).strip().upper() == "YES" or theft_val == 1.0)
            if is_theft:
                self._alert_banner.config(
                    text="⚠  POWER THEFT DETECTED  ⚠",
                    bg=ERR_RED, fg="white")
            else:
                self._alert_banner.config(
                    text="✔  System Normal – No Theft Detected",
                    bg="#1e3a2f", fg=OK_GREEN)

    # ──────────────────────────── close ───────────────────────────────────
    def on_close(self):
        self._disconnect()
        self.destroy()


# ───────────────────────────── demo / mock mode ────────────────────────────
import math, time, random

class MockSerial:
    """
    Simulates Arduino serial output so the dashboard can be demoed
    without real hardware. Pass --demo on the command line.
    """
    def __init__(self):
        self._buf = b""
        self._t = 0.0
        self.is_open = True

    def close(self): self.is_open = False

    def readline(self) -> bytes:
        lines = self._generate_block()
        time.sleep(0.01)
        return lines.pop(0).encode() + b"\n"

    def _generate_block(self):
        t = self._t
        self._t += 0.1

        busV1  = 12.0 + 0.1 * math.sin(t)
        curr1  = 1.5  + 0.05 * math.sin(t * 0.7)
        busV2  = 11.8 + 0.08 * math.sin(t + 0.3)
        curr2  = 1.45 + 0.04 * math.sin(t * 0.7 + 0.2)
        shunt1 = curr1 * 0.1
        shunt2 = curr2 * 0.1
        p1 = busV1 * curr1
        p2 = busV2 * curr2
        theft = random.random() < 0.1   # 10 % chance of "theft"

        def ch_lines(ch_n, bv, sv, cu, p, sup):
            lr  = bv / cu if cu else 0
            sr  = sv / cu if cu else 0
            rp  = sup * cu
            dr  = sv / bv if bv else 0
            cd  = cu / bv if bv else 0
            cnd = 1/lr   if lr else 0
            return [
                f"\nCHANNEL {ch_n} ({'Input' if ch_n==1 else 'Output'} Side)",
                "=====================================",
                f"CHANNEL: {ch_n}",
                f"1. Bus Voltage (V): {bv:.4f}",
                f"2. Shunt Voltage (V): {sv:.6f}",
                f"3. Current (A): {cu:.6f}",
                f"4. Power (W): {p:.6f}",
                f"5. Supply Voltage (V): {sup:.6f}",
                f"6. Load Resistance (Ohm): {lr:.6f}",
                f"7. Shunt Resistance (Ohm): {sr:.6f}",
                f"8. Recomputed Power (W): {rp:.6f}",
                f"9. Voltage Drop Ratio: {dr:.8f}",
                f"10. Current Density: {cd:.8f}",
                f"11. Conductance (S): {cnd:.8f}",
            ]

        eff = min(1.0, p2/p1) if p1 else 0
        diff = curr1 - curr2
        perr = p1 - p2

        lines = (
            ch_lines(1, busV1, shunt1, curr1, p1, busV1+shunt1) +
            ch_lines(2, busV2, shunt2, curr2, p2, busV2+shunt2) +
            [
                "\n========== System Analysis ==========",
                f"Input Power (CH1) (W): {p1:.6f}",
                f"Output Power (CH2) (W): {p2:.6f}",
                f"Power Error (W): {perr:.6f}",
                f"Efficiency Ratio: {eff:.6f}",
                f"Efficiency (%): {eff*100:.2f}",
                f"Current Difference (CH1-CH2): {diff:.6f}",
                f"Theft Alert: {'YES' if theft else 'NO'}",
                "=====================================",
            ]
        )
        return lines


class DemoApp(App):
    """App subclass that injects a MockSerial instead of a real port."""

    def _connect(self):
        self._serial = MockSerial()
        self._running = True
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True)
        self._reader_thread.start()
        self._connect_btn.config(text="Disconnect", bg=ERR_RED)
        self._status_lbl.config(
            text="● Demo Mode (simulated data)", fg=WARN_YELLOW)


# ──────────────────────────────── entry point ──────────────────────────────
if __name__ == "__main__":
    import sys
    demo_mode = "--demo" in sys.argv

    AppClass = DemoApp if demo_mode else App
    app = AppClass()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
