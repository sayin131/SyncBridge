import sys, time
import db
from scanner import scan 

root = sys.argv[1]
conn = db.connect()
db.init_db(conn)

cache = db.load_manifest(conn)      # previous state from disk 
t = time.time()
manifest = scan(root,cache=cache)
print(f"Scan took {time.time()-t:.2f}s, {len(manifest)} files")
db.save_manifest(conn,manifest)
