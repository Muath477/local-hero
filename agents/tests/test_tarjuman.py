import base64
import io
import re

import docx
import pytest
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.run import Run

from orchestrator import ollama_client
from tools import tarjuman
from tools.tarjuman import (
    TranslationCache, Translator, _is_contaminated, _needs_translation, _translate_text, parse_tagged,
    system_prompt, translate_docx, translate_docx_with_stats,
)

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


# ── output checks ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("مرحبا بكم في شركتنا", False),
    ("مرحبا 2024 — ١٢٣ (٪)", False),
    ("aims إلى بناء منصة", True),
    ("שלום עולם", True),
    ("预算 للمشروع", True),
    ("hello world", True),
    ("2024", False),
    ("", False),
])
def test_is_contaminated(text, expected):
    assert _is_contaminated(text) is expected


@pytest.mark.parametrize("text, expected", [
    ("Hello", True), ("مرحبا", True), ("2024-05-01", False), ("  ", False), ("$1,200.50", False),
    ("https://example.com/a?b=1", False), ("someone@example.com", False), ("Contact someone@example.com", True),
    ("{{1}}42{{/1}}", False),
])
def test_needs_translation(text, expected):
    assert _needs_translation(text) is expected


class TestTagParsing:
    def test_valid(self):
        assert parse_tagged("a {{1}}b{{/1}} c", {1}) == [("a ", None), ("b", 1), (" c", None)]
        assert parse_tagged("plain", set()) == [("plain", None)]
        assert parse_tagged("{{2}}x{{/2}}{{1}}y{{/1}}", {1, 2}) == [("x", 2), ("y", 1)]

    @pytest.mark.parametrize("bad", ["{{1}}unclosed", "x{{/1}}", "{{9}}x{{/9}}", "{{1}}{{2}}x{{/2}}{{/1}}", "{{1}}x{{/2}}"])
    def test_broken_tags_are_rejected(self, bad):
        assert parse_tagged(bad, {1, 2}) is None


class TestPrompt:
    def test_plain(self):
        p = system_prompt("Arabic")
        assert "Translate the given text to Arabic" in p and "Modern Standard Arabic" in p
        assert "tags" not in p and "terminology" not in p and "Arabic script only" not in p

    def test_variants(self):
        assert "{{1}}words{{/1}}" in system_prompt("Arabic", tags=True)
        assert "Arabic script only" in system_prompt("Arabic", strict=True)
        assert "Arabic script only" not in system_prompt("French", strict=True)
        assert "API -> واجهة برمجية" in system_prompt("Arabic", glossary={"API": "واجهة برمجية"})
        assert "[[1]]" in system_prompt("Arabic", batch=True)


# ── the model call ──────────────────────────────────────────────────────────

class Chat:
    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), []

    def __call__(self, model, messages, **kw):
        self.calls.append({"model": model, "messages": messages, **kw})
        out = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        return {"message": {"content": out}}


def arabic_fake(calls=None):
    """Deterministic fake model: answers batched and single requests with clean Arabic."""
    def chat(model, messages, **kw):
        user = messages[-1]["content"]
        if calls is not None:
            calls.append(user)
        if re.match(r"\[\[1\]\]", user):
            items = re.split(r"^\[\[(\d+)\]\]\n", user, flags=re.MULTILINE)[1:]
            out = "\n".join(f"[[{items[i]}]]\nنص {items[i]}" for i in range(0, len(items), 2))
        else:
            out = "نص مترجم"
        return {"message": {"content": out}}
    return chat


