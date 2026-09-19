"""Tarjuman — .docx translation. The model translates text; this code protects the document.

Pipeline (ingestion -> model -> export), the model never sees file structure:

  1. Ingestion walks every text container in body order: body paragraphs,
     tables (nested, merged cells counted once), headers and footers.
  2. Inline formatting is protected with placeholders. "The **first party**
     shall deliver" becomes "The {{1}}first party{{/1}} shall deliver"; the
     model carries the tags across and export rebuilds real runs from them.
     The most common formatting in a paragraph stays untagged.
  3. Export rebuilds runs by cloning the original run properties, so bold,
     italic, fonts and colours survive translation even when word order moves.
     RTL is set as XML properties in code (paragraph bidi, run rtl, table
     bidiVisual), never by the model.

Paragraphs the safe path can't rebuild (hyperlinks, fields, tracked changes,
images in the same paragraph) are translated as plain text into their first
text run; their non-text content is left where it was.

Performance: segments with no letters (numbers, URLs, emails) never reach the
model; identical segments are translated once; results are cached on disk by
(model, language, glossary, text) so re-translating a document is instant;
short segments are batched several per call; output length is capped.

Not covered (yet): text boxes, footnotes, .pptx/.xlsx, PDFs.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.run import Run
from lxml import etree

from orchestrator import ollama_client as ollama
from orchestrator.hardware import load_registry

log = logging.getLogger(__name__)

RTL_LANGUAGES = {"Arabic", "العربية", "Hebrew", "עברית", "Persian", "Farsi", "فارسی", "Urdu", "اردو"}
ARABIC_SCRIPT_LANGUAGES = {"Arabic", "العربية", "Persian", "Farsi", "فارسی", "Urdu", "اردو"}
_RTL = {n.casefold() for n in RTL_LANGUAGES}
_ARABIC_SCRIPT = {n.casefold() for n in ARABIC_SCRIPT_LANGUAGES}

PROMPT_VERSION = "3"          # bump when prompts change: it's part of the cache key
CACHE_PATH = Path(__file__).parent.parent / "data" / "tarjuman_cache.json"
SHORT_CHARS = 300             # segments up to this length may share a batched call
MAX_BATCH_CHARS = 1500
MAX_BATCH_ITEMS = 12


def _is_rtl(language: str) -> bool:
    return language.strip().casefold() in _RTL


def _is_arabic_script(language: str) -> bool:
    return language.strip().casefold() in _ARABIC_SCRIPT


# ── output checks ───────────────────────────────────────────────────────────

# Measured, reproducible model quirk at small sizes when translating to
# Arabic: stray words survive in whatever script the model feels like —
# Hebrew ("local" -> "מקומי"), plain English ("aims ..."), Chinese
# ("budget" -> "预算") across runs of the *same* sentence. Any non-Arabic
# letter is the signal, so this isn't a per-script blacklist.
_ARABIC_RE = re.compile(r"[؀-ۿ]")
_TAG_RE = re.compile(r"\{\{(/?)(\d+)\}\}")
_URL_OR_EMAIL = re.compile(r"^(https?://\S+|www\.\S+|[\w.+-]+@[\w-]+\.[\w.-]+)$", re.IGNORECASE)


def _is_contaminated(text: str) -> bool:
    # Zero-tolerance, not a ratio: a stray foreign word at the very start of
    # an otherwise-fine sentence ("aims إلى بناء...") is a fully broken key
    # term. No letters at all (a paragraph that is just "2024") has nothing
    # to leak. Costs one retry on a rare legitimate acronym; worth it.
    return any(ch.isalpha() and not _ARABIC_RE.match(ch) for ch in text)


def _needs_translation(text: str) -> bool:
    t = text.strip()
    return any(c.isalpha() for c in t) and not _URL_OR_EMAIL.match(t)


def parse_tagged(text: str, valid_ids: set[int]) -> list[tuple[str, int | None]] | None:
    """Splits "a {{1}}b{{/1}} c" into [("a ", None), ("b", 1), (" c", None)].
    None when the model broke the tags (unknown id, unclosed, nested, mismatched)."""
    pieces: list[tuple[str, int | None]] = []
    pos, open_id = 0, None
    for m in _TAG_RE.finditer(text):
        if m.start() > pos:
            pieces.append((text[pos:m.start()], open_id))
        closing, num = m.group(1) == "/", int(m.group(2))
        if num not in valid_ids:
            return None
        if closing:
            if open_id != num:
                return None
            open_id = None
        else:
            if open_id is not None:
                return None
            open_id = num
        pos = m.end()
    if open_id is not None:
        return None
    if pos < len(text):
        pieces.append((text[pos:], None))
    return [p for p in pieces if p[0]]


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text)


def _tag_ids(text: str) -> set[int]:
    return {int(m.group(2)) for m in _TAG_RE.finditer(text)}


# ── prompts ─────────────────────────────────────────────────────────────────

def system_prompt(target: str, *, tags: bool = False, glossary: dict[str, str] | None = None,
                  strict: bool = False, batch: bool = False) -> str:
    if batch:
        parts = [
            f"Translate each numbered segment to {target}. The input is a series of segments, each "
            "introduced by a marker line like [[1]]. Reply with the translations only, in the same "
            "order, each introduced by the same marker line. Never merge, split, skip or renumber segments. "
            "No notes, no explanations."
        ]
    else:
        parts = [
            f"Translate the given text to {target}. Reply with ONLY the translation, preserving the "
            "original meaning and tone exactly. No notes, no explanations, no quotation marks around the output."
        ]
    if _is_arabic_script(target):
        parts.append("Use clear Modern Standard Arabic.")
    if tags:
        parts.append("The text contains formatting tags like {{1}}words{{/1}}. Copy every tag exactly, "
                     "wrapping the translation of the same words; never add, drop, nest or rename tags.")
    if glossary:
        terms = "\n".join(f"- {src} -> {dst}" for src, dst in glossary.items())
        parts.append(f"Mandatory terminology, use exactly these translations:\n{terms}")
    if strict and _is_arabic_script(target):
        parts.append("Arabic script only — no Hebrew, no leftover English words.")
    return "\n".join(parts)


def _glossary_hits(glossary: dict[str, str] | None, text: str) -> dict[str, str]:
    if not glossary:
        return {}
    # Whole-term match: a bare substring test would fire "No -> لا" on "Note" and "not".
    return {k: v for k, v in glossary.items()
            if re.search(rf"(?<!\w){re.escape(k)}(?!\w)", text, re.IGNORECASE)}


GLOSSARY_PATH = Path(__file__).parent.parent / "config" / "tarjuman_glossary.yaml"


def default_glossary(language: str) -> dict[str, str]:
    """Standard document terms for the target language from config/tarjuman_glossary.yaml
    (edit that file to add your own). Small models get isolated labels wrong —
    "Confidential" came back as "مُخفي" — so these are applied, not merely suggested."""
    try:
        import yaml
        data = yaml.safe_load(GLOSSARY_PATH.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}
    for name, terms in data.items():
        if str(name).casefold() == language.strip().casefold() and isinstance(terms, dict):
            return {str(k): str(v) for k, v in terms.items()}
    return {}


def _label_key(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\s.:;,\-–—]+$", "", text.strip())).casefold()


RETAG_SYSTEM = ("You are given a source sentence with formatting tags like {{1}}words{{/1}} and its finished translation. "
                "Return the translation EXACTLY as written, character for character, only inserting the same tags around "
                "the words that correspond to the tagged source words. Never change, add, remove or reorder any word. "
                "Reply with the tagged translation only.")


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _token_cap(chars: int) -> int:
    # Arabic/Hebrew can cost up to ~1 token per character; the cap only has to stop
    # a runaway loop (minutes on CPU), not fit the answer tightly.
    return min(3072, max(96, chars * 2 + 48))


def _translate_text(tag: str, text: str, target_language: str, *, glossary: dict[str, str] | None = None,
                    think: bool | None = None, counter: Callable[[], None] | None = None) -> str:
    """One segment, one model call — plus one retry when the answer leaks a
    foreign script or mangles the formatting tags."""
    tagged = bool(_tag_ids(text))
    valid = _tag_ids(text)
    hits = _glossary_hits(glossary, _strip_tags(text))

    def ask(strict: bool, temperature: float) -> str:
        if counter:
            counter()
        result = ollama.chat(
            model=tag,
            messages=[
                {"role": "system", "content": system_prompt(target_language, tags=tagged, glossary=hits, strict=strict)},
                {"role": "user", "content": text},
            ],
            keep_alive="5m", temperature=temperature, think=think, num_predict=_token_cap(len(text)),
        )
        return result["message"]["content"].strip()

    def bad(out: str) -> bool:
        if tagged and parse_tagged(out, valid) is None:
            return True
        return _is_arabic_script(target_language) and _is_contaminated(_strip_tags(out))

    translated = ask(strict=False, temperature=0.0)
    if bad(translated):
        retried = ask(strict=True, temperature=0.4)  # a different sample, not the same deterministic miss again
        if not bad(retried):
            return retried
    if tagged and parse_tagged(translated, valid) is None:
        return _strip_tags(translated)  # tags unusable: keep the words, lose the inline formatting
    return translated


# ── translation memory ──────────────────────────────────────────────────────

class TranslationCache:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else CACHE_PATH
        self._lock = threading.Lock()
        self._data: dict[str, str] = {}
        self._dirty = False
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass  # missing or corrupt cache is just an empty cache

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def put(self, key: str, value: str) -> None:
        with self._lock:
            self._data[key] = value
            self._dirty = True

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)  # atomic: a crash never leaves a half-written cache
            self._dirty = False


class Translator:
    """Callable: list of segment texts in -> list of translations out (same order)."""

    def __init__(self, target_language: str, model_tag: str | None = None, glossary: dict[str, str] | None = None,
                 cache: TranslationCache | None = None, think: bool | None = None):
        cfg = load_registry()["tarjuman"]
        self.target = target_language
        self.tag = model_tag or cfg["ollama_tag"]
        self.think = cfg.get("think") if think is None else think
        self.glossary = {**default_glossary(target_language), **(glossary or {})}  # caller's terms win
        self._labels = {_label_key(k): v for k, v in self.glossary.items()}
        self.cache = cache
        self.stats = {"segments": 0, "unique": 0, "skipped": 0, "glossary": 0, "cached": 0, "translated": 0,
                      "retagged": 0, "retag_failed": 0, "llm_calls": 0}
        self._glossary_key = json.dumps(self.glossary, sort_keys=True, ensure_ascii=False)

    def _key(self, text: str) -> str:
        raw = f"{PROMPT_VERSION}\x00{self.tag}\x00{self.target}\x00{self._glossary_key}\x00{text}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _count_call(self) -> None:
        self.stats["llm_calls"] += 1

    def _single(self, text: str) -> str:
        return _translate_text(self.tag, text, self.target, glossary=self.glossary, think=self.think,
                               counter=self._count_call)

    def _batch(self, items: list[str]) -> list[str]:
        """Several short segments in one call. Anything the model fumbles (missing
        marker, empty answer, leaked script, broken tags) is redone individually."""
        hits = {}
        for t in items:
            hits.update(_glossary_hits(self.glossary, _strip_tags(t)))
        user = "\n".join(f"[[{i}]]\n{t}" for i, t in enumerate(items, 1))
        self._count_call()
        result = ollama.chat(
            model=self.tag,
            messages=[
                {"role": "system", "content": system_prompt(
                    self.target, tags=any(_tag_ids(t) for t in items), glossary=hits, batch=True)},
                {"role": "user", "content": user},
            ],
            keep_alive="5m", temperature=0.0, think=self.think, num_predict=_token_cap(len(user)),
        )
        parts = re.split(r"^\[\[(\d+)\]\][ \t]*\r?\n?", result["message"]["content"].strip(), flags=re.MULTILINE)
        found = {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
        out = []
        for i, src in enumerate(items, 1):
            cand = found.get(i, "")
            ok = bool(cand)
            if ok and _tag_ids(src) and parse_tagged(cand, _tag_ids(src)) is None:
                ok = False
            if ok and _is_arabic_script(self.target) and _is_contaminated(_strip_tags(cand)):
                ok = False
            out.append(cand if ok else self._single(src))
        return out

    def _batches(self, texts: list[str]) -> Iterator[list[str]]:
        current: list[str] = []
        size = 0
        for t in texts:
            if len(t) > SHORT_CHARS:
                if current:
                    yield current
                    current, size = [], 0
                yield [t]
                continue
            if current and (size + len(t) > MAX_BATCH_CHARS or len(current) >= MAX_BATCH_ITEMS):
                yield current
                current, size = [], 0
            current.append(t)
            size += len(t)
        if current:
            yield current

    def __call__(self, texts: list[str]) -> list[str]:
        """Inline-formatting tags never reach the translation: measured on Qwen3-4B, wrapping a phrase
        in tags made it translate the phrase as isolated words ("two percent" -> "200%", "first party"
        -> "the political party"), while the same sentence untagged was right. So: translate the clean
        text, then a second, verifiable pass copies the tags onto the finished translation."""
        clean = [_strip_tags(t) for t in texts]
        translated = self._translate_plain(clean)
        out = []
        for src, plain, tr in zip(texts, clean, translated):
            if not _tag_ids(src):
                out.append(tr)
            elif not _needs_translation(plain):
                out.append(src)  # "{{1}}42{{/1}}": nothing translated, keep the original formatting exactly
            else:
                out.append(self._retag(src, tr))
        return out

    def _retag(self, tagged_source: str, translation: str) -> str:
        """Puts the source's tags around the matching words of `translation`. The result is accepted only
        if the tags are well-formed AND the text without them is identical to the translation — the model
        can lose formatting here, never change a word."""
        key = self._key(f"retag\x00{tagged_source}\x00{translation}")
        if self.cache and (hit := self.cache.get(key)) is not None:
            return hit
        self._count_call()
        result = ollama.chat(
            model=self.tag,
            messages=[{"role": "system", "content": RETAG_SYSTEM},
                      {"role": "user", "content": f"Source: {tagged_source}\nTranslation: {translation}"}],
            keep_alive="5m", temperature=0.0, think=self.think, num_predict=_token_cap(len(translation) + len(tagged_source)),
        )
        out = result["message"]["content"].strip()
        if parse_tagged(out, _tag_ids(tagged_source)) is not None and _squash(_strip_tags(out)) == _squash(translation):
            self.stats["retagged"] += 1
            final = out
        else:
            self.stats["retag_failed"] += 1
            final = translation  # plain text in the paragraph's main formatting: less pretty, never wrong
        if self.cache:
            self.cache.put(key, final)
        return final

    def _translate_plain(self, texts: list[str]) -> list[str]:
        self.stats["segments"] += len(texts)
        results: dict[str, str] = {}
        pending: list[str] = []
        for text in dict.fromkeys(texts):  # identical segments are translated once
            self.stats["unique"] += 1
            if not _needs_translation(text):
                results[text] = text
                self.stats["skipped"] += 1
            elif (label := self._labels.get(_label_key(text))) is not None:
                results[text] = label  # an exact standard term ("Confidential", "Total"): no model needed
                self.stats["glossary"] += 1
            elif self.cache and (hit := self.cache.get(self._key(text))) is not None:
                results[text] = hit
                self.stats["cached"] += 1
            else:
                pending.append(text)

        for batch in self._batches(pending):
            outs = [self._single(batch[0])] if len(batch) == 1 else self._batch(batch)
            for src, dst in zip(batch, outs):
                results[src] = dst
                self.stats["translated"] += 1
                if self.cache:
                    self.cache.put(self._key(src), dst)
        return [results[t] for t in texts]


# ── ingestion: find segments, protect inline formatting ─────────────────────

_PURE_RUN_CHILDREN = {qn(t) for t in ("w:rPr", "w:t", "w:tab", "w:br", "w:lastRenderedPageBreak")}
_INERT_PARAGRAPH_CHILDREN = {qn(t) for t in ("w:pPr", "w:bookmarkStart", "w:bookmarkEnd", "w:proofErr")}
_W_R, _W_T, _W_RPR = qn("w:r"), qn("w:t"), qn("w:rPr")


def _pure(run) -> bool:
    return all(child.tag in _PURE_RUN_CHILDREN for child in run)


def _run_text(run_el, paragraph) -> str:
    return Run(run_el, paragraph).text


@dataclass
class _Unit:
    paragraph: Paragraph
    runs: list                       # w:r elements this unit rewrites
    rich: bool
    text: str                        # what the model sees (stripped)
    lead: str
    trail: str
    templates: dict                  # tag id (None = dominant formatting) -> template w:r


def _sig(run) -> bytes:
    rpr = run.find(_W_RPR)
    return etree.tostring(rpr, method="c14n") if rpr is not None else b""


def _analyse(paragraph: Paragraph) -> _Unit | None:
    p = paragraph._p
    direct = [c for c in p.iterchildren() if c.tag == _W_R]
    exotic = any(c.tag != _W_R and c.tag not in _INERT_PARAGRAPH_CHILDREN for c in p.iterchildren())

    if not exotic and direct and all(_pure(r) for r in direct):
        texts = [_run_text(r, paragraph) for r in direct]
        full = "".join(texts)
        if not full.strip():
            return None
        # Whitespace-only runs adopt their neighbour's formatting instead of spawning tags.
        sigs = [_sig(r) for r in direct]
        for i, t in enumerate(texts):
            if not t.strip() and len(direct) > 1:
                sigs[i] = sigs[i - 1] if i > 0 else next((sigs[j] for j in range(1, len(direct)) if texts[j].strip()), sigs[i])
        weight: dict[bytes, int] = {}
        for s, t in zip(sigs, texts):
            weight[s] = weight.get(s, 0) + len(t.strip())
        dominant = max(weight, key=weight.get)
        ids: dict[bytes, int] = {}
        templates: dict = {None: next(r for r, s in zip(direct, sigs) if s == dominant)}
        for r, s in zip(direct, sigs):
            if s != dominant and s not in ids:
                ids[s] = len(ids) + 1
                templates[ids[s]] = r
        merged: list[list] = []
        for s, t in zip(sigs, texts):
            if merged and merged[-1][0] == s:
                merged[-1][1] += t
            else:
                merged.append([s, t])
        body = "".join(t if s == dominant else f"{{{{{ids[s]}}}}}{t}{{{{/{ids[s]}}}}}" for s, t in merged)
        core = body.strip()
        lead = body[: len(body) - len(body.lstrip())]
        trail = body[len(body.rstrip()):]
        return _Unit(paragraph, direct, True, core, lead, trail, templates)

    # Safe path: every text-bearing pure run anywhere in the paragraph; the rest stays put.
    text_runs = [r for r in p.iter(_W_R) if _pure(r) and r.find(_W_T) is not None]
    full = "".join(_run_text(r, paragraph) for r in text_runs)
    if not full.strip():
        return None
    return _Unit(paragraph, text_runs, False, full.strip(), full[: len(full) - len(full.lstrip())],
                 full[len(full.rstrip()):], {})


# ── export: rebuild runs, apply RTL ─────────────────────────────────────────

_PPR_AFTER_BIDI = ("w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing",
                   "w:mirrorIndents", "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment",
                   "w:textboxTightWrap", "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange")
_TBLPR_AFTER_BIDIVISUAL = ("w:tblStyleRowBandSize", "w:tblStyleColBandSize", "w:tblW", "w:jc", "w:tblCellSpacing",
                           "w:tblInd", "w:tblBorders", "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook",
                           "w:tblCaption", "w:tblDescription", "w:tblPrChange")


def _insert_ordered(parent, child, successors) -> None:
    """OOXML children are an ordered sequence; Word can reject a file whose
    property elements are out of order, so insert before the first successor."""
    for tag in successors:
        found = parent.find(qn(tag))
        if found is not None:
            found.addprevious(child)
            return
    parent.append(child)


def _set_paragraph_rtl(paragraph: Paragraph) -> None:
    # Alignment is deliberately left alone: in a bidi paragraph Word already starts
    # on the right, and jc left/right are mirrored — forcing RIGHT here would put
    # the text on the LEFT. Centre/justify from the source carry over untouched.
    pPr = paragraph._p.get_or_add_pPr()
    if pPr.find(qn("w:bidi")) is None:
        _insert_ordered(pPr, OxmlElement("w:bidi"), _PPR_AFTER_BIDI)
    for r in paragraph._p.iter(_W_R):
        if r.find(_W_T) is not None:
            Run(r, paragraph).font.rtl = True


def _set_table_rtl(tbl) -> None:
    tblPr = tbl.tblPr
    if tblPr.find(qn("w:bidiVisual")) is None:
        _insert_ordered(tblPr, OxmlElement("w:bidiVisual"), _TBLPR_AFTER_BIDIVISUAL)


def _apply(unit: _Unit, translated: str) -> None:
    paragraph = unit.paragraph
    if unit.rich:
        valid = {i for i in unit.templates if i is not None}
        pieces = parse_tagged(translated, valid)
        if pieces is None:
            pieces = [(_strip_tags(translated), None)]
        if unit.lead:
            pieces.insert(0, (unit.lead, None))
        if unit.trail:
            pieces.append((unit.trail, None))
        anchor = unit.runs[0]
        for text, tag_id in pieces:
            new = copy.deepcopy(unit.templates[tag_id])
            for child in list(new):
                if child.tag != _W_RPR:
                    new.remove(child)
            Run(new, paragraph).text = text
            anchor.addprevious(new)
        for old in unit.runs:
            old.getparent().remove(old)
        return

    first, *rest = unit.runs
    Run(first, paragraph).text = unit.lead + _strip_tags(translated) + unit.trail
    for r in rest:
        Run(r, paragraph).text = ""


# ── document walk ───────────────────────────────────────────────────────────

def _walk(parent, element, tables: list, seen_cells: set) -> Iterator[Paragraph]:
    for child in element.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, parent)
        elif child.tag == qn("w:tbl"):
            table = Table(child, parent)
            tables.append(child)
            for row in table.rows:
                for cell in row.cells:
                    if cell._tc in seen_cells:  # merged cells appear once per grid slot
                        continue
                    seen_cells.add(cell._tc)
                    yield from _walk(cell, cell._tc, tables, seen_cells)
        elif child.tag == qn("w:sdt"):  # content controls wrap ordinary blocks
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                yield from _walk(parent, content, tables, seen_cells)


def _paragraphs(doc) -> tuple[list[Paragraph], list]:
    tables: list = []
    seen: set = set()
    out = list(_walk(doc, doc.element.body, tables, seen))
    for section in doc.sections:
        for part in (section.header, section.footer, section.first_page_header, section.first_page_footer,
                     section.even_page_header, section.even_page_footer):
            if not part.is_linked_to_previous:  # a linked header is the previous section's own — already done
                out.extend(_walk(part, part._element, tables, seen))
    return out, tables


def translate_docx(source_bytes: bytes, target_language: str, *,
                   translate: Callable[[list[str]], list[str]] | None = None,
                   glossary: dict[str, str] | None = None, use_cache: bool = True) -> bytes:
    return translate_docx_with_stats(source_bytes, target_language, translate=translate,
                                     glossary=glossary, use_cache=use_cache)[0]


def translate_docx_with_stats(source_bytes: bytes, target_language: str, *,
                              translate: Callable[[list[str]], list[str]] | None = None,
                              glossary: dict[str, str] | None = None, use_cache: bool = True) -> tuple[bytes, dict]:
    """`translate` swaps the model out (identity gives the empty round-trip:
    file in, same file out — the baseline the whole pipeline is tested against)."""
    doc = Document(io.BytesIO(source_bytes))
    rtl = _is_rtl(target_language)

    paragraphs, tables = _paragraphs(doc)
    units = [u for u in map(_analyse, paragraphs) if u]

    cache = TranslationCache() if use_cache and translate is None else None
    translator = translate or Translator(target_language, glossary=glossary, cache=cache)
    outputs = translator([u.text for u in units])
    if len(outputs) != len(units):
        raise RuntimeError(f"translator returned {len(outputs)} results for {len(units)} segments")
    for unit, out in zip(units, outputs):
        _apply(unit, out)
        if rtl:
            _set_paragraph_rtl(unit.paragraph)
    if rtl:
        for tbl in tables:
            _set_table_rtl(tbl)
    if cache:
        cache.save()

    buf = io.BytesIO()
    doc.save(buf)
    stats = dict(getattr(translator, "stats", {}))
    log.info("tarjuman: %s", stats)
    return buf.getvalue(), stats
