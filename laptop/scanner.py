import hashlib
import os 

BLOCK = 1024 * 1024 # 1 MB

def sha256_file(path):
    h = hashlib.sha256()
    with open(path,"rb") as f:
        while block := f.read(BLOCK):
            h.update(block)
    return h.hexdigest()

def scan(root,cache=None):
    """Return {releative_path: {Size,mtime,sha256}},
    cache is the previous scan result.used to skip unchanges files."""
    cache = cache or {}
    manifest = {}
    for dirpath, _, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath,name)
            rel = os.path.relpath(full,root).replace(os.sep,"/")
            st = os.stat(full)
            old = cache.get(rel)
            if old and old["size"] == st.st_size and old["mtime"] == st.st_mtime:
                digest = old["sha256"] # unchanges. skip hashing
            else:
                digest = sha256_file(full)
            manifest[rel] = {"size": st.st_size,"mtime": st.st_mtime, "sha256": digest}
    return manifest

if __name__ == "__main__":
    import json, sys, time
    root = sys.argv[1]
    t = time.time()
    first = scan(root)
    print(f"First scan: {time.time() - t:.2f}s")
    t = time.time()
    second = scan(root,cache=first)
    print(f"Second scan: {time.time() - t:.2f}s")
    print(json.dumps(second,indent=2))