class TestTranslateText:
    def test_clean_first_answer_one_call_with_a_token_cap(self, monkeypatch):
        chat = Chat("  مرحبا  ")
        monkeypatch.setattr(ollama_client, "chat", chat)
        assert _translate_text("m", "hello", "Arabic") == "مرحبا"
        assert len(chat.calls) == 1 and chat.calls[0]["temperature"] == 0.0
        assert 0 < chat.calls[0]["num_predict"] <= 3072

    def test_contaminated_answer_is_retried_with_a_different_sample(self, monkeypatch):
        chat = Chat("مرحبا local", "مرحبا محلي")
        monkeypatch.setattr(ollama_client, "chat", chat)
        assert _translate_text("m", "hello local", "Arabic") == "مرحبا محلي"
        assert chat.calls[1]["temperature"] > 0 and "Arabic script only" in chat.calls[1]["messages"][0]["content"]

    def test_falls_back_to_first_answer_if_retry_is_also_bad(self, monkeypatch):
        monkeypatch.setattr(ollama_client, "chat", Chat("مرحبا local", "مرحبا again"))
        assert _translate_text("m", "x", "Arabic") == "مرحبا local"

    def test_non_arabic_target_is_never_retried(self, monkeypatch):
        chat = Chat("Bonjour")
        monkeypatch.setattr(ollama_client, "chat", chat)
        assert _translate_text("m", "hello", "French") == "Bonjour"
        assert len(chat.calls) == 1

    def test_number_only_costs_one_call(self, monkeypatch):
        chat = Chat("2024")
        monkeypatch.setattr(ollama_client, "chat", chat)
        _translate_text("m", "2024", "Arabic")
        assert len(chat.calls) == 1

    def test_broken_tags_trigger_a_retry_then_get_stripped(self, monkeypatch):
        chat = Chat("مرحبا {{7}}عالم", "مرحبا {{1}}عالم")
        monkeypatch.setattr(ollama_client, "chat", chat)
        out = _translate_text("m", "Hello {{1}}world{{/1}}", "Arabic")
        assert len(chat.calls) == 2 and "{{" not in out
        assert "tags" in chat.calls[0]["messages"][0]["content"]

    def test_good_tags_survive(self, monkeypatch):
        monkeypatch.setattr(ollama_client, "chat", Chat("مرحبا {{1}}عالم{{/1}}"))
        assert _translate_text("m", "Hello {{1}}world{{/1}}", "Arabic") == "مرحبا {{1}}عالم{{/1}}"

    def test_only_matching_glossary_terms_are_sent(self, monkeypatch):
        chat = Chat("نص")
        monkeypatch.setattr(ollama_client, "chat", chat)
        _translate_text("m", "Call the API now", "Arabic", glossary={"API": "واجهة برمجية", "Invoice": "فاتورة"})
        system = chat.calls[0]["messages"][0]["content"]
        assert "API" in system and "Invoice" not in system

    def test_think_flag_is_forwarded(self, monkeypatch):
        chat = Chat("نص")
        monkeypatch.setattr(ollama_client, "chat", chat)
        _translate_text("m", "hello", "Arabic", think=False)
        assert chat.calls[0]["think"] is False


# ── the translator: skip / dedupe / cache / batch ───────────────────────────

