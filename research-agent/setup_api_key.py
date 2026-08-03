import os, yaml
key = os.environ.get("DEEPSEEK_API_KEY", "")
if not key:
    key = "__MISSING__"
    print("WARNING: DEEPSEEK_API_KEY not set in environment")

path = os.path.expanduser("~/research-agent-data/config.yml")
cfg = yaml.safe_load(open(path, encoding="utf-8"))
cfg["model"]["api_key"] = key
yaml.dump(cfg, open(path, "w", encoding="utf-8"), allow_unicode=True)
print(f"Config written: key present={key != '__MISSING__'}, len={len(key)}")
