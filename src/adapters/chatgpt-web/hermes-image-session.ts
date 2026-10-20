import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { atomicWriteFile, getConfigDir } from "../../config";
import { readLauncherBrowserHostDescriptor } from "../../launcher-browser-host";
import type { CodexParsedRequest } from "../../types";

const SESSION = /^[a-f0-9]{64}$/;
const SURFACE = /^[A-Za-z0-9_-]{32}$/;
const directory = () => join(getConfigDir(), "runtime", "hermes-image-sessions");

/** Read trusted roles before prompt serialization or retained-history slicing. */
export function hermesImageSessionForRequest(parsed: CodexParsedRequest): string | undefined {
  const texts = [...(parsed.context.systemPrompt ?? [])];
  for (const message of parsed.context.messages) {
    if (message.role !== "developer") continue;
    texts.push(typeof message.content === "string" ? message.content
      : message.content.filter(part => part.type === "text").map(part => part.text).join("\n"));
  }
  const sessions = new Set<string>();
  for (const text of texts) {
    for (const line of text.split(/\r?\n/)) {
      if (!line.startsWith("HERMES_IMAGE_SESSION=")) continue;
      const session = line.slice("HERMES_IMAGE_SESSION=".length).trim();
      if (!SESSION.test(session)) throw new Error("HERMES_IMAGE_BINDING_INVALID: malformed session marker");
      sessions.add(session);
    }
  }
  if (sessions.size > 1) throw new Error("HERMES_IMAGE_BINDING_AMBIGUOUS: conflicting trusted session markers");
  return sessions.values().next().value;
}

interface ImageBinding {
  version: 2;
  session: string;
  surfaceId: string;
  conversationKey: string;
  descriptorPath: string;
  hostPid: number;
  hostEndpoint: string;
  targetId: string;
  updatedAt: string;
}

function readBinding(path: string): ImageBinding | undefined {
  try {
    const value = JSON.parse(readFileSync(path, "utf8"));
    if (value?.version !== 2 || !SESSION.test(value.session) || !SURFACE.test(value.surfaceId)
      || !SESSION.test(value.conversationKey) || typeof value.descriptorPath !== "string"
      || !Number.isInteger(value.hostPid) || typeof value.hostEndpoint !== "string"
      || typeof value.targetId !== "string") return undefined;
    return value;
  } catch (error) {
    if (error instanceof SyntaxError || (error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw error;
  }
}

function live(binding: ImageBinding): boolean {
  try {
    const host = readLauncherBrowserHostDescriptor(binding.descriptorPath);
    return host.pid === binding.hostPid && host.endpoint === binding.hostEndpoint
      && host.surfaceTargets[binding.surfaceId] === binding.targetId;
  } catch {
    return false;
  }
}

/** Refresh on every retained turn, without adding instructions or opening another chat. */
export function bindHermesImageSession(
  session: string, surfaceId: string, conversationKey: string, descriptorPath: string,
): void {
  if (!SESSION.test(session) || !SESSION.test(conversationKey) || !SURFACE.test(surfaceId)) {
    throw new Error("HERMES_IMAGE_BINDING_INVALID: invalid transport identity");
  }
  const host = readLauncherBrowserHostDescriptor(descriptorPath);
  const targetId = host.surfaceTargets[surfaceId];
  if (!targetId) throw new Error("HERMES_IMAGE_BINDING_STALE: leased browser surface is absent");
  const binding: ImageBinding = {
    version: 2, session, surfaceId, conversationKey, descriptorPath, targetId,
    hostPid: host.pid, hostEndpoint: host.endpoint, updatedAt: new Date().toISOString(),
  };
  const data = JSON.stringify(binding);
  // Canonical ownership first: a lost session index can be repaired from this record.
  atomicWriteFile(join(directory(), "surfaces", `${surfaceId}.json`), data);
  atomicWriteFile(join(directory(), `${session}.json`), data);
}

/** Recover only from an exact session match on a live, still-owned launcher surface. */
export function resolveHermesImageSession(session: string): ImageBinding {
  if (!SESSION.test(session)) throw new Error("HERMES_IMAGE_BINDING_INVALID: invalid session");
  const root = directory();
  const cached = readBinding(join(root, `${session}.json`));
  const matches: ImageBinding[] = [];
  let files: string[] = [];
  try { files = readdirSync(join(root, "surfaces")); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
  for (const file of files) {
    if (!/^[A-Za-z0-9_-]{32}\.json$/.test(file)) continue;
    const binding = readBinding(join(root, "surfaces", file));
    if (binding?.session === session && `${binding.surfaceId}.json` === file && live(binding)) {
      matches.push(binding);
    }
  }
  if (matches.length > 1) {
    throw new Error("HERMES_IMAGE_BINDING_AMBIGUOUS: multiple live surfaces own this session; no chat selected");
  }
  const binding = matches[0];
  if (!binding) {
    throw new Error(cached
      ? "HERMES_IMAGE_BINDING_STALE: session has no live owned surface; do not open a replacement chat"
      : "HERMES_IMAGE_BINDING_MISSING: transport has not bound this session; do not generate or open another chat");
  }
  if (JSON.stringify(cached) !== JSON.stringify(binding)) {
    atomicWriteFile(join(root, `${session}.json`), JSON.stringify(binding));
  }
  return binding;
}
