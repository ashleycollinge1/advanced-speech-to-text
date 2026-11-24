import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("release_notes", ROOT / "scripts" / "release_notes.py")
rn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rn)

DIGEST = "sha256:" + "b" * 64


def test_render_includes_changes_image_upgrade_and_compare_link():
    notes = rn.render("v1.2.0", "ghcr.io/Owner/Repo", DIGEST, "owner/repo", "v1.1.0", ["Add X", "Fix Y"])
    assert "- Add X\n- Fix Y" in notes
    assert "`ghcr.io/owner/repo:1.2.0`" in notes and DIGEST in notes
    assert "docker compose pull && docker compose up -d" in notes
    assert "compare/v1.1.0...v1.2.0" in notes


def test_render_first_release_has_no_compare_link():
    notes = rn.render("v0.1.0", "ghcr.io/o/r", DIGEST, "o/r", None, [])
    assert "Full changelog" not in notes and "- Maintenance release." in notes


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_commit_subjects_since_previous_tag_skip_release_commits(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    for msg, tag in [("first", "v1.0.0"), ("release: compose file for v1.0.0", None), ("Add feature", None),
                     ("Fix bug", "v1.1.0")]:  # fmt: skip
        _git(tmp_path, "commit", "-q", "--allow-empty", "-m", msg)
        if tag:
            _git(tmp_path, "tag", tag)
    monkeypatch.chdir(tmp_path)
    assert rn.previous_tag("v1.1.0") == "v1.0.0"
    assert rn.commit_subjects("v1.0.0", "v1.1.0") == ["Fix bug", "Add feature"]
    assert rn.previous_tag("v1.0.0") is None
