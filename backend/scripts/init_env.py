"""Create .env (repo root) from .env.example with freshly generated random secrets.

    python -m scripts.init_env          (add --force to overwrite)
"""

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    target, example = ROOT / ".env", ROOT / ".env.example"
    if target.exists() and "--force" not in sys.argv:
        print(".env already exists; leaving it untouched (use --force to regenerate).")
        return 0
    text = example.read_text(encoding="utf-8")
    for token, nbytes in (
        ("change-me-admin", 24),
        ("change-me-owner", 24),
        ("change-me-app", 24),
        ("change-me-pepper", 32),
    ):
        text = text.replace(token, secrets.token_hex(nbytes))
    target.write_text(text, encoding="utf-8", newline="\n")
    print("Created .env with generated local secrets (not committed; listed in .gitignore).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
