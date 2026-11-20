# SyncBridge

Secure local-network file sync between a PC and an Android phone. No cloud, no WhatsApp or
Telegram, and nothing leaves your Wi-Fi.

```
SyncBridge/
├── laptop/       PC app: desktop GUI + FastAPI/HTTPS server (Python)
├── android/      Android app: Kotlin + Jetpack Compose (open in Android Studio)
├── phone_sim/    Command-line stand-in for the phone, for testing without Android
└── protocol/     Protocol spec and threat model
```

## Verification status (read this first)

I wrote all of this in a sandbox with no internet, no Android SDK, no Kotlin compiler and no
tkinter, so **what is verified is limited**:

| Part | Status |
|---|---|
| Sync planner (`planner.py`), scanner, SQLite layer, cert generation | **Tested**: 24 unit tests + manual DB/cert checks pass |
| All Python files | **Compile cleanly** (`py_compile`) |
| FastAPI server (`main.py`), desktop GUI (`app_gui.py`), `phone_sim` | **Not run**: `fastapi`, `uvicorn`, `tkinter` weren't available here |
| Android app (`android/`) | **Never compiled or run.** Expect a few small compile errors on first build (imports, version mismatches). Nothing here could be checked against a real compiler |

So: the hard logic is tested, the integration code is untested. Budget an hour or two of
fixing on first run. Do the steps below in order, so the PC side is proven before you debug
the Android side.

## 1. PC app

```bash
cd laptop
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
pytest tests/ -v                 # should show 24 passed
python app_gui.py
```

The window shows a QR code, the sync folder (default `~/SyncBridge`, change it with the button),
paired devices, and recent activity. On Windows, allow Python through the firewall for
**Private networks** when prompted, or the phone can't connect. If the PC has several networks
(VPN, Ethernet), set the IP field to the address your phone's Wi-Fi can reach.

Build a single executable (run on each OS you want to ship for):
```bash
pip install pyinstaller && python build_pc.py      # -> dist/SyncBridge(.exe)
```
(Headless alternative: `python run_server.py --root ./sync_root`.)

## 2. Test with the phone simulator (recommended before Android)

```bash
cd phone_sim && pip install -r requirements.txt
python client.py pair --ip <ip> --port 8443 --fp <fingerprint> --secret <secret> --root ./phone_root
python client.py sync
```
Use the values printed by `run_server.py` (the GUI shows only a QR, so the headless runner is
easier for this). Try: add files on each side, edit the same file on both, delete a file, kill
the server mid-upload of a big file and sync again.

## 3. Android app

1. Install **Android Studio** (Koala or newer) and open the `android/` folder. Let Gradle sync.
2. Enable USB debugging on the phone, connect it, press **Run**. (Or *Build > Build APK*.)
3. Phone and PC on the same Wi-Fi. In the app: **Scan QR to pair**, then **Choose folder**, then **Sync now**.

How it works on the phone: the pinned-certificate check happens inside the TLS handshake
(`net/Pinning.kt`); pairing credentials live in Keystore-encrypted storage (`data/Store.kt`);
the folder is accessed via the Storage Access Framework (`sync/SafTree.kt`); sync runs in
WorkManager (`sync/SyncWorker.kt`), every ~15 min on Wi-Fi plus instantly via a WebSocket
while the app is open (`sync/LiveEvents.kt`); if the PC's IP changes it re-finds it via mDNS
(`net/Discovery.kt`). `sync/SyncEngine.kt` is the Kotlin port of `phone_sim/client.py`.

## Behaviour worth knowing

- **Conflicts never lose data.** Both edited: the PC's version keeps the name, yours becomes
  `name (phone conflict …).ext`, and both sync everywhere. Delete-vs-edit: the edit wins.
- **Transfers resume.** 4 MB chunks; partial files are keyed by the file's hash, so a resume
  can't mix versions. Downloads are hash-verified before being placed in your folder.
- **iOS is not supported.** Android only.
- **Android limits:** no permanent background connection, so closed-app sync is periodic
  (15-minute OS minimum). Writing into the SAF folder isn't atomic like on the PC side.
- **Not done yet:** rename detection (a move = delete + new file, re-transferred), bandwidth
  limits, ignore patterns, system-tray icon, a release-signed APK.

## Security summary

TLS with a self-signed certificate pinned by SHA-256 fingerprint delivered via QR (defeats
man-in-the-middle on shared Wi-Fi); one-time pairing secret (120 s, 3 attempts); 256-bit device
tokens stored only as hashes on the PC; all paths confined to the sync root; every file verified
by SHA-256; WebSocket requires the same token. Full details and non-goals: `protocol/protocol.md`.
