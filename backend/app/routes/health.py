from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness only: does not touch the database or expose configuration."""
    return {"status": "ok"}
