"""
Django signals for automatic audit logging.
"""
from django.db.models.signals import pre_save
from django.dispatch import receiver

from .models import Visit, TestOrder, AuditLog


@receiver(pre_save, sender=Visit)
def log_visit_status_change(sender, instance, **kwargs):
    """
    Catch status changes that bypass the service layer (safety net).
    The service layer already creates audit logs, so this only fires
    for changes made directly on the model (e.g., admin save).
    """
    if not instance.pk:
        return  # New object, nothing to compare

    try:
        old = Visit.objects.get(pk=instance.pk)
    except Visit.DoesNotExist:
        return

    if old.status != instance.status:
        # Check if the service layer already logged this
        # (by checking if a log was created in the last second)
        from django.utils import timezone
        from datetime import timedelta

        recent_log = AuditLog.objects.filter(
            visit=instance,
            action='visit_status_changed',
            new_value=instance.status,
            timestamp__gte=timezone.now() - timedelta(seconds=2),
        ).exists()

        if not recent_log:
            AuditLog.objects.create(
                visit=instance,
                action='visit_status_changed',
                old_value=old.status,
                new_value=instance.status,
                details='Changed via direct model save (not service layer)',
                actor=None,
            )
