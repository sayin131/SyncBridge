"""
scanner.py — walks a folder and produces a manifest of {relative_path: {size, mtime, sha256}}

Hashing is skipped for files whose size+mtime match a previous scan (passed in as `cache`),
so repeated scans of a large, mostly-unchanged folder are fast.
"""
import hashlib
import os

BLOCK = 1024 * 1024  # 1 MB


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(BLOCK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def scan(root: str, cache: dict | None = None) -> dict:
    """
    Returns {relative_path: {"size": int, "mtime": float, "sha256": str}}
    Paths always use "/" separators so laptop and phone manifests are comparable.
    """
    cache = cache or {}
    manifest = {}
    root = os.path.abspath(root)

    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.endswith(".part"):
                continue  # in-progress transfer, never part of the synced state
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            try:
                st = os.stat(full)
            except OSError:
                continue

            old = cache.get(rel)
            if old and old["size"] == st.st_size and abs(old["mtime"] - st.st_mtime) < 1e-6:
                digest = old["sha256"]
            else:
                digest = sha256_file(full)

            manifest[rel] = {
                "size": st.st_size,
                "mtime": st.st_mtime,
                "sha256": digest,
            }
    return manifest


def safe_join(root: str, rel_path: str) -> str:
    """
    Resolve rel_path under root, raising ValueError if it would escape root
    (blocks path traversal like '../../etc/passwd').
    """
    root_abs = os.path.abspath(root)
    candidate = os.path.abspath(os.path.join(root_abs, rel_path))
    if not (candidate == root_abs or candidate.startswith(root_abs + os.sep)):
        raise ValueError(f"path escapes sync root: {rel_path}")
    return candidate


if __name__ == "__main__":
    import json
    import sys
    import time

    root = sys.argv[1] if len(sys.argv) > 1 else "."
    t0 = time.time()
    first = scan(root)
    print(f"First scan: {time.time() - t0:.3f}s, {len(first)} files")
    t0 = time.time()
    second = scan(root, cache=first)
    print(f"Cached scan: {time.time() - t0:.3f}s")
    print(json.dumps(second, indent=2))
