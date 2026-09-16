// Codepoint-safe truncation — `str.slice(0, n)` cuts by UTF-16 code unit,
// which can split an astral-plane character (most emoji) into two lone
// surrogates that render as broken glyphs. Array.from() iterates by
// codepoint, so a cut always lands between whole characters.
export function truncateText(str: string, maxLength: number): string {
  return Array.from(str).slice(0, maxLength).join("");
}
