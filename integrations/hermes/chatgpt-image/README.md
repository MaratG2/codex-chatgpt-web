# GPT Image through the existing ChatGPT Web conversation

This local Hermes plugin uses a two-phase handoff, not a synchronous second LLM
request inside a pending tool call. `chatgpt_image(prepare)` records the prompt,
destination, and existing assistant image identities. ChatGPT is explicitly
instructed to invoke its native image-generation capability in the same chat.
`collect` downloads the newly displayed generated raster and saves PNG plus
provenance. Preparation is never reported as a generated image.

The maintained launcher worker binds a private Hermes session hash to its exact
leased browser surface. `scripts/hermes-image-bridge.ts` uses that binding and the
existing browser's proxy/authentication, checks the saved conversation has not
changed, rejects baseline images and unlabelled images, and downloads the raster
resource through the page. It never opens a tab, sends a prompt, screenshots a
page, exports cookies, or calls an image API. The model receives only a saved
workspace path, hashes and dimensions, not signed URLs or image base64.

Configuration: enable the `chatgpt-image` plugin and include its toolset for Hermes.
The custom Web model provider includes the native-image exception and binding
marker in initial instructions. No FAL/OpenRouter/OpenAI image API key is used.
No change to `image_gen.provider` is needed: stock synchronous `image_generate`
cannot safely make a nested request into the same busy ChatGPT conversation.

SVG, HTML/Canvas drawing and screenshots are not valid fallbacks. PIL is used only
to decode the fetched raster and encode PNG, never to draw, crop or resize it.
The `*.provenance.json` files record the observed generated-image label and hashes;
the exact internal GPT Image version is not inferred from a Web subscription.

Limits: native generation must be available in the chosen ChatGPT Web mode.
Image collection depends on current assistant DOM/image labels and browser fetch
permissions. An unavailable native tool, unknown label, CORS/download failure or
rate limit is an explicit blocker. No automatic replacement chat or paid fallback.
Deployment does not itself verify generation; no tests or model runs were requested.

The 22:05 scheduled run exposed a routing error: it called
`Codex Native2.codex_tool_call(wire_name="image_gen__imagegen")`, which produced a
PNG under `.codex/generated_images/<thread>/`. That connector result is not a
first-party Web-chat raster. It must not be labelled/imported as Web output.
The launcher now restricts Hermes-bound tool inventories to registered `hermes_*`
tools, excluding the generic Codex gateway. Native Web generation must be invoked
through the first-party image tool actually declared by ChatGPT itself. An absent
tool is `NATIVE_WEB_IMAGE_UNAVAILABLE`, not an export error or permission to guess
a connector tool name.

`collect` observes the same page for up to 15 seconds and returns `awaiting_render`
if the native result has not mounted yet. It preserves the request and permits
collection (not generation) retries for a bounded 120-second window. A new Codex
PNG in this thread produces `WRONG_IMAGE_GENERATION_ROUTE`, preserving the original
file without substituting it for requested Web output.
