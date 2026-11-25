from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def audit(db: Session, actor_id: int | None, action: str, entity: str, entity_id: Any = "",
          before: dict[str, Any] | None = None, after: dict[str, Any] | None = None, ip: str = "") -> None:
    db.add(AuditLog(actor_id=actor_id, action=action, entity=entity, entity_id=str(entity_id),
                    before=before, after=after, ip=ip))
