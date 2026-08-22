import json

import asyncpg

from app.models import AuditEvent


class AuditLog:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    async def recent(self, limit: int = 100) -> list[AuditEvent]:
        return self.events[-limit:][::-1]

    async def close(self) -> None:
        return None


class PostgresAuditLog(AuditLog):
    def __init__(self, dsn: str) -> None:
        super().__init__()
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def _pool(self) -> asyncpg.Pool:
        if self.pool is None:
            self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
        return self.pool

    async def record(self, event: AuditEvent) -> None:
        pool = await self._pool()
        await pool.execute(
            "INSERT INTO audit_events(action, actor, resource, allowed, details, created_at) "
            "VALUES($1, $2, $3, $4, $5::jsonb, $6)",
            event.action,
            event.actor,
            event.resource,
            event.allowed,
            json.dumps(event.details, ensure_ascii=False),
            event.created_at,
        )

    async def recent(self, limit: int = 100) -> list[AuditEvent]:
        pool = await self._pool()
        rows = await pool.fetch(
            "SELECT action, actor, resource, allowed, details, created_at "
            "FROM audit_events ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        return [AuditEvent.model_validate(dict(row)) for row in rows]

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
