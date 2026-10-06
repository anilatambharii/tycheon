"""The release plumbing is configuration: check it says what the release process relies on."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _text(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding="utf-8")


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _version() -> str:
    return tomllib.loads(_text("pyproject.toml"))["project"]["version"]


def test_the_changelog_has_a_section_for_the_current_version() -> None:
    changelog = _text("CHANGELOG.md")
    assert f"## [{_version()}]" in changelog
    assert "Not investment advice" in changelog


def test_release_please_agrees_with_the_package_version() -> None:
    manifest = json.loads(_text(".release-please-manifest.json"))
    config = json.loads(_text("release-please-config.json"))
    assert manifest["."] == _version()
    assert config["release-type"] == "python" and config["include-v-in-tag"] is True
    # plain `v0.1.0` tags, so they match the `v*` trigger and the version check in publish.yml
    assert config["include-component-in-tag"] is False
    assert config["packages"]["."]["changelog-path"] == "CHANGELOG.md"


def test_publishing_uses_trusted_publishing_and_stores_no_token() -> None:
    text = _text(".github/workflows/publish.yml")
    wf = _workflow("publish.yml")
    assert "pypa/gh-action-pypi-publish" in text
    for job in ("testpypi", "pypi"):
        assert wf["jobs"][job]["permissions"]["id-token"] == "write"
        assert wf["jobs"][job]["environment"] == job
    # no long-lived credential anywhere in the workflow
    assert not re.search(r"(password|api[-_]?token)\s*:", text, re.IGNORECASE)
    assert "secrets." not in text


def test_a_real_pypi_upload_only_happens_for_a_version_tag() -> None:
    wf = _workflow("publish.yml")
    assert "refs/tags/v" in wf["jobs"]["pypi"]["if"]
    # PyPI depends on the build only, so skipping TestPyPI never blocks a release...
    assert wf["jobs"]["pypi"]["needs"] == "build"
    # ...and the TestPyPI dry run happens only when someone starts it by hand
    assert wf["jobs"]["testpypi"]["if"] == "github.event_name == 'workflow_dispatch'"
    steps = " ".join(str(s) for s in wf["jobs"]["build"]["steps"])
    assert "pyproject.toml" in steps and "GITHUB_REF_NAME" in steps  # tag must match version


def test_the_publish_workflow_checks_the_built_distribution() -> None:
    text = _text(".github/workflows/publish.yml")
    assert "twine check --strict" in text and "uv build" in text


def test_docs_are_built_strictly_and_deployed_only_from_main() -> None:
    text = _text(".github/workflows/docs.yml")
    wf = _workflow("docs.yml")
    assert "mkdocs build --strict" in text
    assert "refs/heads/main" in wf["jobs"]["deploy"]["if"]
    assert wf["jobs"]["deploy"]["permissions"] == {"pages": "write", "id-token": "write"}


@pytest.mark.parametrize("name", ["publish.yml", "docs.yml", "release-please.yml"])
def test_release_workflows_request_least_privilege(name: str) -> None:
    wf = _workflow(name)
    top = wf.get("permissions", {})
    assert top.get("contents") in ("read", "write")
    assert "id-token" not in top  # only individual jobs that publish get the OIDC token


def test_the_docs_navigation_includes_the_leaderboard_and_methodology() -> None:
    nav = _text("mkdocs.yml")
    for page in ("leaderboard/index.md", "benchmark-methodology.md", "releasing.md"):
        assert page in nav and (ROOT / "docs" / page).is_file()


def test_the_tutorials_exist_and_say_what_they_cover() -> None:
    kronos = _text("docs/tutorials/calibrated-kronos.md")
    bench = _text("docs/tutorials/run-the-benchmark.md")
    assert "calibrated_kronos.py" in kronos and "Not investment advice" in kronos
    assert "make benchmark-small" in bench and "--data-dir" in bench


def test_the_launch_draft_is_marked_as_a_draft_and_carries_the_disclaimer() -> None:
    post = _text("docs/blog/launch-draft.md")
    assert post.lstrip().lower().startswith("<!-- draft")
    assert "Kronos forecasts the path; Tycheon tells you how much to trust it" in post
    assert "Not investment advice" in post


def test_the_pypi_readme_has_no_repo_relative_links_after_the_build_rewrite() -> None:
    """PyPI does not resolve repo paths: the build must rewrite every relative link and image."""
    config = tomllib.loads(_text("pyproject.toml"))
    assert "readme" in config["project"]["dynamic"] and "readme" not in config["project"]
    hook = config["tool"]["hatch"]["metadata"]["hooks"]["fancy-pypi-readme"]
    assert hook["content-type"] == "text/markdown"
    assert hook["fragments"] == [{"path": "README.md"}]
    assert "hatch-fancy-pypi-readme" in " ".join(config["build-system"]["requires"])

    text = _text("README.md")
    for sub in hook["substitutions"]:
        text = re.sub(sub["pattern"], sub["replacement"], text)
    targets = re.findall(r"\]\(([^)\s]+)\)", text)
    relative = [t for t in targets if not t.startswith(("http://", "https://", "#", "mailto:"))]
    assert targets and not relative, relative
    # images must point at the raw file, not at the HTML page that wraps it
    for image in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", text):
        assert "/blob/" not in image
