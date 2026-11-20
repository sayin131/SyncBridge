"""
app_gui.py — SyncBridge desktop app (Windows / macOS / Linux).

A small window that runs the sync server in the background and lets you:
  - choose the folder to sync
  - pair a phone by scanning a QR code
  - see paired devices (and unpair them)
  - watch recent sync activity

Run:    python app_gui.py
Build:  see build_pc.py (PyInstaller -> single executable)
"""
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ---- per-user data dir; must be configured BEFORE importing main ----
if sys.platform.startswith("win"):
    APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "SyncBridge")
elif sys.platform == "darwin":
    APP_DIR = os.path.expanduser("~/Library/Application Support/SyncBridge")
else:
    APP_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "SyncBridge")
os.makedirs(APP_DIR, exist_ok=True)

CONFIG_PATH = os.path.join(APP_DIR, "config.json")
CERT_PATH = os.path.join(APP_DIR, "cert.pem")
KEY_PATH = os.path.join(APP_DIR, "key.pem")


def load_config() -> dict:
    cfg = {"root": os.path.join(os.path.expanduser("~"), "SyncBridge"), "port": 8443}
    try:
        with open(CONFIG_PATH) as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


cfg = load_config()
os.makedirs(cfg["root"], exist_ok=True)
os.environ["SYNCBRIDGE_DB"] = os.path.join(APP_DIR, "syncbridge.db")
os.environ["SYNCBRIDGE_ROOT"] = cfg["root"]

import uvicorn  # noqa: E402

import db  # noqa: E402
import discovery  # noqa: E402
import main as server_main  # noqa: E402
import pairing_qr  # noqa: E402
import security  # noqa: E402

try:
    import qrcode
    from PIL import ImageTk
except ImportError:  # QR image is optional; the payload text is still shown
    qrcode = None
    ImageTk = None

PAIR_TTL = 120


