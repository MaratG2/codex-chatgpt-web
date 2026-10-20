/**
 * Export newly rendered assistant raster images from a bound Hermes conversation.
 * No generation request, tab creation, navigation, screenshot, cookie export or API fallback.
 * Input/output JSON travels over stdio. Signed URLs and bytes never enter model context.
 */
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { connectLauncherBrowserHost } from "../src/launcher-browser-host";
import { CHATGPT_ASSISTANT_TURN_SELECTOR } from "../src/chatgpt-session";
import { resolveHermesImageSession } from "../src/adapters/chatgpt-web/hermes-image-session";

async function main() {
  const input = JSON.parse(readFileSync(0, "utf8"));
  if (!/^[a-f0-9]{64}$/.test(input.session)) throw new Error("Invalid Hermes session");
  if (!["prepare", "collect"].includes(input.action)) throw new Error("Invalid image action");
  const binding = resolveHermesImageSession(input.session);
  const connection = await connectLauncherBrowserHost(binding.descriptorPath, 12_000, binding.surfaceId);
  try {
    const current = resolveHermesImageSession(input.session);
    if (current.surfaceId !== binding.surfaceId || current.targetId !== binding.targetId
      || current.conversationKey !== binding.conversationKey
      || connection.descriptor.pid !== binding.hostPid
      || connection.descriptor.endpoint !== binding.hostEndpoint) {
      throw new Error("HERMES_IMAGE_BINDING_CHANGED: session ownership changed while connecting");
    }
    const page = connection.page;
    const url = new URL(page.url());
    if (url.origin !== "https://chatgpt.com" || !/\/c\/[a-zA-Z0-9-]+\/?$/.test(url.pathname)) {
      throw new Error("Bound surface is not a saved ChatGPT conversation");
    }
    const conversation = url.origin + url.pathname;
    if (input.action === "collect" && input.conversation !== conversation) {
      throw new Error("Conversation changed after image preparation");
    }
    const candidates = await page.evaluate(({ selector }) => {
      const roots = Array.from(document.querySelectorAll(selector));
      const seen = new Set<HTMLImageElement>();
      const result: { src: string; key: string; width: number; height: number; alt: string; generatedLabel: boolean }[] = [];
      for (const root of roots) {
        for (const img of Array.from(root.querySelectorAll("img"))) {
          if (seen.has(img)) continue;
          seen.add(img);
          if (img.closest('[data-user-message-bubble], [data-message-author-role="user"]')) continue;
          if (!img.complete || img.naturalWidth < 512 || img.naturalHeight < 512) continue;
          const src = img.currentSrc || img.src;
          if (!src) continue;
          const source = new URL(src, location.href);
          // Only resources actually displayed in this assistant turn. Exclude ordinary remote images.
          if (!(source.protocol === "blob:" && source.origin === location.origin)
            && !(source.protocol === "https:" && (source.hostname === "chatgpt.com"
              || source.hostname === "oaiusercontent.com" || source.hostname.endsWith(".oaiusercontent.com")
              || source.hostname === "oaidalleapiprodscus.blob.core.windows.net"))) continue;
          const identity = root.getAttribute("data-turn-key") || root.getAttribute("data-turn-id")
            || root.getAttribute("data-testid") || "";
          // Ignore signed-query rotation but retain an observed file id in query-based content URLs.
          const fileId = source.searchParams.get("id") || source.searchParams.get("file_id") || "";
          result.push({ src, key: `${identity}|${source.origin}${source.pathname}|${fileId}`,
            width: img.naturalWidth, height: img.naturalHeight, alt: img.alt.slice(0, 300),
            generatedLabel: /generated image|image created|created image|сгенерированное изображение|изображение создано/i.test(img.alt) });
        }
      }
      return result;
    }, { selector: CHATGPT_ASSISTANT_TURN_SELECTOR });
    if (input.action === "prepare") {
      return { conversation, baseline: candidates.map(c => c.key) };
    }
    const baseline = new Set(input.baseline);
    const fresh = candidates.filter(c => !baseline.has(c.key));
    if (!fresh.length) throw new Error("No new assistant raster found. Native generation or rendering has not completed; no file saved.");
    if (fresh.length !== 1 && !Number.isInteger(input.index)) {
      return { status: "choose_image", candidates: fresh.map((c, index) =>
        ({ index, width: c.width, height: c.height, alt: c.alt })) };
    }
    const selected = fresh[input.index ?? 0];
    if (!selected) throw new Error("Image index is outside this request's new images");
    if (!selected.generatedLabel) {
      throw new Error("New raster has no ChatGPT generated-image label; refusing unconfirmed image provenance");
    }
    const data = await page.evaluate(async ({ src }) => {
      // Fetch in the signed-in browser, preserving its existing AI VPN/proxy route.
      const response = await fetch(src, { credentials: "include", signal: AbortSignal.timeout(25_000) });
      if (!response.ok) throw new Error(`Image download HTTP ${response.status}`);
      const type = (response.headers.get("content-type") || "").split(";")[0];
      if (!["image/png", "image/jpeg", "image/webp"].includes(type)) throw new Error("Resource is not a supported raster");
      const reader = response.body!.getReader();
      const parts: Uint8Array[] = [];
      let size = 0;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > 25 * 1024 * 1024) { await reader.cancel(); throw new Error("Image exceeds 25 MiB"); }
        parts.push(value);
      }
      const blob = new Blob(parts as BlobPart[], { type });
      const base64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.onerror = () => reject(new Error("Image encoding failed"));
        reader.readAsDataURL(blob);
      });
      return { type, base64 };
    }, { src: selected.src });
    return { status: "downloaded", ...data, conversation,
      source_key_sha256: createHash("sha256").update(selected.key).digest("hex"),
      width: selected.width, height: selected.height, generated_image_label: selected.alt };
  } finally {
    // CDP Browser.close disconnects this client; it does not close the user's browser.
    await connection.browser.close();
  }
}

main().then(result => process.stdout.write(JSON.stringify(result) + "\n"))
  .catch(error => { process.stderr.write(String(error.message) + "\n"); process.exitCode = 1; });
