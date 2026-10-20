# MaratG2 maintained launcher

This is the owner's fork of miuuyy/codex-chatgpt-web, based on **v6.1.11**,
commit `9fe005eefb93913c138368b0972b8a0c476fcf54`.
The maintained branch is **codex/marat-runtime**, version **6.1.11-marat.1**.
Upstream licenses and attribution are preserved.

## Changes

- Settings → ChatGPT project stores an optional project landing-page URL. The
  runtime `chatgptProjectUrl` setting also accepts
  `setup --chatgpt-project-url https://chatgpt.com/g/g-p-…/project`.
- New automatic task chats navigate to that project before entering any prompt.
  Retained conversations continue in their existing tab. No project URL is baked
  into the published source. A project requires saved chats; Temporary Chat is
  unavailable until the setting is cleared.
- A missing composer, authentication failure or unexpected destination stops the
  turn. There is no fallback to ordinary ChatGPT home when a project is configured.
  Account inspection and connector maintenance still use their original empty
  surfaces; they are not task conversations.
- Built-in launcher update checks and installation are disabled. Installers point
  to this fork and pin its version. No original-repository release is selected
  automatically.
- Runtime payload size/hash enforcement is disabled in `runtime-install.cjs` at
  the owner's request, allowing local edits. Manifest identity/structure, required
  files, path containment and executable checks remain. Download checksum checks,
  authentication, tunnel verification, tool permissions and sandboxing are unchanged.
- The custom version has its own managed runtime directory. Rebuilding regenerates
  its manifest; startup therefore installs the maintained payload, not upstream files.

## Build without invoking models or tests

Use the pinned Bun version from package.json:

```sh
bun install --frozen-lockfile --ignore-scripts
(cd launcher && bun install --frozen-lockfile --ignore-scripts)
bun run scripts/build-runtime-bundle.ts
(cd launcher && bun run build)
```

The launcher can then be packaged with the existing packaging scripts, or its
Electron application and runtime can be deployed into the existing Linux carrier.
Building is not an end-to-end validation. Project-page composer behavior and Full
Harness inside projects have not been tested against ChatGPT at the owner's request.
If ChatGPT does not expose the connector in projects, the run stops; this fork
cannot override a ChatGPT-side capability restriction.

## Preserve changes when updating

`origin` must be `https://github.com/MaratG2/codex-chatgpt-web.git`;
`upstream` must be `https://github.com/miuuyy/codex-chatgpt-web.git`.
Never reset the maintained branch to upstream or install an upstream binary over it.

```sh
git switch codex/marat-runtime
git fetch upstream --tags
git switch -c codex/merge-upstream-VERSION
git merge --no-ff vVERSION
# Resolve conflicts while retaining project routing, update pinning and fork identity.
# Bump the custom version in package.json, launcher/package.json and src/version.ts.
# Build, review, and merge into codex/marat-runtime before deploying.
```

Fetching upstream does not modify the maintained branch. Merge conflicts must be
resolved explicitly; no script discards local changes. GitHub Actions are disabled
for the initial fork so a source push does not launch tests or release jobs.

## Current verification status

No test suites, browser smoke tests or model requests are authorized for this change.
Compilation/build results are reported separately in the deployment report. Do not
claim successful project chat delivery until the owner authorizes a real run.
# Hermes native image handoff (20 October 2026)

`browser-worker.ts` can bind an explicit `HERMES_IMAGE_SESSION=<sha256>` marker
from the initial Hermes instructions to the exact leased browser surface.
The private mapping is written under `runtime/hermes-image-sessions/`.
`scripts/hermes-image-bridge.ts` prepares an image baseline and exports a newly
rendered, generated-labelled assistant raster from that same saved conversation.
It does not create chats, send prompts, take screenshots, export credentials or
invoke the native Codex/API image route. Downloads run inside the existing browser
and inherit its configured network/proxy route.

The Hermes-side `chatgpt-image` plugin and provider exception are maintained in
the server deployment sources. Its workflow is prepare → native ChatGPT image
generation in the current conversation → collect → visual inspection. Generation
availability and DOM compatibility have **not** been live-tested in this deployment,
at the owner's request. Failures must remain explicit; no SVG or paid API fallback.
Retain these additions when merging upstream; normal `build-marat.sh` includes the
worker changes. The standalone image helper runs from the maintained source tree.

## Hermes binding repair (20 October 2026)

The original worker regex searched JSON-escaped prompt text. A preceding encoded
newline (`\nHERMES_IMAGE_SESSION`) prevented its word-boundary match, silently
omitting the binding. `hermes-image-session.ts` now extracts a whole-line marker
from structured system/developer content before serialization and before resume
history slicing. A typed `hermesImageSession` field travels through the daemon and
helper protocol on each retained tool-capable turn; the prompt is not repeated.

The worker writes version-2 ownership records atomically for the session and its
leased surface, refreshing them after browser reconnection. The exporter recovers
a missing/corrupt session index only from one matching live canonical surface
record. It checks host PID, endpoint and CDP target; ambiguous/stale/missing
ownership produces explicit `HERMES_IMAGE_BINDING_*` errors before generation.
No recent-chat guessing, additional chat, model retry or SVG/API fallback is used.
Both `cli.js` and `browser-helper.cjs` must be rebuilt/deployed for this change.
The installation uses the lifecycle drain/shutdown protocol while idle, allowing
the launcher supervisor to reload the daemon without restarting the browser.
No test or model call is part of this repair; the next scheduled run is the first
live exercise of image generation.

## GPT Image connector collection repair (21 October 2026)

The first-party-only restriction introduced on 20 October was incorrect: the
scheduled Web agent had already generated a genuine PNG using the registered
Native2 image_gen.imagegen tool. Requiring an assistant DOM image discarded that
successful result; disabling the generator caused NATIVE_WEB_IMAGE_UNAVAILABLE.

Hermes-marked requests now expose hermes_* tools plus exactly image_gen.imagegen.
The generic exec gateway and unrelated native work tools stay excluded. The
maintained plugin in integrations/hermes/chatgpt-image (v1.1.0) binds prepare to the
provider's exact task thread, records a rollout offset and unique prompt marker,
and collects only the original PNG named by that matching completed generator
call. No DOM rendering or signed browser download is needed. The Web model/chat,
proxy settings and schedules are retained; no separately configured paid API is
introduced. Provenance truthfully identifies GPT Image via Native2; quota behavior
is not inferred from the ChatGPT plan.

The PNG is copied byte-for-byte, with original preservation, changed-destination
checks, bounded log-flush waiting and resumable PNG/provenance writes. Unrelated
native tools, SVG, code-drawn replacements and extra chats remain disallowed.
Keep connector.py with __init__.py/plugin.yaml during upgrades. No model test or
extra production run is part of this repair; real generation was observed in a
past scheduled run, while the new complete collection flow awaits a scheduled run.
