/** Only a project landing page may be used as the destination for a new task. */
export function normalizeChatGptProjectUrl(value: unknown): string | undefined {
  if (value === undefined || value === null || value === "") return undefined;
  if (typeof value !== "string") throw new Error("ChatGPT project URL must be a string");
  const text = value.trim();
  if (!text) return undefined;
  const url = new URL(text);
  if (url.origin !== "https://chatgpt.com" || url.username || url.password
    || url.search || url.hash || !/^\/g\/g-p-[a-zA-Z0-9-]+\/project\/?$/.test(url.pathname)) {
    throw new Error("Use the ChatGPT project link: https://chatgpt.com/g/g-p-…/project");
  }
  return `${url.origin}${url.pathname.replace(/\/$/, "")}`;
}
