"""Append-only activity log: who did what, written in the same transaction as the change."""

from uuid import uuid4

from app.context import TenantContext
from app.db import Connection, text


def log_activity(
    conn: Connection,
    tenant: TenantContext,
    action: str,
    entity: str,
    entity_id: str | None = None,
    detail: str = "",
) -> None:
    conn.execute(
        text("""
            INSERT INTO public.activity_log
                (id, tenant_id, user_id, action, entity, entity_id, detail)
            VALUES
                (:id, :tenant_id, :user_id, :action, :entity,
                 CAST(:entity_id AS uuid), :detail)
        """),
        {
            "id": str(uuid4()),
            "tenant_id": tenant.tenant_id,
            "user_id": tenant.user_id,
            "action": action[:40],
            "entity": entity[:40],
            "entity_id": entity_id,
            "detail": detail[:300],
        },
    )