class ServerController:
    """Runs uvicorn (HTTPS) in a background thread so the GUI stays responsive."""

    def __init__(self):
        self.server = None
        self.thread = None
        self.advertiser = discovery.Advertiser()

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, port: int, ip: str):
        cert, key = security.ensure_cert(CERT_PATH, KEY_PATH)
        config = uvicorn.Config(server_main.app, host="0.0.0.0", port=port,
                                ssl_certfile=cert, ssl_keyfile=key,
                                log_config=None)  # log_config=None: no console in --windowed builds
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        self.advertiser.start(ip, port)

    def stop(self):
        self.advertiser.stop()
        if self.server:
            self.server.should_exit = True
        if self.thread:
            self.thread.join(timeout=5)
        self.server = self.thread = None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SyncBridge")
        self.geometry("760x720")
        self.minsize(700, 640)

        self.ctrl = ServerController()
        self.conn = db.connect(os.environ["SYNCBRIDGE_DB"])
        db.init_db(self.conn)
        self.pair_expires = 0.0
        self.qr_photo = None

        self.ip_var = tk.StringVar(value=security.get_local_ip())
        self.port_var = tk.StringVar(value=str(cfg["port"]))
        self.root_var = tk.StringVar(value=cfg["root"])
        self.status_var = tk.StringVar(value="Stopped")
        self.countdown_var = tk.StringVar(value="")
        self.fp_var = tk.StringVar(value="")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.start_server()
        self.after(1500, self.refresh)

    # ---------- UI ----------

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="SyncBridge", font=("TkDefaultFont", 16, "bold")).pack(side="left")
        self.toggle_btn = ttk.Button(top, text="Stop server", command=self.toggle_server)
        self.toggle_btn.pack(side="right")
        ttk.Label(top, textvariable=self.status_var).pack(side="right", padx=12)

        folder = ttk.LabelFrame(self, text="Sync folder")
        folder.pack(fill="x", **pad)
        ttk.Entry(folder, textvariable=self.root_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=8, pady=8)
        ttk.Button(folder, text="Change…", command=self.choose_folder).pack(side="right", padx=8)
        ttk.Button(folder, text="Open", command=self.open_folder).pack(side="right")

        pair = ttk.LabelFrame(self, text="Pair a phone")
        pair.pack(fill="x", **pad)
        left = ttk.Frame(pair)
        left.pack(side="left", padx=8, pady=8)
        self.qr_label = ttk.Label(left, text="(QR appears here)", width=34, anchor="center")
        self.qr_label.pack()
        right = ttk.Frame(pair)
        right.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        row = ttk.Frame(right)
        row.pack(fill="x")
        ttk.Label(row, text="IP").pack(side="left")
        ttk.Entry(row, textvariable=self.ip_var, width=16).pack(side="left", padx=(4, 12))
        ttk.Label(row, text="Port").pack(side="left")
        ttk.Entry(row, textvariable=self.port_var, width=6).pack(side="left", padx=4)
        ttk.Label(right, text="If your PC has several networks (VPN, Ethernet), set the IP your "
                              "phone's Wi-Fi can reach.", wraplength=330, foreground="#666").pack(
            anchor="w", pady=(4, 8))
        ttk.Button(right, text="Generate new pairing code", command=self.new_pairing).pack(anchor="w")
        ttk.Label(right, textvariable=self.countdown_var).pack(anchor="w", pady=6)
        ttk.Label(right, text="Certificate fingerprint (SHA-256):").pack(anchor="w")
        ttk.Label(right, textvariable=self.fp_var, wraplength=330, font=("TkFixedFont", 8)).pack(anchor="w")

        dev = ttk.LabelFrame(self, text="Paired devices")
        dev.pack(fill="x", **pad)
        self.dev_tree = ttk.Treeview(dev, columns=("name", "id", "seen"), show="headings", height=3)
        for col, title, w in (("name", "Name", 200), ("id", "Device ID", 200), ("seen", "Last seen", 160)):
            self.dev_tree.heading(col, text=title)
            self.dev_tree.column(col, width=w)
        self.dev_tree.pack(side="left", fill="x", expand=True, padx=8, pady=8)
        ttk.Button(dev, text="Unpair", command=self.unpair).pack(side="right", padx=8)

        hist = ttk.LabelFrame(self, text="Recent activity")
        hist.pack(fill="both", expand=True, **pad)
        self.hist_tree = ttk.Treeview(hist, columns=("time", "device", "action", "status", "path"),
                                      show="headings")
        for col, title, w in (("time", "Time", 70), ("device", "Device", 110), ("action", "Action", 100),
                              ("status", "Status", 90), ("path", "File", 300)):
            self.hist_tree.heading(col, text=title)
            self.hist_tree.column(col, width=w)
        self.hist_tree.pack(fill="both", expand=True, padx=8, pady=8)

    # ---------- actions ----------

    def port(self) -> int:
        try:
            return int(self.port_var.get())
        except ValueError:
            return 8443

    def start_server(self):
        try:
            self.ctrl.start(self.port(), self.ip_var.get())
        except Exception as e:
            messagebox.showerror("SyncBridge", f"Could not start server:\n{e}")
            return
        cfg["port"] = self.port()
        save_config(cfg)
        self.fp_var.set(security.cert_fingerprint(CERT_PATH))
        self.status_var.set(f"Running on port {self.port()}")
        self.toggle_btn.config(text="Stop server")
        self.new_pairing()

    def toggle_server(self):
        if self.ctrl.running:
            self.ctrl.stop()
            self.status_var.set("Stopped")
            self.toggle_btn.config(text="Start server")
        else:
            self.start_server()

    def choose_folder(self):
        was_running = self.ctrl.running
        path = filedialog.askdirectory(initialdir=self.root_var.get(), title="Choose sync folder")
        if not path:
            return
        os.makedirs(path, exist_ok=True)
        if was_running:
            self.ctrl.stop()
        server_main.SYNC_ROOT = os.path.abspath(path)
        os.environ["SYNCBRIDGE_ROOT"] = server_main.SYNC_ROOT
        cfg["root"] = server_main.SYNC_ROOT
        save_config(cfg)
        self.root_var.set(server_main.SYNC_ROOT)
        if was_running:
            self.start_server()

    def open_folder(self):
        path = self.root_var.get()
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')

    def new_pairing(self):
        payload = pairing_qr.make_pairing_payload(self.conn, self.ip_var.get(), self.port(),
                                                  CERT_PATH, PAIR_TTL)
        text = json.dumps(payload)
        self.pair_expires = time.time() + PAIR_TTL
        if qrcode is not None and ImageTk is not None:
            img = qrcode.make(text).get_image().resize((240, 240))
            self.qr_photo = ImageTk.PhotoImage(img)
            self.qr_label.config(image=self.qr_photo, text="")
        else:
            self.qr_label.config(image="", text="Install 'qrcode[pil]' to show a QR.\n\n" + text)

    def unpair(self):
        sel = self.dev_tree.selection()
        if not sel:
            return
        device_id = self.dev_tree.item(sel[0])["values"][1]
        if messagebox.askyesno("Unpair", f"Unpair {device_id}? It will need to scan a new QR code."):
            db.delete_device(self.conn, str(device_id))

    def refresh(self):
        # countdown
        remaining = int(self.pair_expires - time.time())
        if remaining > 0:
            self.countdown_var.set(f"Code valid for {remaining}s (one use, 3 attempts)")
        elif self.ctrl.running:
            self.countdown_var.set("Code expired. Generate a new one.")

        def fmt(ts):
            return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "-"

        self.dev_tree.delete(*self.dev_tree.get_children())
        for r in db.list_devices(self.conn):
            self.dev_tree.insert("", "end", values=(r["device_name"], r["device_id"], fmt(r["last_seen"])))
        self.hist_tree.delete(*self.hist_tree.get_children())
        for r in db.recent_history(self.conn, 100):
            self.hist_tree.insert("", "end", values=(fmt(r["timestamp"]), r["device_id"], r["action"],
                                                     r["status"], r["path"]))
        self.after(2000, self.refresh)

    def on_close(self):
        if self.ctrl.running:
            self.ctrl.stop()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
