import json, subprocess, sys
data = json.load(open('app/dockerstage/data/knowledge_base.json'))
failed = []
for entry in data['entries']:
    name = entry['name']
    imports = entry.get('import_names', [])
    if not imports: continue
    # install
    print(f"Testing {name}...", flush=True)
    res = subprocess.run([sys.executable, "-m", "pip", "install", "-q", name], capture_output=True)
    if res.returncode != 0:
        print(f"Could not install {name}, skipping.")
        continue
    # import
    import_cmd = "; ".join(f"import {m}" for m in imports)
    res2 = subprocess.run([sys.executable, "-c", import_cmd], capture_output=True, text=True)
    if res2.returncode != 0:
        print(f"FAILED IMPORT {name}: {res2.stderr.strip()}")
        failed.append(name)
    else:
        print(f"OK {name}")
print("FAILED:", failed)
