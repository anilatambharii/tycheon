"""Build and push the static demo Space.

    uv run --extra serve python deploy/huggingface/deploy.py            # build, print the file list
    uv run --extra serve --with huggingface_hub python deploy/huggingface/deploy.py --push

The results are computed by ``build_static.py`` from the checkout being deployed. The token is read
from HF_TOKEN in the environment or the repo's gitignored .env and is never printed. This script
never changes the Space's visibility: that stays a manual step.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_REPO = "AmbhariiLabs/tycheon-demo"
ALLOWED = {"README.md", "index.html", "data.json"}

sys.path.insert(0, str(HERE))


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
    """Write exactly the files the Space serves into ``target``."""
    import build_static

    shutil.copy2(HERE / "README.md", target / "README.md")
    shutil.copy2(HERE / "site" / "index.html", target / "index.html")
    build_static.write(target / "data.json")
    files = sorted(p for p in target.rglob("*") if p.is_file())
    extra = [p.name for p in files if p.name not in ALLOWED or p.parent != target]
    if extra:
        raise SystemExit(f"refusing to stage {extra}")
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
            print(f"  {path.name}  ({path.stat().st_size:,} bytes)")
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
            # the Space is a mirror of this folder: files from the earlier Docker attempt go
            delete_patterns=["Dockerfile", "start.sh", "app/**", "wheel/**"],
            commit_message="Deploy Tycheon static demo",
        )
        print(f"pushed to https://huggingface.co/spaces/{args.repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
