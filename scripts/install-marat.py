#!/usr/bin/env python3
"""Stage or activate the fork in an existing Linux Electron carrier; never run a task."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess

os.umask(0o077)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--carrier", type=Path, required=True)
parser.add_argument("--backup-root", type=Path, required=True)
parser.add_argument("--core-home", type=Path, default=Path.home()/".codex-chatgpt-web")
parser.add_argument("--cron-db", type=Path, required=True)
parser.add_argument("--project-url", required=True)
parser.add_argument("--launcher-service", default="codexweb-launcher.service")
parser.add_argument("--scheduler-service", default="hermes-scheduler.service")
parser.add_argument("--activate", action="store_true")
args = parser.parse_args()
source = Path(__file__).resolve().parent.parent
carrier = args.carrier.resolve(strict=True)
core = args.core_home.resolve(strict=True)
stage = source/"dist/marat-carrier"
version = json.loads((source/"package.json").read_text())["version"]
commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
if not re.fullmatch(r"https://chatgpt\.com/g/g-p-[a-zA-Z0-9-]+/project/?", args.project_url):
    raise SystemExit("Expected an HTTPS ChatGPT project landing-page URL")
project_url = args.project_url.rstrip("/")
if "-marat." not in version or not (carrier/"codex-web-gpt-launcher").is_file():
    raise SystemExit("This installer requires a maintained build and the existing Linux carrier")
if carrier == stage or source in carrier.parents or carrier in source.parents:
    raise SystemExit("Carrier and source must be separate directories")
manifest = json.loads((source/"dist/runtime/manifest.json").read_text())
if manifest["appVersion"] != version or not (source/"dist/marat-app.asar").is_file():
    raise SystemExit("Build the maintained runtime and application first")
provenance = {"version": version, "commit": commit, "repository": "MaratG2/codex-chatgpt-web"}

if not stage.exists():
    shutil.copytree(carrier, stage, symlinks=True)
    resources = stage/"resources"
    shutil.rmtree(resources/"runtime")  # Fixed child of our newly-created staging directory.
    shutil.copytree(source/"dist/runtime", resources/"runtime", symlinks=True)
    shutil.copy2(source/"dist/marat-app.asar", resources/"app.asar")
    (resources/"marat-build.json").write_text(json.dumps(provenance, indent=2)+"\n")
elif json.loads((stage/"resources/marat-build.json").read_text()) != provenance:
    raise SystemExit("A different staged build exists; preserve or move it before staging again")
if not args.activate:
    print(json.dumps({"status": "staged", "stage": str(stage), **provenance}))
    raise SystemExit(0)

def systemctl(*arguments, check=True):
    return subprocess.run(["systemctl", "--user", *arguments], check=check,
                          capture_output=True, text=True)

def assert_idle():
    workers = systemctl("list-units", "--type=scope", "--state=active", "--no-legend", "hermes-worker-*").stdout
    if workers.strip():
        raise RuntimeError("Hermes workers are active; leave the staged build and wait for them")
    with sqlite3.connect(f"file:{args.cron_db.resolve(strict=True)}?mode=ro", uri=True) as db:
        if db.execute("SELECT 1 FROM executions WHERE status='running' LIMIT 1").fetchone():
            raise RuntimeError("A cron execution is still marked running; do not interrupt it")

assert_idle()
scheduler_active = systemctl("is-active", "--quiet", args.scheduler_service, check=False).returncode == 0
launcher_active = systemctl("is-active", "--quiet", args.launcher_service, check=False).returncode == 0
backup = args.backup_root.resolve()/datetime.now(timezone.utc).strftime("before-marat-fork-%Y%m%dT%H%M%SZ")
backup.mkdir(parents=True, mode=0o700)
saved = []
moved = False
try:
    if scheduler_active:
        systemctl("stop", args.scheduler_service)
    assert_idle()
    systemctl("stop", args.launcher_service)
    # Preserve private configuration before the launcher's normal upgrade migrates its paths/hooks.
    originals = [core/"config.json", Path.home()/".codex/config.toml",
                 *sorted((core/"codex").glob("*.json")), *sorted((core/"tunnel/profiles").glob("*.yaml"))]
    for i, original in enumerate(originals):
        if original.is_file():
            copy = backup/f"private-{i}"
            shutil.copy2(original, copy)
            saved.append((original, copy))
    (backup/"restore-map.json").write_text(json.dumps([[str(a), str(b)] for a,b in saved], indent=2)+"\n")
    os.replace(carrier, backup/"application")
    moved = True
    os.replace(stage, carrier)
    config_path = core/"config.json"
    config = json.loads(config_path.read_text())
    config.update(chatgptProjectUrl=project_url, useSavedChats=True,
                  experimentalFreshConversationPerTurn=False)
    temp = config_path.with_suffix(".marat-tmp")
    temp.write_text(json.dumps(config, indent=2)+"\n")
    os.replace(temp, config_path)
    if launcher_active:
        systemctl("start", args.launcher_service)
except BaseException:
    if moved:
        systemctl("stop", args.launcher_service, check=False)
        if carrier.exists():
            os.replace(carrier, backup/"failed-application")
        os.replace(backup/"application", carrier)
        for original, copy in saved:
            shutil.copy2(copy, original)
        if launcher_active:
            systemctl("start", args.launcher_service, check=False)
    raise
finally:
    if scheduler_active:
        systemctl("start", args.scheduler_service)
result = {"status": "installed", **provenance, "backup": str(backup),
          "carrier": str(carrier), "projectConfigured": True, "testsRun": False,
          "modelRequestsSent": False, "liveProjectRoutingVerified": False}
(backup/"installation.json").write_text(json.dumps(result, indent=2)+"\n")
print(json.dumps(result, indent=2))
