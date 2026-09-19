"""Deterministic clean-up of model output — code, not another model call."""
from __future__ import annotations

import re

# Models prompted in Arabic sometimes carry Arabic/typographic punctuation into
# code ("return a،b", "n × 2", smart quotes), which is a SyntaxError. Inside a
# fenced code block none of these are ever intended, so replace them there and
# leave the surrounding prose untouched.
_CODE_CHARS = str.maketrans({
    "،": ",",    # ، Arabic comma
    "؛": ";",    # ؛ Arabic semicolon
    "؟": "?",    # ؟ Arabic question mark
    "×": "*",    # ×
    "÷": "/",    # ÷
    "−": "-",    # − minus sign
    "–": "-",    # – en dash
    "—": "-",    # — em dash
    "“": '"', "”": '"',   # smart double quotes
    "‘": "'", "’": "'",   # smart single quotes
    " ": " ",    # no-break space
    "​": "",     # zero-width space
    "…": "...",  # ellipsis
})
_FENCE = re.compile(r"(```[\w+-]*[ \t]*\n)(.*?)(```|\Z)", re.DOTALL)


def fix_code_punctuation(text: str) -> str:
    return _FENCE.sub(lambda m: m.group(1) + m.group(2).translate(_CODE_CHARS) + m.group(3), text)


# ── foreign-script leaks in Arabic answers ──────────────────────────────────

# Small multilingual models occasionally emit a word in another script inside an
# Arabic sentence (measured: "56 بайت" with Cyrillic, Chinese/Hebrew words in
# translations). The letters below are never legitimate in an answer to an
# Arabic message — unless the user asked for that language.
_FOREIGN = re.compile("[\u0590-\u05ff\u0400-\u04ff\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u0e00-\u0e7f]")
_ARABIC_LETTER = re.compile("[\u0600-\u06ff]")
_LATIN_LETTER = re.compile("[A-Za-z]")
_FENCED = re.compile(r"```.*?(```|\Z)", re.DOTALL)
_WANTS_OTHER_SCRIPT = re.compile(
    "روس|صين|ياباني|يابان|كوري|عبري|عبرية|تايلند|russian|chinese|japanese|korean|hebrew|thai|cyrillic|"
    "mandarin|kanji|hangul", re.IGNORECASE)

REPAIR_PROMPT = ("أعد كتابة النص التالي كما هو تماماً بالعربية، مع استبدال أي كلمة أو حروف من لغة أخرى "
                 "(روسية أو صينية أو عبرية...) بمقابلها العربي، أو بنطقها بالحروف العربية إن لم يكن لها مقابل "
                 "(مثال: 56 байт تصبح 56 بايت). لا تغيّر أي رقم أو معلومة ولا تضف شيئاً. أجب بالنص المصحح فقط.")


def leak_count(text: str) -> int:
    return len(_FOREIGN.findall(_FENCED.sub("", text)))


def needs_script_repair(user_message: str, answer: str, tool_trace: list[dict] | None = None) -> bool:
    if not leak_count(answer):
        return False
    if any(str(step.get("tool", "")).startswith("translate") for step in tool_trace or []):
        return False  # a translation tool's output is meant to be in whatever language was asked
    if _WANTS_OTHER_SCRIPT.search(user_message):
        return False
    return len(_ARABIC_LETTER.findall(user_message)) > len(_LATIN_LETTER.findall(user_message))
