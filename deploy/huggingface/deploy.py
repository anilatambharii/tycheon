"""Stage and push the demo Space.

    uv run python deploy/huggingface/deploy.py            # stage only, print what would be pushed
    uv run --with huggingface_hub python deploy/huggingface/deploy.py --push

The token is read from HF_TOKEN in the environment or the repo's gitignored .env and is
never printed. This script never changes the Space's visibility: that stays a manual step.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_REPO = "AmbhariiLabs/tycheon-demo"
FORBIDDEN = ("ee", ".env", "third_party", "tests", ".git")


def load_dotenv_token() -> str | None:
    """HF_TOKEN from the environment, else from the repo's gitignored .env (never printed)."""
    token = os.environ.get("HF_TOKEN")
    if token:
        return token
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == "HF_TOKEN" and value.strip():
                return value.strip().strip("\"'")
    return None


def stage(target: Path) -> list[Path]:
    """Copy the Space files and one wheel of this checkout into ``target``."""
    for name in ("Dockerfile", "start.sh", "README.md"):
        shutil.copy2(HERE / name, target / name)
    shutil.copytree(HERE / "app", target / "app", ignore=shutil.ignore_patterns("__pycache__"))
    wheel_dir = target / "wheel"
    wheel_dir.mkdir()
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(wheel_dir)], cwd=ROOT, check=True)
    files = sorted(p for p in target.rglob("*") if p.is_file())
    for path in files:
        top = path.relative_to(target).parts[0]
        if top in FORBIDDEN or path.suffix in {".pem", ".key"}:
            raise SystemExit(f"refusing to stage {path.relative_to(target)}")
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--push", action="store_true", help="upload; the default is a dry run")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)
        files = stage(target)
        for path in files:
            print(f"  {path.relative_to(target).as_posix()}  ({path.stat().st_size:,} bytes)")
        if not args.push:
            print("dry run: nothing uploaded (pass --push)")
            return 0
        token = load_dotenv_token()
        if not token:
            sys.stderr.write("error: set HF_TOKEN in the environment or in .env\n")
            return 2
        from huggingface_hub import HfApi

        api = HfApi(token=token)
        api.upload_folder(
            repo_id=args.repo,
            repo_type="space",
            folder_path=str(target),
            commit_message="Deploy Tycheon demo",
        )
        print(f"pushed to https://huggingface.co/spaces/{args.repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
