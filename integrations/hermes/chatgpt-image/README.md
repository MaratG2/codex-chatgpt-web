# GPT Image from the existing ChatGPT Web agent

Version 1.1.0 fixes the scheduled-run failure. The Web agent previously called the
registered Native2 image_gen__imagegen tool successfully; its genuine PNG was saved
under .codex/generated_images/<thread>/. The collector incorrectly looked only
for an assistant image in the Web page. Version 1.0.2 then mistakenly disabled
that working generator. That restriction is removed.

The launcher exposes Hermes dynamic tools plus the exact native image_gen.imagegen
tool. The generic exec gateway and unrelated native work tools remain excluded for
Hermes; ordinary Codex/Paperclip inventories are unchanged. The Web agent invokes
GPT Image in its existing task via Native2 with the current connector binding.
No additional reasoning chat or separately configured paid image API is created.
The tool's account quota is not inferred here.

prepare binds a unique request and exact prompt to the current Hermes provider's
Codex thread and rollout-log offset. The agent passes that prompt unchanged to
image_gen__imagegen, awaits its result, then calls collect. Collection accepts only
the original PNG named by a completed image_gen.imagegen call in that same thread,
after prepare, with the matching request prompt. No DOM image, signed download URL
or screenshot is used. Stale outputs, changed thread/log bindings, duplicate
calls, symlinks, unexpected paths and changed destinations are rejected.

PNG bytes are preserved exactly after decoding/dimension checks. The original
remains in Codex storage and a private request cache. Workspace provenance records
the generator call, prompt, hashes, dimensions and gpt-image-via-codex-native2
provider. It is not labelled first-party browser generation. SVG/code-drawn
replacements remain prohibited.

Collection waits briefly for log flush, then returns awaiting_tool_output for
bounded retries rather than launching another generation. Success requires saved,
visual review and normal Git commit/push. The AI VPN/proxy configuration is
retained; local collection makes no network call. No tests or model runs are part
of deployment. Connector/service limits, refusals and output quality remain
external conditions. Preserve partial results and report actual failures.

## Shared gallery template contract (1.2.0)

AI Education ad PNGs now require branding/gallery-template.json and a template_slot.
The plugin reads and hashes the real owner-approved originals. Adapt slots must
pass the exact referenced_image_paths returned by prepare to GPT Image; collect
validates actual tool arguments and reference hashes. Palette-only text prompts
are not accepted as template compliance. Visual acceptance additionally requires
comparison with the corresponding original layout and content blocks.

Reuse slots return saved directly from prepare after copying the approved master
byte-for-byte, with reuse provenance and generated=false. Do not call GPT Image
for those results. The owner selected the original review slide and requested one
new generic consultation master without the photo wording or bottom CTA; these
are common assets for every course. Only slides 1–4 are adapted per course.

Keep style.py installed beside connector.py and __init__.py when upgrading.
