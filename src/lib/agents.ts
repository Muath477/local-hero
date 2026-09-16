// The one place that talks to the FastAPI agents service (General Assistant,
// File Analyst, Auto, Tarjuman). It speaks the OpenAI-compatible API.
const AGENTS_URL = process.env.AGENTS_URL || "http://localhost:9099";

export type AgentModel = { id: string; name?: string; description?: string };
export type ChatMessage = { role: string; content: string };

export async function listModels(): Promise<AgentModel[]> {
  try {
    const res = await fetch(`${AGENTS_URL}/v1/models`, { cache: "no-store" });
    if (!res.ok) return [];
    const data = await res.json();
    return data.data ?? [];
  } catch {
    return [];
  }
}

/** Open a streaming chat completion. Returns the raw fetch Response (SSE body). */
export function streamChat(model: string, messages: ChatMessage[]) {
  return fetch(`${AGENTS_URL}/v1/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model, messages, stream: true }),
    cache: "no-store",
  });
}
