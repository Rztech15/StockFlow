"""Serves the dashboard locally (python -m uvicorn). On Vercel the same files in public/ are
served by the CDN before the function is reached, so these routes are only a local fallback.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.errors import not_found

PUBLIC = Path(__file__).resolve().parents[2] / "public"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)

BRAND = {
    "favicon.svg": "image/svg+xml",
    "favicon.ico": "image/x-icon",
    "apple-touch-icon.png": "image/png",
    "logo-horizontal.svg": "image/svg+xml",
    "logo-horizontal-dark.svg": "image/svg+xml",
    "logo-icon.svg": "image/svg+xml",
}

router = APIRouter(include_in_schema=False)


def _serve(name: str, media_type: str) -> FileResponse:
    path = PUBLIC / name
    if not path.is_file():
        raise not_found("Dashboard files are not available in this deployment.")
    return FileResponse(path, media_type=media_type, headers={"content-security-policy": CSP})


@router.get("/")
def index() -> FileResponse:
    return _serve("index.html", "text/html; charset=utf-8")


@router.get("/app.js")
def script() -> FileResponse:
    return _serve("app.js", "text/javascript; charset=utf-8")


@router.get("/styles.css")
def styles() -> FileResponse:
    return _serve("styles.css", "text/css; charset=utf-8")


@router.get("/brand/{name}")
def brand(name: str) -> FileResponse:
    media_type = BRAND.get(name)  # allow-list: no arbitrary file names or path traversal
    if media_type is None:
        raise not_found()
    return _serve(f"brand/{name}", media_type)
