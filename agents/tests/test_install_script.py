import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "install_models", Path(__file__).resolve().parent.parent / "scripts" / "install_models.py")
install_models = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install_models)


def test_every_tag_in_models_yaml_is_installable_by_the_script():
    """models.yaml and the installer must not drift apart — a new tag needs a manifest entry."""
    missing = [t for t in install_models.required_tags() if t not in install_models.MANIFEST]
    assert not missing, f"add these to scripts/install_models.py MANIFEST: {missing}"


def test_required_tags_include_fallbacks_and_map_to_roles():
    tags = install_models.required_tags({
        "tiers": {}, "coder": {"ollama_tag": "a", "fallback_tag": "b"}, "router": {"ollama_tag": "a"}})
    assert tags == {"a": ["coder", "router"], "b": ["coder"]}


@pytest.mark.parametrize("tag, spec", sorted(install_models.MANIFEST.items()))
def test_manifest_entries_are_complete(tag, spec):
    assert spec["route"] in ("registry", "gguf", "hf") and spec["gb"] > 0 and spec["role"]
    if spec["route"] == "gguf":
        assert spec["repo"] and spec["file"].endswith(".gguf") and spec["bytes"] > 1_000_000
    if spec["route"] == "hf":
        assert tag.startswith("hf.co/") and spec["library_alternative"]


def test_nothing_is_downloaded_without_yes(monkeypatch, capsys):
    monkeypatch.setattr(install_models.shutil, "which", lambda name: "ollama")
    monkeypatch.setattr(install_models, "installed", lambda: set())
    called = []
    monkeypatch.setattr(install_models, "install", lambda tag, spec: called.append(tag) or True)
    assert install_models.main([]) == 1
    assert called == [] and "Re-run with --yes" in capsys.readouterr().out


def test_yes_installs_only_the_missing_ones(monkeypatch):
    needed = install_models.required_tags()
    keep = next(iter(needed))
    monkeypatch.setattr(install_models.shutil, "which", lambda name: "ollama")
    monkeypatch.setattr(install_models, "installed", lambda: {keep})
    called = []
    monkeypatch.setattr(install_models, "install", lambda tag, spec: called.append(tag) or True)
    assert install_models.main(["--yes"]) == 0
    assert keep not in called and set(called) == set(needed) - {keep}


def test_a_failed_install_is_reported(monkeypatch):
    monkeypatch.setattr(install_models.shutil, "which", lambda name: "ollama")
    monkeypatch.setattr(install_models, "installed", lambda: set())
    monkeypatch.setattr(install_models, "install", lambda tag, spec: False)
    assert install_models.main(["--yes"]) == 1
