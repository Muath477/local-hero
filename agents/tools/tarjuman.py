"""Tarjuman — .docx translation, format-preserving at the paragraph level.

Scope of "format-preserving" here: paragraph structure, paragraph-level
style (heading level, list style, alignment) and run-level properties
(bold/italic/font) survive untouched, because only `run.text` is ever
written to. What's NOT preserved: a paragraph with several differently
-formatted runs (e.g. "plain text **bold word** more plain") collapses to
a single run after translation — the whole paragraph is translated as one
string and written back into the first run, other runs in that paragraph
are cleared. Good enough for typical business documents (memos, letters,
contracts) where formatting is mostly per-paragraph; a document leaning
on lots of inline bold/italic mid-sentence will lose that nuance.
Tables, headers, and footers are not touched — only body paragraphs.
"""
from __future__ import annotations

import io
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from orchestrator import ollama_client as ollama
from orchestrator.hardware import load_registry

RTL_LANGUAGES = {"Arabic", "العربية"}

# Measured, reproducible model quirk at this size class when translating to
# Arabic: stray words survive in whatever script the model feels like —
# caught it leaking Hebrew ("local" -> "מקומי"), plain English ("aims ..."),
# and Chinese ("budget" -> "预算") across different runs of the *same*
# sentence. Not one language to blacklist — any non-Arabic letter script
# showing up in bulk is the signal, so this checks the Arabic-vs-everything
# ratio generically instead of enumerating scripts one bug report at a time.
_ARABIC_RE = re.compile(r"[؀-ۿ]")


def _is_contaminated(text: str) -> bool:
    arabic = foreign = 0
    for ch in text:
        if not ch.isalpha():
            continue
        if _ARABIC_RE.match(ch):
            arabic += 1
        else:
            foreign += 1
    if arabic == 0:
        return True
    # Zero-tolerance, not a ratio: a stray foreign word at the very start of
    # an otherwise-fine sentence ("aims إلى بناء...") is a fully broken key
    # term, not a decorative acronym — the kind of single-word leak this is
    # meant to catch is exactly what a percentage threshold undercounts.
    # Costs one retry call on the rare legitimate acronym; worth it.
    return foreign > 0


def _translate_text(tag: str, text: str, target_language: str) -> str:
    system = (
        f"Translate the given text to {target_language}. Reply with ONLY the "
        "translation, preserving the original meaning and tone exactly. "
        "No notes, no explanations, no quotation marks around the output."
    )
    result = ollama.chat(
        model=tag,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
        keep_alive="5m",
        temperature=0.0,
    )
    translated = result["message"]["content"].strip()

    if target_language in RTL_LANGUAGES and _is_contaminated(translated):
        system_strict = system + " Arabic script only — no Hebrew, no leftover English words."
        result = ollama.chat(
            model=tag,
            messages=[{"role": "system", "content": system_strict}, {"role": "user", "content": text}],
            keep_alive="5m",
            temperature=0.4,  # a different sample, not the same deterministic miss again
        )
        retried = result["message"]["content"].strip()
        if not _is_contaminated(retried):
            return retried
    return translated


def translate_docx(source_bytes: bytes, target_language: str) -> bytes:
    registry = load_registry()
    model_tag = registry["tarjuman"]["ollama_tag"]
    rtl = target_language in RTL_LANGUAGES

    doc = Document(io.BytesIO(source_bytes))

    for paragraph in doc.paragraphs:
        original = paragraph.text
        if not original.strip():
            continue

        translated = _translate_text(model_tag, original, target_language)

        if paragraph.runs:
            paragraph.runs[0].text = translated
            for run in paragraph.runs[1:]:
                run.text = ""
        else:
            paragraph.add_run(translated)

        if rtl:
            paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            _set_paragraph_bidi(paragraph)

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _set_paragraph_bidi(paragraph) -> None:
    """python-docx has no first-class RTL/bidi property; the flag lives in
    the paragraph's raw XML (w:pPr/w:bidi). Best-effort — if this ever
    breaks against a future python-docx version, translation still works,
    just without the RTL paragraph-direction flag.
    """
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    pPr = paragraph._p.get_or_add_pPr()
    if pPr.find(qn("w:bidi")) is None:
        bidi = OxmlElement("w:bidi")
        pPr.append(bidi)
