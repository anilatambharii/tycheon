"""The public-launch files: good first issues, issue forms, README numbers, Scorecard workflow.

Fast and offline. These check that what the launch materials *say* is still true of the repository:
every path an issue cites exists, every benchmark number in the README is in the result file it
cites, the forms parse, and the new workflow is pinned by commit SHA.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DISCLAIMER = "For research and risk analytics. Not investment advice."
RESULT_FILE = "benchmarks/results/small/0.1.0.json"

_PATH = re.compile(r"`([A-Za-z0-9_.\-/]+(?:::[A-Za-z0-9_]+)?)`(\s*\(new file\))?")
_PATH_SUFFIXES = (".py", ".md", ".yml", ".yaml", ".json", ".toml", ".sh", ".mjs", ".txt")
_ROOT_FILES = {"Makefile", "Dockerfile", "LICENSE"}


def _text(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding="utf-8")


class _Loader(yaml.SafeLoader):
    """SafeLoader that tolerates the ``!!python/name:`` tags mkdocs.yml uses (never evaluated)."""


_Loader.add_multi_constructor("tag:yaml.org,2002:python/", lambda loader, suffix, node: None)


def _yaml(relpath: str) -> Any:
    return yaml.load(_text(relpath), Loader=_Loader)  # noqa: S506 - _Loader is a SafeLoader


# ------------------------------------------------------------------ good first issues
@pytest.fixture(scope="module")
def issues() -> list[dict[str, Any]]:
    loaded = _yaml("docs/launch/good-first-issues.yaml")
    assert isinstance(loaded, list)
    return loaded


def test_issues_parse_with_the_required_keys(issues: list[dict[str, Any]]) -> None:
    assert len(issues) >= 20
    for issue in issues:
        assert set(issue) == {"title", "labels", "body"}, issue.get("title")
        assert isinstance(issue["title"], str) and issue["title"].strip()
        assert isinstance(issue["labels"], list) and issue["labels"]
        assert isinstance(issue["body"], str) and issue["body"].strip()


def test_issue_titles_are_unique(issues: list[dict[str, Any]]) -> None:
    titles = [i["title"] for i in issues]
    assert len(titles) == len(set(titles))


def test_every_issue_has_the_good_first_issue_label_and_only_defined_labels(
    issues: list[dict[str, Any]],
) -> None:
    defined = {label["name"] for label in _yaml("docs/launch/labels.yaml")}
    for issue in issues:
        assert "good first issue" in issue["labels"], issue["title"]
        unknown = set(issue["labels"]) - defined
        assert not unknown, f"{issue['title']}: labels not in labels.yaml: {unknown}"


def test_labels_file_is_well_formed() -> None:
    labels = _yaml("docs/launch/labels.yaml")
    names = [label["name"] for label in labels]
    assert len(names) == len(set(names))
    for label in labels:
        assert re.fullmatch(r"[0-9a-f]{6}", label["color"]), label
        assert label["description"]


def test_every_issue_has_acceptance_criteria_make_check_and_contributing(
    issues: list[dict[str, Any]],
) -> None:
    for issue in issues:
        body = issue["body"]
        assert "**Acceptance criteria**" in body, issue["title"]
        assert "make check" in body, issue["title"]
        assert "CONTRIBUTING.md" in body, issue["title"]


def _cited_paths(body: str) -> list[tuple[str, bool]]:
    """(path, is_new_file) for each backticked token that looks like a repository path."""
    found = []
    for match in _PATH.finditer(body):
        token, is_new = match.group(1), bool(match.group(2))
        path = token.split("::", 1)[0]
        looks_like_path = ("/" in path and path.endswith(_PATH_SUFFIXES)) or path in _ROOT_FILES
        if looks_like_path and "<" not in path and not path.startswith("/"):
            found.append((token, is_new))
    return found


def test_every_repository_path_an_issue_cites_exists(issues: list[dict[str, Any]]) -> None:
    missing = []
    total = 0
    for issue in issues:
        for token, is_new in _cited_paths(issue["body"]):
            total += 1
            path, _, test_name = token.partition("::")
            target = ROOT / path
            if is_new:
                assert not target.exists(), f"{issue['title']}: {path} is marked new but exists"
                continue
            if not target.exists():
                missing.append(f"{issue['title']}: {path}")
            elif test_name and f"def {test_name}(" not in target.read_text(encoding="utf-8"):
                missing.append(f"{issue['title']}: {token} (no such test)")
    assert total > 50  # the extraction is working
    assert not missing, missing


def test_issues_make_no_performance_claims(issues: list[dict[str, Any]]) -> None:
    banned = ("guaranteed", "outperform", "generates alpha", "profit", "beats the market")
    for issue in issues:
        text = issue["body"].lower()
        assert not [w for w in banned if w in text], issue["title"]


# -------------------------------------------------------------------- create script
def test_create_script_is_dry_run_by_default_and_skips_existing_titles() -> None:
    script = _text("docs/launch/create-issues.sh")
    assert script.startswith("#!/usr/bin/env bash")
    assert 'DRY_RUN="${DRY_RUN:-1}"' in script
    assert "gh issue create" in script and "gh label create" in script
    assert "gh issue list" in script and "grep -Fxq" in script  # idempotent by exact title


# ------------------------------------------------------------- README benchmark table
def _readme_benchmark_rows() -> dict[str, list[str]]:
    readme = _text("README.md")
    section = readme.split("## Benchmark results", 1)[1].split("\n## ", 1)[0]
    rows: dict[str, list[str]] = {}
    for line in section.splitlines():
        if not line.startswith("|") or line.startswith("|---") or "Model" in line.split("|")[1]:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        name = re.sub(r"\(.*?\)", "", cells[0]).replace("*", "").strip()
        rows[name] = cells[1:]
    return rows


def test_readme_cites_the_result_file_and_it_exists() -> None:
    readme = _text("README.md")
    assert RESULT_FILE in readme
    assert (ROOT / RESULT_FILE).is_file()
    assert "docs/leaderboard/index.md" in readme and "docs/benchmark-methodology.md" in readme


def test_readme_benchmark_numbers_are_the_ones_in_the_result_file() -> None:
    doc = json.loads(_text(RESULT_FILE))
    models = doc["results"][0]["models"]
    rows = _readme_benchmark_rows()
    assert set(rows) == set(models), "the README table must list every model in the file"
    for name, cells in rows.items():
        pooled = models[name]["pooled"]
        mase, rmse, crps, cover90, p_se, p_crps, outcome = cells
        assert mase == f"{pooled['mase']:.3f}", name
        assert rmse == f"{pooled['rmse'] * 100:.3f}", name
        assert crps == f"{pooled['crps'] * 100:.3f}", name
        assert cover90 == f"{pooled['coverage']['0.9']['achieved'] * 100:.1f}%", name
        dm = pooled.get("diebold_mariano_vs_random_walk")
        if name == "random-walk":
            assert pooled["verdict"] == "benchmark"
            assert "n/a" in p_se and "n/a" in p_crps and outcome == "the reference"
            continue
        assert p_se == f"{dm['squared_error']['p_model_better']:.3f}", name
        assert p_crps == f"{dm['crps']['p_model_better']:.3f}", name
        assert outcome == pooled["verdict"], name


def test_readme_says_plainly_when_baselines_win() -> None:
    readme = _text("README.md")
    doc = json.loads(_text(RESULT_FILE))
    verdicts = {m["pooled"]["verdict"] for m in doc["results"][0]["models"].values()}
    # only the reference row and "indistinguishable" verdicts exist; the prose must match that
    assert verdicts <= {"benchmark", "indistinguishable from the random walk"}
    assert "None of the 6 models other than the random-walk reference beats it" in readme
    assert "synthetic" in readme.lower()


def test_readme_carries_the_disclaimer_the_scorecard_badge_and_status() -> None:
    readme = _text("README.md")
    assert DISCLAIMER in readme
    assert "scorecard.dev/viewer/?uri=github.com/anilatambharii/tycheon" in readme
    assert "What works, what is experimental, what is not verified" in readme
    for link in ("CONTRIBUTING.md", "SECURITY.md", "docs/gallery.md", "docs/methodology.md"):
        assert f"]({link})" in readme, link


# ------------------------------------------------------------------- issue forms
_FORM_KEYS = {"name", "description", "body"}


@pytest.mark.parametrize(
    "name", ["bug_report", "feature_request", "model_benchmark_request", "good_first_issue"]
)
def test_issue_forms_have_the_required_keys(name: str) -> None:
    form = _yaml(f".github/ISSUE_TEMPLATE/{name}.yml")
    assert set(form) >= _FORM_KEYS
    assert isinstance(form["body"], list) and form["body"]
    ids = []
    for element in form["body"]:
        assert element["type"] in {"markdown", "textarea", "input", "dropdown", "checkboxes"}
        if element["type"] != "markdown":
            assert element["attributes"]["label"]
            ids.append(element.get("id"))
    assert all(ids) and len(ids) == len(set(ids))
    assert any(e.get("validations", {}).get("required") for e in form["body"])


def test_issue_forms_only_use_labels_that_are_defined() -> None:
    defined = {label["name"] for label in _yaml("docs/launch/labels.yaml")}
    for path in (ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml"):
        form = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert set(form.get("labels", [])) <= defined, path.name


def test_issue_template_config_disables_blank_issues_and_links_out() -> None:
    config = _yaml(".github/ISSUE_TEMPLATE/config.yml")
    assert config["blank_issues_enabled"] is False
    urls = " ".join(link["url"] for link in config["contact_links"])
    assert "/discussions" in urls and "/security/" in urls


@pytest.mark.parametrize("slug", ["q-a", "ideas", "show-and-tell", "benchmarks"])
def test_discussion_forms_parse_and_are_listed_in_the_checklist(slug: str) -> None:
    form = _yaml(f".github/DISCUSSION_TEMPLATE/{slug}.yml")
    assert isinstance(form["body"], list) and form["body"]
    assert any(e["type"] != "markdown" for e in form["body"])
    assert f"`{slug}`" in _text("docs/launch/discussions.md")


def test_security_forms_send_leakage_reports_to_the_private_channel() -> None:
    bug = _text(".github/ISSUE_TEMPLATE/bug_report.yml")
    assert "security/advisories/new" in bug and "lookahead" in bug.lower()


def test_pull_request_template_keeps_the_leakage_and_baseline_checklist() -> None:
    template = _text(".github/PULL_REQUEST_TEMPLATE.md")
    for needle in ("Point-in-time", "leakage", "Baselines", "Diebold-Mariano", "make check"):
        assert needle in template, needle


# --------------------------------------------------------------- scorecard workflow
def test_scorecard_workflow_pins_every_action_by_full_sha_with_minimal_permissions() -> None:
    text = _text(".github/workflows/scorecard.yml")
    workflow = yaml.safe_load(text)
    uses = re.findall(r"uses:\s*(\S+)", text)
    assert {u.split("@")[0] for u in uses} == {
        "actions/checkout",
        "ossf/scorecard-action",
        "github/codeql-action/upload-sarif",
    }
    for ref in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", ref), ref
    triggers = workflow[True] if True in workflow else workflow["on"]  # YAML 1.1 reads `on` as True
    assert triggers["push"]["branches"] == ["main"] and triggers["schedule"]
    assert workflow["permissions"] == {}
    job = workflow["jobs"]["analysis"]
    assert set(job["permissions"]) == {"contents", "security-events", "id-token"}
    assert job["permissions"]["contents"] == "read"


# ------------------------------------------------- gallery, examples, blog, mkdocs
def test_gallery_is_in_the_nav_right_after_tycheon_cloud() -> None:
    nav = _yaml("mkdocs.yml")["nav"]
    keys = [next(iter(item)) for item in nav]
    assert keys[keys.index("Tycheon Cloud") + 1] == "Examples gallery"
    assert (ROOT / "docs" / "gallery.md").is_file()


def test_blog_and_launch_folders_stay_out_of_the_published_site() -> None:
    exclude = _yaml("mkdocs.yml")["exclude_docs"]
    assert "blog/" in exclude and "launch/" in exclude


@pytest.mark.parametrize("name", ["mcp_smoke.py", "cloud_finetune.py", "risk_report.py"])
def test_launch_examples_print_the_disclaimer(name: str) -> None:
    source = _text(f"examples/{name}")
    assert DISCLAIMER in source
    assert "print(" in source


def test_cloud_finetune_example_says_it_is_unverified_and_stops_without_the_feature() -> None:
    source = _text("examples/cloud_finetune.py")
    assert "UNVERIFIED AGAINST A LIVE SERVICE" in source
    assert '"finetune" not in plan["features"]' in source
    assert "TYCHEON_CLOUD_URL" in source


def test_gallery_and_blog_carry_the_disclaimer_and_the_unverified_notice() -> None:
    gallery = _text("docs/gallery.md")
    blog = _text("docs/blog/leakage-proof-benchmark.md")
    for text in (gallery, blog):
        assert DISCLAIMER in text
    assert (
        "Not verified against a live service" in gallery or "Unverified against a live" in gallery
    )
    assert blog.lstrip().lower().startswith("<!-- draft")
    assert "# A leakage-proof benchmark for financial foundation models" in blog
