from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models, transaction


class User(AbstractUser):
    class Role(models.TextChoices):
        SALESMAN = "SALESMAN", "Salesman"
        ADMIN = "ADMIN", "Factory admin"
        PRODUCTION = "PRODUCTION", "Production supervisor"
        ACCOUNTANT = "ACCOUNTANT", "Accountant / auditor (read-only)"
        OWNER = "OWNER", "Owner"

    role = models.CharField(max_length=12, choices=Role.choices, default=Role.ADMIN)

    @property
    def is_admin_role(self):
        return self.role == self.Role.ADMIN or self.is_superuser


class Sequence(models.Model):
    """Gap-free document counters (invoice numbers restart each financial year)."""
    key = models.CharField(max_length=40, unique=True)
    last = models.PositiveIntegerField(default=0)

    @classmethod
    def next(cls, key):
        with transaction.atomic():
            seq, _ = cls.objects.select_for_update().get_or_create(key=key)
            seq.last += 1
            seq.save(update_fields=["last"])
            return seq.last


class AuditLog(models.Model):
    """Who changed what, when. Rows are never edited or deleted."""
    at = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="+")
    entity = models.CharField(max_length=40)
    entity_id = models.CharField(max_length=40)
    reference = models.CharField(max_length=60, blank=True)
    action = models.CharField(max_length=30)
    detail = models.TextField(blank=True)

    class Meta:
        ordering = ["-at", "-id"]

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.user} {self.action} {self.entity}#{self.entity_id}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise RuntimeError("Audit log rows are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Audit log rows cannot be deleted.")
