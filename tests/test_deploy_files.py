"""Static guarantees about the deployment artefacts, checked without Docker, Helm or Terraform.

The runtime checks (image properties, chart rendering, plans) live in `deploy/` scripts and the
`deploy-verify` workflow; these keep the files from drifting in ways a reviewer would miss.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CHART = ROOT / "deploy" / "helm" / "tycheon"
DOCKERFILES = [ROOT / "Dockerfile", ROOT / "ee" / "dashboard" / "Dockerfile"]
PINNED_WORKFLOWS = ["images.yml", "deploy-verify.yml", "scorecard.yml"]


@pytest.mark.parametrize("path", DOCKERFILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_dockerfiles_pin_their_base_images_by_digest(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    args = dict(re.findall(r"^ARG (\w+_IMAGE)=(\S+)", text, re.MULTILINE))
    assert args, "base images should be ARGs so they can be pinned in one place"
    for name, value in args.items():
        assert re.search(r"@sha256:[0-9a-f]{64}$", value), f"{name} is not pinned by digest"
    stages = set(re.findall(r"^FROM \S+ AS (\w+)", text, re.MULTILINE))
    for source in re.findall(r"^FROM (\S+)", text, re.MULTILINE):
        assert source.startswith("${") or source in stages or "@sha256:" in source, source


@pytest.mark.parametrize("path", DOCKERFILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_dockerfiles_run_as_an_unprivileged_user(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    users = re.findall(r"^USER (\S+)", text, re.MULTILINE)
    assert users and users[-1] == "10001:10001"
    assert "--privileged" not in text and "sudo" not in text


def test_the_python_image_installs_the_published_wheel_not_the_source_tree() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "uv build --wheel" in text and "ee/control_plane /app/ee/control_plane" in text
    # the proprietary code is copied only into the cloud target, never the open-source one
    api = text.split("AS api")[1].split("AS cloud")[0]
    assert "ee/" not in api


def test_the_docker_build_context_is_an_allowlist() -> None:
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "*" or any(line == "*" for line in lines)
    assert "!tests/" not in lines and "!.git/" not in lines and "!.env" not in lines


@pytest.mark.parametrize("name", PINNED_WORKFLOWS)
def test_delivery_workflows_pin_every_action_by_commit_sha(name: str) -> None:
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    for match in re.finditer(r"^\s*-?\s*uses:\s*(\S+)", text, re.MULTILINE):
        reference = match.group(1)
        assert re.search(r"@[0-9a-f]{40}$", reference), f"{name}: {reference} is not pinned"


def test_the_images_workflow_signs_scans_and_gates_production() -> None:
    text = (WORKFLOWS / "images.yml").read_text(encoding="utf-8")
    for needle in (
        "cosign sign",
        "cosign attest",
        "cosign verify",
        "trivy-action",
        "sbom-action",
        'exit-code: "1"',
        "ignore-unfixed: true",
        "name: production",
        "--atomic",
    ):
        assert needle in text, needle
    assert "pull_request" in text and "push: true" in text
    # nothing is pushed for a pull request
    assert "steps.meta.outputs.push == 'true'" in text
    document = yaml.safe_load(text)
    production = document["jobs"]["deploy-production"]
    assert production["environment"]["name"] == "production"
    assert "refs/tags/v" in production["if"]
    assert document["permissions"] == {"contents": "read"}


def test_no_credential_is_stored_in_the_delivery_workflows() -> None:
    for name in PINNED_WORKFLOWS:
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert not re.search(r"(AKIA|ASIA)[0-9A-Z]{16}|sk_live_|-----BEGIN", text)


def test_the_chart_ships_no_secret_values() -> None:
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))

    def walk(node: object, path: str = "") -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                label = f"{path}.{key}" if path else str(key)
                if re.search(
                    r"(secret|token|password|apikey|api_key|private)", str(key), re.IGNORECASE
                ):
                    assert (
                        child in ("", None, [])
                        or isinstance(child, (dict, bool))
                        or label.endswith("existingSecret")
                    ), label
                walk(child, label)

    walk(values)
    assert values["api"]["existingSecret"] == "tycheon-api-keys"


def test_the_chart_templates_apply_the_pod_hardening_everywhere() -> None:
    helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    for needle in (
        "runAsNonRoot: true",
        "readOnlyRootFilesystem: true",
        'drop: ["ALL"]',
        "RuntimeDefault",
        "automountServiceAccountToken: false",
    ):
        assert needle in helpers, needle
    supporting = (CHART / "templates" / "supporting.yaml").read_text(encoding="utf-8")
    assert "default-deny" in supporting and "policyTypes: [Ingress, Egress]" in supporting
    assert "hostNetwork" not in helpers + supporting and "privileged" not in helpers + supporting


def test_the_dev_database_is_only_enabled_in_the_kind_profile() -> None:
    for name in ("values.yaml", "values-saas.yaml", "values-customer-vpc.yaml"):
        values = yaml.safe_load((CHART / name).read_text(encoding="utf-8")) or {}
        assert not values.get("devPostgres", {}).get("enabled", False), name
    kind = yaml.safe_load((CHART / "values-kind.yaml").read_text(encoding="utf-8"))
    assert kind["devPostgres"]["enabled"] is True and kind["networkPolicy"]["enabled"] is False


def test_every_alert_runbook_exists() -> None:
    rules = (CHART / "templates" / "prometheusrule.yaml").read_text(encoding="utf-8")
    names = set(re.findall(r"runbookBase \}\}/([\w-]+)\.md", rules))
    assert len(names) >= 6
    for name in names:
        assert (ROOT / "docs" / "ops" / "runbooks" / f"{name}.md").is_file(), name


def test_every_alert_in_the_slo_page_is_a_real_alert() -> None:
    rules = (CHART / "templates" / "prometheusrule.yaml").read_text(encoding="utf-8")
    defined = set(re.findall(r"- alert: (\w+)", rules))
    documented = set(
        re.findall(
            r"`(Tycheon\w+)`", (ROOT / "docs" / "ops" / "slos.md").read_text(encoding="utf-8")
        )
    )
    assert documented == defined, documented ^ defined


def test_terraform_roots_commit_their_provider_lock_files() -> None:
    roots = [
        p.parent for p in (ROOT / "deploy" / "terraform").rglob("main.tf") if "examples" in p.parts
    ]
    assert len(roots) >= 3
    for root in roots:
        assert (root / ".terraform.lock.hcl").is_file(), root
    ignore = (ROOT / "deploy" / "terraform" / ".gitignore").read_text(encoding="utf-8")
    assert "*.tfstate" in ignore and ".terraform/" in ignore


def test_terraform_never_defaults_a_secret() -> None:
    for path in (ROOT / "deploy" / "terraform").rglob("*.tf"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(
            r"(AKIA|ASIA)[0-9A-Z]{16}|-----BEGIN|password\s*=\s*\"[^$\"]+\"", text
        ), path


def test_dependabot_covers_every_ecosystem_and_every_directory_exists() -> None:
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    assert config["version"] == 2
    ecosystems = {update["package-ecosystem"] for update in config["updates"]}
    assert ecosystems == {"github-actions", "uv", "npm", "terraform", "docker"}
    for update in config["updates"]:
        directories = update.get("directories") or [update["directory"]]
        for directory in directories:
            assert (ROOT / directory.lstrip("/")).is_dir(), directory
        # pull requests get a Conventional Commits title, and none of them is a release-bumping type
        assert re.fullmatch(r"(ci|build|chore)\(deps\)", update["commit-message"]["prefix"])
    python = next(u for u in config["updates"] if u["package-ecosystem"] == "uv")
    # the bounds in pyproject.toml are a deliberate policy: only the lock file moves automatically
    assert python["versioning-strategy"] == "lockfile-only"
    terraform = next(u for u in config["updates"] if u["package-ecosystem"] == "terraform")
    for directory in terraform["directories"]:
        assert (ROOT / directory.lstrip("/") / ".terraform.lock.hcl").is_file(), directory
