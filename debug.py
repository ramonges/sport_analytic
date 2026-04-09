import json, os, glob

clv_dir = "data/clv"
files = glob.glob(f"{clv_dir}/*.json")
if not files:
    print("NO CLV FILES FOUND in", clv_dir)
else:
    print(f"Found {len(files)} CLV files")
    with open(files[0]) as f:
        data = json.load(f)
    print("\n=== TOP KEYS ===")
    print(list(data.keys()))
    print("\n=== clv_data keys (if present) ===")
    clv = data.get("clv_data", data)
    print(type(clv), list(clv.keys())[:10] if isinstance(clv, dict) else "list len=" + str(len(clv)))
    print("\n=== First 2000 chars ===")
    print(json.dumps(data, indent=2)[:2000])

    