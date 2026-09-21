import { prisma } from "@/lib/db";
import { streamChat } from "@/lib/agents";

// Shared by /api/chat (new message) and /api/chats/[id]/messages/[messageId]
// (edit-and-regenerate) — both end at the same place: take whatever's in
// the chat's message history right now, ask the model, stream the reply
// back, persist it once the stream ends. Pulled out so an edited message
// doesn't need its own copy of the SSE-unwrapping loop.
export async function runAssistantReply(chatId: string, model: string): Promise<Response> {
  const history = await prisma.message.findMany({
    where: { chatId },
    orderBy: { createdAt: "asc" },
    select: { role: true, content: true },
  });

  const upstream = await streamChat(model, history);
  if (!upstream.ok || !upstream.body) {
    const msg = "تعذّر الاتصال بخدمة الموديل — تأكد أنها تعمل على المنفذ 9099.";
    await prisma.message.create({ data: { chatId, role: "assistant", content: msg } });
    return new Response(msg, { status: 502, headers: { "X-Chat-Id": chatId } });
  }

  const reader = upstream.body.getReader();
  const decoder = new TextDecoder();
  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    async start(controller) {
      let buffer = "";
      let full = "";
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed.startsWith("data:")) continue;
            const data = trimmed.slice(5).trim();
            if (data === "[DONE]") continue;
            try {
              const delta = JSON.parse(data).choices?.[0]?.delta?.content ?? "";
              if (delta) {
                full += delta;
                controller.enqueue(encoder.encode(delta));
              }
            } catch {
              // ignore partial JSON lines
            }
          }
        }
      } finally {
        await prisma.message.create({ data: { chatId, role: "assistant", content: full || "(لا رد)" } });
        controller.close();
      }
    },
    cancel() {
      reader.cancel();
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Chat-Id": chatId,
    },
  });
}
