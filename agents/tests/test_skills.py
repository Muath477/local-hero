import pytest

from orchestrator import ollama_client
from orchestrator.hardware import load_registry
from orchestrator.skills import SKILLS_DIR, Skill, load_skills, match_skills, parse_skill, render

GOOD = """---
name: demo
description: a demo
roles: coder, writer_general
triggers: خطأ, Bug
---
Do the thing.
Second line.
"""


class TestParsing:
    def test_parses_header_and_body(self):
        s = parse_skill(GOOD)
        assert s == Skill("demo", "a demo", ("coder", "writer_general"), ("خطأ", "Bug"), "Do the thing.\nSecond line.")

    def test_inline_comments_and_bom(self):
        s = parse_skill("﻿---\nname: x\nroles: coder  # only code\ntriggers: a\n---\nbody")
        assert s.roles == ("coder",) and s.name == "x"

    def test_roles_are_optional(self):
        assert parse_skill("---\nname: x\ntriggers: a\n---\nbody").roles == ()

    @pytest.mark.parametrize("text", [
        "no header at all", "---\nname: x\ntriggers: a\nbody without closing", "---\ntriggers: a\n---\nbody",
        "---\nname: x\n---\nbody", "---\nname: x\ntriggers: a\n---\n", "",
    ])
    def test_malformed_skills_are_rejected(self, text):
        with pytest.raises(ValueError):
            parse_skill(text)


class TestLoading:
    def test_bad_files_are_skipped_not_fatal(self, tmp_path):
        (tmp_path / "good.md").write_text(GOOD, encoding="utf-8")
        (tmp_path / "bad.md").write_text("garbage", encoding="utf-8")
        (tmp_path / "notes.txt").write_text(GOOD, encoding="utf-8")
        assert [s.name for s in load_skills(tmp_path)] == ["demo"]

    def test_missing_directory_is_empty(self, tmp_path):
        assert load_skills(tmp_path / "nope") == []

    def test_shipped_skills_are_valid_and_reference_real_roles(self):
        skills = load_skills(SKILLS_DIR)
        assert len(skills) >= 6
        roles = set(load_registry())
        for s in skills:
            assert set(s.roles) <= roles, s.name
            assert s.triggers and len(s.body) < 1500, f"{s.name}: skills share a small model's context"
        assert len({s.name for s in skills}) == len(skills)


class TestMatching:
    SKILLS = [
        Skill("a", "", ("coder",), ("bug", "خطأ"), "A"),
        Skill("b", "", (), ("bug",), "B"),
        Skill("c", "", ("writer_general",), ("bug",), "C"),
        Skill("d", "", ("coder",), ("خطأ", "bug", "error"), "D"),
    ]

    def test_role_filter_and_case_insensitive_substring(self):
        assert [s.name for s in match_skills(self.SKILLS, "coder", "I hit a BUG")] == ["a", "b"]

    def test_more_trigger_hits_rank_first(self):
        assert [s.name for s in match_skills(self.SKILLS, "coder", "خطأ bug error", max_skills=1)] == ["d"]

    def test_no_match_and_cap(self):
        assert match_skills(self.SKILLS, "coder", "hello") == []
        assert len(match_skills(self.SKILLS, "coder", "bug", max_skills=1)) == 1

    def test_star_trigger_is_always_on_for_its_roles_but_ranks_last(self):
        skills = [Skill("always", "", ("coder",), ("*",), "A"), Skill("specific", "", ("coder",), ("bug",), "S")]
        assert [s.name for s in match_skills(skills, "coder", "hello")] == ["always"]
        assert [s.name for s in match_skills(skills, "coder", "a bug")] == ["specific", "always"]
        assert match_skills(skills, "writer_general", "bug") == []

    def test_render(self):
        assert render([Skill("a", "", (), ("x",), "Body A")]) == "\n\n## Skill: a\nBody A"
        assert render([]) == ""

    @pytest.mark.parametrize("role, message, expected", [
        ("writer_general", "اكتب لي إيميل رسمي للمدير", "formal-arabic-writing"),
        ("coder", "ليش يطلع لي TypeError في الكود؟", "debugging-code"),
        ("coder", "اكتب دالة بايثون تعكس النص", "code-writing"),
        ("researcher_rag", "ايش يقول التقرير عن المبيعات؟", "document-qa"),
        ("researcher_rag", "متى يفتح فرع جدة؟", "document-qa"),
        ("tool_caller", "ابحث عن أسعار الذهب", "web-research"),
        ("tool_caller", "ترجم الملف contract.docx للعربية", "document-translation"),
    ])
    def test_shipped_skills_fire_on_their_use_cases(self, role, message, expected):
        assert expected in [s.name for s in match_skills(load_skills(), role, message)]

    def test_a_skill_never_leaks_to_another_role(self):
        assert match_skills(load_skills(), "writer_general", "ليش يطلع لي خطأ traceback bug") == []


class TestWiring:
    def test_matched_skills_join_the_system_prompt_and_are_reported(self, make_manager, monkeypatch):
        seen = []
        monkeypatch.setattr(ollama_client, "chat",
                            lambda model, messages, **kw: seen.append(messages) or {"message": {"content": "ok"}})
        m = make_manager()
        out = m.run("coder", "ليش يطلع لي خطأ في الكود؟")
        system = seen[0][0]["content"]
        assert "## Skill: debugging-code" in system and system.startswith("أنت مبرمج خبير")
        assert out["skills"] == ["debugging-code"]

    def test_no_match_leaves_the_prompt_untouched(self, make_manager, monkeypatch):
        seen = []
        monkeypatch.setattr(ollama_client, "chat",
                            lambda model, messages, **kw: seen.append(messages) or {"message": {"content": "ok"}})
        m = make_manager()
        out = m.run("writer_general", "مرحبا")
        assert "## Skill" not in seen[0][0]["content"] and out["skills"] == []

    def test_skills_reach_the_tool_loop_too(self, make_manager, monkeypatch):
        seen = []
        monkeypatch.setattr(ollama_client, "chat",
                            lambda model, messages, **kw: seen.append(messages) or {"message": {"content": "done"}})
        out = make_manager().run("tool_caller", "ابحث عن أخبار اليوم")
        assert "## Skill: web-research" in seen[0][0]["content"] and out["skills"] == ["web-research"]