class TestTranslator:
    def test_untranslatable_segments_never_reach_the_model(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ollama_client, "chat", arabic_fake(calls))
        t = Translator("Arabic")
        assert t(["2024", "https://x.io", "a@b.co"]) == ["2024", "https://x.io", "a@b.co"]
        assert calls == [] and t.stats["skipped"] == 3

    def test_identical_segments_are_translated_once(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ollama_client, "chat", arabic_fake(calls))
        t = Translator("Arabic")
        out = t(["Hello there"] * 5)
        assert out == ["نص مترجم"] * 5 and len(calls) == 1 and t.stats["unique"] == 1

    def test_short_segments_share_one_call(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ollama_client, "chat", arabic_fake(calls))
        t = Translator("Arabic")
        out = t([f"word{chr(97 + i)}" for i in range(10)])
        assert len(calls) == 1 and out == [f"نص {i}" for i in range(1, 11)]

    def test_batches_are_capped_in_size(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ollama_client, "chat", arabic_fake(calls))
        Translator("Arabic")([f"word{chr(97 + i % 26)}{i}" for i in range(30)])
        assert len(calls) == 3  # 12 + 12 + 6

    def test_long_segments_are_translated_alone(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ollama_client, "chat", arabic_fake(calls))
        Translator("Arabic")(["long " * 100, "short one", "short two"])
        assert len(calls) == 2 and not calls[0].startswith("[[1]]")

    def test_a_dropped_marker_only_redoes_that_segment(self, monkeypatch):
        seen = []

        def chat(model, messages, **kw):
            user = messages[-1]["content"]
            seen.append(user)
            if user.startswith("[[1]]"):
                return {"message": {"content": "[[1]]\nواحد\n[[3]]\nثلاثة"}}  # segment 2 missing
            return {"message": {"content": "اثنان"}}

        monkeypatch.setattr(ollama_client, "chat", chat)
        assert Translator("Arabic")(["one", "two", "three"]) == ["واحد", "اثنان", "ثلاثة"]
        assert len(seen) == 2 and seen[1] == "two"

    def test_a_leaked_script_inside_a_batch_is_redone_alone(self, monkeypatch):
        def chat(model, messages, **kw):
            user = messages[-1]["content"]
            if user.startswith("[[1]]"):
                return {"message": {"content": "[[1]]\nواحد\n[[2]]\ntwo leaked"}}
            return {"message": {"content": "اثنان"}}

        monkeypatch.setattr(ollama_client, "chat", chat)
        assert Translator("Arabic")(["one", "two"]) == ["واحد", "اثنان"]

    def test_cache_makes_a_second_run_free(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(ollama_client, "chat", arabic_fake(calls))
        cache_file = tmp_path / "c.json"
        first_cache = TranslationCache(cache_file)
        Translator("Arabic", cache=first_cache)(["Hello there", "Good morning"])
        first_cache.save()
        n = len(calls)
        t2 = Translator("Arabic", cache=TranslationCache(cache_file))
        out = t2(["Hello there", "Good morning"])
        assert len(calls) == n and t2.stats["cached"] == 2 and out == ["نص 1", "نص 2"]

    def test_cache_key_depends_on_model_language_and_glossary(self, tmp_path):
        c = TranslationCache(tmp_path / "c.json")
        a = Translator("Arabic", model_tag="m1", cache=c)._key("x")
        assert a != Translator("Arabic", model_tag="m2", cache=c)._key("x")
        assert a != Translator("French", model_tag="m1", cache=c)._key("x")
        assert a != Translator("Arabic", model_tag="m1", glossary={"x": "y"}, cache=c)._key("x")

    def test_corrupt_cache_file_is_ignored(self, tmp_path):
        f = tmp_path / "c.json"
        f.write_text("{ not json", encoding="utf-8")
        assert TranslationCache(f).get("k") is None

    def test_uses_the_model_configured_for_tarjuman(self):
        assert Translator("Arabic").tag == tarjuman.load_registry()["tarjuman"]["ollama_tag"]


# ── documents ───────────────────────────────────────────────────────────────

def make_doc(build) -> bytes:
    d = docx.Document()
    build(d)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def load(data: bytes):
    return docx.Document(io.BytesIO(data))


def formatted_chars(paragraph):
    return [(ch, bool(r.bold), bool(r.italic)) for r in paragraph.runs for ch in r.text]


def all_paragraphs(d):
    out = list(d.paragraphs)
    for t in d.tables:
        for row in t.rows:
            for cell in row.cells:
                out.extend(cell.paragraphs)
    return out


def rich_paragraph(d):
    p = d.add_paragraph("The ")
    p.add_run("first party").bold = True
    p.add_run(" shall ")
    p.add_run("deliver").italic = True
    p.add_run(" on time.")
    return p


class TestRoundTrip:
    """Skill step 1: file in, same file out, with the model taken out of the loop."""

    def test_identity_translation_preserves_text_and_formatting(self):
        def build(d):
            d.add_heading("Contract", 1)
            rich_paragraph(d)
            d.add_paragraph("")
            t = d.add_table(rows=2, cols=2)
            t.cell(0, 0).text = "Name"
            t.cell(0, 1).paragraphs[0].add_run("Bold cell").bold = True
            t.cell(1, 0).merge(t.cell(1, 1)).text = "Merged cell"
            d.sections[0].header.paragraphs[0].text = "Header text"
            d.sections[0].footer.paragraphs[0].text = "Footer text"

        src = make_doc(build)
        out = load(translate_docx(src, "French", translate=lambda texts: texts))
        orig = load(src)
        assert [p.text for p in all_paragraphs(out)] == [p.text for p in all_paragraphs(orig)]
        for a, b in zip(all_paragraphs(orig), all_paragraphs(out)):
            assert formatted_chars(a) == formatted_chars(b)
            assert a.style.name == b.style.name
        assert out.sections[0].header.paragraphs[0].text == "Header text"
        assert out.sections[0].footer.paragraphs[0].text == "Footer text"
        assert len(out.tables) == 1 and len(out.tables[0].rows) == 2

    def test_ltr_target_gets_no_rtl_markup(self):
        src = make_doc(lambda d: d.add_paragraph("Hello"))
        out = load(translate_docx(src, "French", translate=lambda t: ["Bonjour"]))
        assert out.paragraphs[0]._p.pPr is None or out.paragraphs[0]._p.pPr.find(qn("w:bidi")) is None

    def test_translator_returning_wrong_count_is_an_error(self):
        src = make_doc(lambda d: d.add_paragraph("Hello"))
        with pytest.raises(RuntimeError):
            translate_docx(src, "Arabic", translate=lambda texts: [])


class TestInlineFormatting:
    def test_the_model_sees_placeholders_and_formatting_lands_on_the_right_words(self):
        seen = []

        def fake(texts):
            seen.extend(texts)
            return ["يلتزم {{1}}الطرف الأول{{/1}} بالتسليم {{2}}في الوقت{{/2}} المحدد."]

        src = make_doc(rich_paragraph)
        out = load(translate_docx(src, "Arabic", translate=fake))
        assert seen == ["The {{1}}first party{{/1}} shall {{2}}deliver{{/2}} on time."]
        p = out.paragraphs[0]
        assert "{{" not in p.text and p.text == "يلتزم الطرف الأول بالتسليم في الوقت المحدد."
        bold = "".join(r.text for r in p.runs if r.bold)
        italic = "".join(r.text for r in p.runs if r.italic)
        assert bold == "الطرف الأول" and italic == "في الوقت"
        assert all(r.font.rtl for r in p.runs)

    def test_most_common_formatting_stays_untagged(self):
        seen = []
        def build(d):
            d.add_paragraph().add_run("all bold").bold = True

        src = make_doc(build)
        translate_docx(src, "French", translate=lambda t: seen.extend(t) or t)
        assert seen == ["all bold"]

    def test_broken_tags_degrade_to_plain_text_instead_of_corrupting(self):
        src = make_doc(rich_paragraph)
        out = load(translate_docx(src, "Arabic", translate=lambda t: ["نص {{9}}مكسور {{1}}"]))
        assert "{{" not in out.paragraphs[0].text and out.paragraphs[0].text.startswith("نص")

    def test_leading_and_trailing_whitespace_survive(self):
        src = make_doc(lambda d: d.add_paragraph("  padded  "))
        out = load(translate_docx(src, "French", translate=lambda t: ["rembourré"]))
        assert out.paragraphs[0].text == "  rembourré  "


class TestStructure:
    def test_tables_headers_footers_are_translated_and_merged_cells_once(self):
        def build(d):
            t = d.add_table(rows=2, cols=2)
            t.cell(0, 0).text = "Name"
            t.cell(0, 1).text = "Date"
            t.cell(1, 0).merge(t.cell(1, 1)).text = "Merged"
            inner = t.cell(0, 0).add_table(1, 1)
            inner.cell(0, 0).text = "Nested"
            d.sections[0].header.paragraphs[0].text = "Head"
            d.sections[0].footer.paragraphs[0].text = "Foot"

        seen = []
        out = load(translate_docx(make_doc(build), "French", translate=lambda t: seen.extend(t) or [f"[{x}]" for x in t]))
        assert set(seen) == {"Name", "Date", "Merged", "Nested", "Head", "Foot"}
        assert seen.count("Merged") == 1
        assert out.sections[0].header.paragraphs[0].text == "[Head]"
        assert out.tables[0].cell(0, 1).text == "[Date]" and out.tables[0].cell(1, 0).text == "[Merged]"

    def test_a_header_linked_to_the_previous_section_is_not_translated_twice(self):
        def build(d):
            d.sections[0].header.paragraphs[0].text = "Shared header"
            d.add_section()
            assert d.sections[1].header.is_linked_to_previous

        seen = []
        translate_docx(make_doc(build), "French", translate=lambda t: seen.extend(t) or t)
        assert seen.count("Shared header") == 1

    def test_hyperlink_paragraph_is_translated_without_losing_the_link(self):
        def build(d):
            p = d.add_paragraph("Visit ")
            link = OxmlElement("w:hyperlink")
            r = OxmlElement("w:r")
            t = OxmlElement("w:t")
            t.text = "our site"
            r.append(t)
            link.append(r)
            p._p.append(link)

        seen = []
        out = load(translate_docx(make_doc(build), "French", translate=lambda t: seen.extend(t) or ["Visitez notre site"]))
        assert seen == ["Visit our site"]
        assert len(out.paragraphs[0]._p.findall(qn("w:hyperlink"))) == 1
        assert "Visitez notre site" in "".join(t.text for t in out.paragraphs[0]._p.iter(qn("w:t")))

    def test_images_survive(self):
        def build(d):
            p = d.add_paragraph("Caption ")
            p.add_run().add_picture(io.BytesIO(PNG))

        out = load(translate_docx(make_doc(build), "French", translate=lambda t: ["Légende "]))
        assert len(out.paragraphs[0]._p.findall(".//" + qn("w:drawing"))) == 1
        assert out.paragraphs[0].text.startswith("Légende")

    def test_blank_paragraphs_cost_nothing_and_stay_blank(self):
        seen = []
        src = make_doc(lambda d: (d.add_paragraph(""), d.add_paragraph("Text")))
        out = load(translate_docx(src, "French", translate=lambda t: seen.extend(t) or t))
        assert seen == ["Text"] and out.paragraphs[0].text == ""


class TestRtl:
    @staticmethod
    def _tags(el):
        return [c.tag.split("}")[1] for c in el]

    def test_paragraph_and_run_direction_and_alignment_left_alone(self):
        def build(d):
            p = d.add_paragraph("Centered")
            p.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_after = docx.shared.Pt(6)
            d.add_paragraph("Plain")

        out = load(translate_docx(make_doc(build), "Arabic", translate=lambda t: ["مركز", "عادي"]))
        centered, plain = out.paragraphs
        assert centered.alignment == docx.enum.text.WD_ALIGN_PARAGRAPH.CENTER  # source alignment is kept
        assert plain.alignment is None, "forcing RIGHT on a bidi paragraph would render it on the left in Word"
        tags = self._tags(centered._p.pPr)
        assert tags.index("bidi") < tags.index("spacing") < tags.index("jc"), tags  # schema order
        assert all(r.font.rtl for r in centered.runs)

    def test_tables_are_mirrored_with_bidi_visual_in_schema_order(self):
        def build(d):
            d.add_table(rows=1, cols=2).cell(0, 0).text = "A"

        src = make_doc(build)
        out = load(translate_docx(src, "Arabic", translate=lambda t: ["أ"] * len(t)))
        tags = self._tags(out.tables[0]._tbl.tblPr)
        assert "bidiVisual" in tags and tags.index("bidiVisual") < tags.index("tblW"), tags

    @pytest.mark.parametrize("lang", ["Arabic", "العربية", "arabic", "Hebrew", "Persian", "Urdu"])
    def test_rtl_languages_are_recognised_case_insensitively(self, lang):
        assert tarjuman._is_rtl(lang)

    @pytest.mark.parametrize("lang", ["French", "English", "Turkish"])
    def test_ltr_languages(self, lang):
        assert not tarjuman._is_rtl(lang)

    def test_numeric_paragraphs_are_still_made_rtl(self):
        src = make_doc(lambda d: d.add_paragraph("2024"))
        out = load(translate_docx(src, "Arabic", translate=lambda t: t))
        assert out.paragraphs[0]._p.pPr.find(qn("w:bidi")) is not None


class TestEndToEndWithTheRealTranslator:
    def test_dedupe_and_batching_cut_model_calls(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tarjuman, "CACHE_PATH", tmp_path / "cache.json")
        calls = []
        monkeypatch.setattr(ollama_client, "chat", arabic_fake(calls))

        def build(d):
            for _ in range(4):
                d.add_paragraph("Confidential")
            t = d.add_table(rows=6, cols=1)
            for i in range(6):
                t.cell(i, 0).text = f"Row label {chr(65 + i)}"
            d.add_paragraph("2024")

        data, stats = translate_docx_with_stats(make_doc(build), "Arabic")
        assert stats["segments"] == 11 and stats["unique"] == 8 and stats["skipped"] == 1
        assert len(calls) == 1, "7 short unique segments should share a single batched call"
        assert stats["llm_calls"] == 1

    def test_second_translation_of_the_same_document_is_free(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tarjuman, "CACHE_PATH", tmp_path / "cache.json")
        calls = []
        monkeypatch.setattr(ollama_client, "chat", arabic_fake(calls))
        src = make_doc(lambda d: (d.add_paragraph("Hello there"), d.add_paragraph("Good morning")))
        first, _ = translate_docx_with_stats(src, "Arabic")
        n = len(calls)
        second, stats = translate_docx_with_stats(src, "Arabic")
        assert len(calls) == n and stats["cached"] == 2 and stats["llm_calls"] == 0
        assert [p.text for p in load(first).paragraphs] == [p.text for p in load(second).paragraphs]

    def test_glossary_changes_the_cache_key_so_results_dont_leak_between_glossaries(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tarjuman, "CACHE_PATH", tmp_path / "cache.json")
        calls = []
        monkeypatch.setattr(ollama_client, "chat", arabic_fake(calls))
        src = make_doc(lambda d: d.add_paragraph("Call the API"))
        translate_docx_with_stats(src, "Arabic")
        _, stats = translate_docx_with_stats(src, "Arabic", glossary={"API": "واجهة"})
        assert stats["cached"] == 0


# ── tags never reach the translation; glossary ──────────────────────────────

from tools.tarjuman import GLOSSARY_PATH, RETAG_SYSTEM, _glossary_hits, _label_key, default_glossary


class Scripted:
    """Fake model: plain requests get `plain`; the re-tagging request gets `retagged`."""

    def __init__(self, plain, retagged=None):
        self.plain, self.retagged, self.calls = plain, retagged, []

    def __call__(self, model, messages, **kw):
        self.calls.append({"system": messages[0]["content"], "user": messages[-1]["content"], **kw})
        is_retag = messages[0]["content"] == RETAG_SYSTEM
        return {"message": {"content": self.retagged if is_retag else self.plain}}


SRC = "The {{1}}first party{{/1}} shall deliver."
TR = "الطرف الأول يسلّم البضاعة."


class TestTagsNeverReachTheTranslation:
    def test_translate_clean_then_copy_the_tags_onto_the_result(self, monkeypatch):
        chat = Scripted(TR, "{{1}}الطرف الأول{{/1}} يسلّم البضاعة.")
        monkeypatch.setattr(ollama_client, "chat", chat)
        t = Translator("Arabic")
        assert t([SRC]) == ["{{1}}الطرف الأول{{/1}} يسلّم البضاعة."]
        assert chat.calls[0]["user"] == "The first party shall deliver." and "{{" not in chat.calls[0]["system"]
        assert "Source: " + SRC in chat.calls[1]["user"] and t.stats["retagged"] == 1 and t.stats["llm_calls"] == 2

    def test_a_retag_that_changes_any_word_is_rejected(self, monkeypatch):
        chat = Scripted(TR, "{{1}}الحزب الأول{{/1}} يسلّم البضاعة.")   # the very mistake tags used to cause
        monkeypatch.setattr(ollama_client, "chat", chat)
        t = Translator("Arabic")
        assert t([SRC]) == [TR] and t.stats["retag_failed"] == 1

    @pytest.mark.parametrize("bad", ["{{1}}الطرف الأول يسلّم البضاعة.", "{{9}}الطرف الأول{{/9}} يسلّم البضاعة.", "", TR + " إضافة"])
    def test_broken_or_padded_retags_fall_back_to_the_clean_translation(self, monkeypatch, bad):
        monkeypatch.setattr(ollama_client, "chat", Scripted(TR, bad))
        assert Translator("Arabic")([SRC]) == [TR]

    def test_whitespace_differences_are_tolerated(self, monkeypatch):
        monkeypatch.setattr(ollama_client, "chat", Scripted(TR, "{{1}}الطرف  الأول{{/1}}\nيسلّم البضاعة."))
        assert "{{1}}" in Translator("Arabic")([SRC])[0]

    def test_segments_without_letters_keep_their_original_tags_and_cost_nothing(self, monkeypatch):
        chat = Scripted("x")
        monkeypatch.setattr(ollama_client, "chat", chat)
        assert Translator("Arabic")(["{{1}}42{{/1}}"]) == ["{{1}}42{{/1}}"] and chat.calls == []

    def test_untagged_segments_never_trigger_a_retag(self, monkeypatch):
        chat = Scripted(TR)
        monkeypatch.setattr(ollama_client, "chat", chat)
        Translator("Arabic")(["Plain sentence here."])
        assert len(chat.calls) == 1

    def test_the_retag_result_is_cached_too(self, monkeypatch, tmp_path):
        chat = Scripted(TR, "{{1}}الطرف الأول{{/1}} يسلّم البضاعة.")
        monkeypatch.setattr(ollama_client, "chat", chat)
        cache = TranslationCache(tmp_path / "c.json")
        Translator("Arabic", cache=cache)([SRC])
        n = len(chat.calls)
        assert Translator("Arabic", cache=cache)([SRC]) == ["{{1}}الطرف الأول{{/1}} يسلّم البضاعة."]
        assert len(chat.calls) == n

    def test_two_tagged_paragraphs_share_the_batched_clean_translation_call(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ollama_client, "chat", arabic_fake(calls))
        Translator("Arabic")(["First {{1}}one{{/1}} here", "Second {{1}}two{{/1}} here"])
        assert calls[0].startswith("[[1]]") and "{{" not in calls[0]


class TestGlossary:
    def test_an_exact_label_is_replaced_without_a_model_call(self, monkeypatch):
        chat = Scripted("x")
        monkeypatch.setattr(ollama_client, "chat", chat)
        t = Translator("Arabic")
        assert t(["Confidential", "confidential:", "Total ", "N/A"]) == ["سري", "سري", "الإجمالي", "غير منطبق"]
        assert chat.calls == [] and t.stats["glossary"] == 4

    def test_a_term_inside_a_sentence_becomes_mandatory_terminology_in_the_prompt(self, monkeypatch):
        chat = Scripted("نص")
        monkeypatch.setattr(ollama_client, "chat", chat)
        Translator("Arabic")(["This document is Confidential and long enough to need the model."])
        assert "Confidential -> سري" in chat.calls[0]["system"]

    def test_terms_match_whole_words_only(self):
        assert _glossary_hits({"No": "لا", "Note": "ملاحظة"}, "Not a note.") == {"Note": "ملاحظة"}
        assert _glossary_hits({"N/A": "غير منطبق"}, "Value: N/A today") == {"N/A": "غير منطبق"}
        assert _glossary_hits({"Total": "الإجمالي"}, "Subtotals") == {}

    def test_request_terms_override_the_defaults(self, monkeypatch):
        monkeypatch.setattr(ollama_client, "chat", Scripted("x"))
        assert Translator("Arabic", glossary={"Confidential": "خاص"})(["Confidential"]) == ["خاص"]

    def test_defaults_apply_only_to_their_language(self, monkeypatch):
        chat = Scripted("Confidentiel")
        monkeypatch.setattr(ollama_client, "chat", chat)
        assert Translator("French")(["Confidential"]) == ["Confidentiel"] and len(chat.calls) == 1

    @pytest.mark.parametrize("lang, has_terms", [("Arabic", True), ("arabic", True), ("العربية", False), ("French", False)])
    def test_default_glossary_lookup(self, lang, has_terms):
        assert bool(default_glossary(lang)) is has_terms

    def test_label_key_normalisation(self):
        assert _label_key("  Page   Footer: ") == "page footer" and _label_key("Total.") == "total"

    def test_the_shipped_glossary_is_clean(self):
        import yaml

        data = yaml.safe_load(GLOSSARY_PATH.read_text(encoding="utf-8"))
        terms = data["Arabic"]
        assert len(terms) >= 40
        keys = [_label_key(str(k)) for k in terms]
        assert len(keys) == len(set(keys)), "two entries normalise to the same label"
        for k, v in terms.items():
            assert str(k).strip() and any("\u0600" <= ch <= "\u06ff" for ch in str(v)), (k, v)
        assert terms[True if False else "Yes"] == "نعم" and terms["No"] == "لا"  # YAML must not turn Yes/No into booleans

    def test_the_glossary_changes_the_cache_key(self):
        assert Translator("Arabic")._glossary_key != Translator("French")._glossary_key
