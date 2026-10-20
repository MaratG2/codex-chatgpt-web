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
