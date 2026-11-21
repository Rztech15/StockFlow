"""Portable pipeline: runs every stage in order and stops at the first failure.

    cd backend
    python -m scripts.ci

Needs: Docker, a virtualenv with `pip install -e ".[dev]"`, gitleaks and trivy on PATH, and a
.env (python -m scripts.init_env). Stage names match the Phase 0 exit criteria.
"""

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
PY = sys.executable

STAGES: list[tuple[str, list[str], Path]] = [
    ("lint", [PY, "-m", "ruff", "check", "."], BACKEND),
    ("unit tests", [PY, "-m", "pytest", "-m", "not db", "-q"], BACKEND),
    ("start PostgreSQL", ["docker", "compose", "up", "-d", "--wait", "db", "mailpit"], ROOT),
    ("bootstrap roles", [PY, "-m", "scripts.bootstrap_db"], BACKEND),
    ("run migrations", [PY, "-m", "alembic", "upgrade", "head"], BACKEND),
    ("integration tests", [PY, "-m", "pytest", "-m", "db and not rls and not guard", "-q"], BACKEND),
    ("tenant-isolation tests", [PY, "-m", "pytest", "-m", "rls", "-q"], BACKEND),
    ("catalog / RLS guard", [PY, "-m", "pytest", "-m", "guard", "-q"], BACKEND),
    ("dependency audit", [PY, "-m", "pip_audit"], BACKEND),
    ("secret scan (gitleaks)", ["gitleaks", "detect", "--source", ".", "--config", ".gitleaks.toml", "--redact", "--no-banner"], ROOT),
    ("docker image build", ["docker", "build", "-t", "stockflow:local", "."], ROOT),
    ("image vulnerability scan", ["trivy", "image", "--exit-code", "1", "--severity", "HIGH,CRITICAL", "--ignore-unfixed", "stockflow:local"], ROOT),
]  # fmt: skip


def main() -> int:
    for number, (name, command, cwd) in enumerate(STAGES, start=1):
        print(f"\n==> {number}/{len(STAGES)} {name}", flush=True)
        try:
            code = subprocess.run(command, cwd=cwd, check=False).returncode
        except FileNotFoundError:
            print(f"Missing tool: {command[0]}")
            code = 127
        if code != 0:
            print(f"\nFAILED at stage {number}: {name}")
            return code
    print("\nAll stages passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
