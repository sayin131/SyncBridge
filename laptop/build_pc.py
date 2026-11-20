"""
build_pc.py — packages the desktop app into a single executable with PyInstaller.

    pip install pyinstaller
    python build_pc.py

Output: dist/SyncBridge(.exe). Build on each OS you want to ship for (PyInstaller does not
cross-compile). On Windows, allow the app through the firewall for *Private* networks the
first time it runs, otherwise the phone can't reach it.
"""
import PyInstaller.__main__

PyInstaller.__main__.run([
    "app_gui.py",
    "--name", "SyncBridge",
    "--onefile",
    "--windowed",
    "--noconfirm",
    "--collect-submodules", "uvicorn",
    "--collect-submodules", "watchdog",
    "--collect-submodules", "anyio",
    "--collect-submodules", "zeroconf",
    "--hidden-import", "websockets",
    "--hidden-import", "PIL.ImageTk",
])
