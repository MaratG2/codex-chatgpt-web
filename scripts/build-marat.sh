#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
bun="${BUN:-bun}"
export PATH="$(dirname -- "$(command -v "$bun")"):$PATH"
"$bun" install --frozen-lockfile --ignore-scripts
(cd launcher && "$bun" install --frozen-lockfile --ignore-scripts)
"$bun" run typecheck
"$bun" run scripts/build-runtime-bundle.ts
(cd launcher && "$bun" run build)
# Build the application archive without downloading or starting another Electron.
"$bun" -e 'const fs=require("node:fs"); const asar=require("./launcher/node_modules/@electron/asar"); const path=require("node:path"); const dest="dist/marat-app"; fs.mkdirSync(dest,{recursive:true}); for(const name of ["dist","electron","assets","package.json"]) fs.cpSync(path.join("launcher",name),path.join(dest,name),{recursive:true}); await asar.createPackage(dest,"dist/marat-app.asar");'
printf '%s\n' "Built dist/runtime and dist/marat-app.asar. No tests or model calls were run."
