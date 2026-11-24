from .models import AuditLog


def audit(user, obj, action, detail="", reference=""):
    """Record an audit entry for `obj` (any model instance)."""
    return AuditLog.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        entity=obj._meta.model_name,
        entity_id=str(obj.pk),
        reference=reference or str(getattr(obj, "number", "") or getattr(obj, "run_no", "") or ""),
        action=action,
        detail=detail,
    )
