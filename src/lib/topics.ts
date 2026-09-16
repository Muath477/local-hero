// Topic classification for the admin dashboard's "user interests" widget.
// Same pattern as the Python agents service's router
// (local-ai-platform/orchestrator/router.py): seeded example phrases per
// category, embed everything via Ollama's batched /api/embed, classify by
// max cosine similarity against any example in a category. Reused, not
// reinvented — this is deliberately the same shape as that file.
//
// Two differences from the router, both because this is advisory
// analytics rather than a decision that dispatches real work:
//  - Single-stage (no LLM fallback for ambiguous cases) — a message that
//    lands in "أخرى" costs nothing, unlike a misrouted chat request.
//  - Lower confidence threshold (0.55 vs the router's 0.62) — this widget
//    is more useful bucketing a fuzzy match than dumping everything into
//    "أخرى".

const OLLAMA_HOST = "http://localhost:11434";
const EMBED_MODEL = "nomic-embed-text:latest";
const CONFIDENCE_THRESHOLD = 0.55;
export const OTHER_CATEGORY = "أخرى";

// Seed set — tailored to LocalHero's actual agents (general-assistant-plus,
// coder, file-analyst, tool-caller/search, tarjuman) rather than a generic
// list, so categories map to something an admin can act on. Extend by
// adding phrases to an existing category or a new top-level key.
export const TOPIC_EXAMPLES: Record<string, string[]> = {
  "ترجمة مستندات": [
    "ترجم لي هذا الملف",
    "أبي أترجم مستند من الإنجليزي للعربي",
    "ترجمة عقد إلى العربية",
    "translate this document",
    "I need this file translated",
    "ترجم لي هذا التقرير",
  ],
  "مساعدة برمجية": [
    "اكتب لي كود بايثون",
    "صحح لي هذا الخطأ بالكود",
    "اشرح لي هذه الدالة",
    "write a function that",
    "debug this script",
    "fix this bug in my code",
  ],
  "تحليل ملفات": [
    "لخص لي هذا الملف",
    "ايش يقول التقرير المرفق",
    "اقرأ الملف وجاوبني عن محتواه",
    "summarize this document",
    "what does this file say about",
    "استخرج لي المعلومات من الملف",
  ],
  "بحث بالإنترنت": [
    "ابحث لي عن",
    "ابحث في الإنترنت عن آخر الأخبار",
    "ايش آخر إصدار من",
    "search the web for",
    "find current information about",
  ],
  "تحية ودردشة عامة": [
    "مرحبا",
    "صباح الخير",
    "كيف حالك",
    "شكرا لك",
    "hello",
    "hi there, how are you",
  ],
  "سؤال عام أو شرح مفهوم": [
    "ايش رأيك في",
    "اشرح لي مفهوم",
    "وضح لي الفرق بين",
    "ما هو",
    "explain how this works",
    "what is the difference between",
  ],
  "شكوى أو مشكلة تقنية": [
    "ما اشتغلت الأداة",
    "فيه خطأ ما يطلع الرد",
    "تعطل النظام عندي",
    "this isn't working",
    "I'm getting an error",
    "the response is broken",
  ],
};

async function embedBatch(texts: string[]): Promise<number[][]> {
  const res = await fetch(`${OLLAMA_HOST}/api/embed`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: EMBED_MODEL, input: texts }),
  });
  if (!res.ok) throw new Error(`ollama embed failed: ${res.status} ${await res.text()}`);
  const data = await res.json();
  return data.embeddings as number[][];
}

function cosine(a: number[], b: number[]): number {
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  const denom = Math.sqrt(na) * Math.sqrt(nb);
  return denom === 0 ? 0 : dot / denom;
}

type CategoryVectors = { name: string; vectors: number[][] };
let categoryVectorsCache: CategoryVectors[] | null = null;

// Seed examples are static, so their embeddings are cached in-process for
// the life of the server — only the (changing) user messages get
// re-embedded on each call. Same "batch once, reuse" idea as the router's
// warm_up(), just at module scope instead of an explicit startup hook.
async function getCategoryVectors(): Promise<CategoryVectors[]> {
  if (categoryVectorsCache) return categoryVectorsCache;
  const names = Object.keys(TOPIC_EXAMPLES);
  const flatTexts = names.flatMap((n) => TOPIC_EXAMPLES[n]);
  const flatVectors = await embedBatch(flatTexts);
  let i = 0;
  categoryVectorsCache = names.map((name) => {
    const n = TOPIC_EXAMPLES[name].length;
    const vectors = flatVectors.slice(i, i + n);
    i += n;
    return { name, vectors };
  });
  return categoryVectorsCache;
}

// Core classification: one category label per input message, same order.
// Exported on its own (not just pre-aggregated counts) so a caller can
// derive both a global distribution and a per-user breakdown from a
// single embedding pass instead of calling Ollama twice.
export async function classifyMessages(messages: string[]): Promise<string[]> {
  if (messages.length === 0) return [];

  const categories = await getCategoryVectors();
  const msgVectors = await embedBatch(messages);

  return msgVectors.map((vec) => {
    let bestCategory = OTHER_CATEGORY;
    let bestScore = -1;
    for (const cat of categories) {
      const score = Math.max(...cat.vectors.map((v) => cosine(vec, v)));
      if (score > bestScore) {
        bestScore = score;
        bestCategory = cat.name;
      }
    }
    return bestScore >= CONFIDENCE_THRESHOLD ? bestCategory : OTHER_CATEGORY;
  });
}

export function aggregateCounts(labels: string[]): { category: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const label of labels) counts.set(label, (counts.get(label) ?? 0) + 1);
  return [...counts.entries()]
    .map(([category, count]) => ({ category, count }))
    .sort((a, b) => b.count - a.count);
}

// Convenience wrapper for the common "just the global distribution" case.
export async function classifyTopics(
  messages: string[],
): Promise<{ category: string; count: number }[]> {
  return aggregateCounts(await classifyMessages(messages));
}
