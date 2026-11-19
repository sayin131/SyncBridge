"""
watcher.py — watches SYNC_ROOT for changes and notifies connected phones over the websocket.

Editors often fire several filesystem events per save (create, modify, modify again).
We debounce: wait `DEBOUNCE_SECONDS` of silence on a path before announcing it, so we
don't spam the phone with five events for one save.

watchdog callbacks run on their own thread, not the asyncio event loop, so we hand off
to the loop with run_coroutine_threadsafe.
"""
import asyncio
import threading
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

DEBOUNCE_SECONDS = 2.0


class DebouncedHandler(FileSystemEventHandler):
    def __init__(self, loop: asyncio.AbstractEventLoop, on_settled):
        self._loop = loop
        self._on_settled = on_settled  # async callback(path: str)
        self._pending: dict[str, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def _touch(self, path: str):
        if path.endswith(".part"):
            return
        with self._lock:
            self._pending[path] = time.time()

    def on_created(self, event):
        if not event.is_directory:
            self._touch(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._touch(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._touch(event.dest_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self._touch(event.src_path)

    def _run(self):
        while not self._stop.is_set():
            now = time.time()
            settled = []
            with self._lock:
                for path, last_seen in list(self._pending.items()):
                    if now - last_seen >= DEBOUNCE_SECONDS:
                        settled.append(path)
                        del self._pending[path]
            for path in settled:
                asyncio.run_coroutine_threadsafe(self._on_settled(path), self._loop)
            time.sleep(0.5)

    def stop(self):
        self._stop.set()


def start_watching(root: str, loop: asyncio.AbstractEventLoop, on_settled) -> Observer:
    handler = DebouncedHandler(loop, on_settled)
    observer = Observer()
    observer.schedule(handler, root, recursive=True)
    observer.start()
    return observer
