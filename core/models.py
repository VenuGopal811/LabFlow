"""
LabFlow core models.

Key design:
- No separate Patient table (per-visit identity, no cross-visit matching).
- Visit is the root entity; patient info lives on Visit.
- TestOrder has its own independent status FSM.
- AuditLog is append-only — nothing is ever deleted or overwritten silently.
"""
import secrets
from django.db import models
from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone


# ── Status choices ─────────────────────────────────────────────────

class VisitStatus(models.TextChoices):
    REGISTERED = 'registered', 'Registered'
    PAYMENT_PENDING = 'payment_pending', 'Payment Pending'
    PAYMENT_CONFIRMED = 'payment_confirmed', 'Payment Confirmed'
    APPROVED_BY_CHAMBER = 'approved_by_chamber', 'Approved by Chamber'
    SENT_TO_COLLECTION = 'sent_to_collection', 'Sent to Collection'
    SAMPLE_COLLECTED = 'sample_collected', 'Sample Drawn'
    DOCTOR_REVIEWED = 'doctor_reviewed', 'Doctor Reviewed'
    PENDING_REPORTING = 'pending_reporting', 'Pending Reporting'
    REPORT_READY = 'report_ready', 'Report Ready'
    REPORT_DELIVERED = 'report_delivered', 'Report Delivered'
    CANCELLED = 'cancelled', 'Cancelled'


class TestOrderStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    SAMPLE_COLLECTED = 'sample_collected', 'Sample Collected'
    TESTING = 'testing', 'Testing'
    RESULT_ENTERED = 'result_entered', 'Result Entered'
    DOCTOR_REVIEWED = 'doctor_reviewed', 'Doctor Reviewed'
    RETEST_REQUIRED = 'retest_required', 'Retest Required'
    RECOLLECTION_REQUIRED = 'recollection_required', 'Recollection Required'
    REPORT_READY = 'report_ready', 'Report Ready'
    CANCELLED = 'cancelled', 'Cancelled'


class PaymentMethod(models.TextChoices):
    CASH = 'cash', 'Cash'
    UPI = 'upi', 'UPI / Online'


class Gender(models.TextChoices):
    MALE = 'M', 'Male'
    FEMALE = 'F', 'Female'
    OTHER = 'O', 'Other'


class SampleType(models.TextChoices):
    BLOOD = 'blood', 'Blood'
    URINE = 'urine', 'Urine'
    SEMEN = 'semen', 'Semen'
    OTHER = 'other', 'Other'


# ── Test Catalog ───────────────────────────────────────────────────

class TestCatalog(models.Model):
    """Master list of tests the lab can perform."""
    name = models.CharField(max_length=200, unique=True)
    short_code = models.CharField(
        max_length=20, unique=True, blank=True,
        help_text='Short code for quick entry, e.g. CBC, LFT'
    )
    sample_type = models.CharField(
        max_length=20, choices=SampleType.choices, default=SampleType.BLOOD
    )
    department = models.CharField(
        max_length=100, blank=True,
        help_text='E.g., Hematology, Biochemistry, Microbiology'
    )
    parameters = models.JSONField(
        default=list, blank=True,
        help_text='List of parameter dicts: [{"name": "Hemoglobin", "unit": "g/dL", "ref_min": 12.0, "ref_max": 16.0}, ...]'
    )
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Test (Catalog)'
        verbose_name_plural = 'Test Catalog'

    def __str__(self):
        if self.short_code:
            return f'{self.short_code} — {self.name}'
        return self.name


# ── Visit ──────────────────────────────────────────────────────────

class Visit(models.Model):
    """
    Root entity. One visit = one patient interaction.
    Patient info is stored directly (no cross-visit matching).
    """
    visit_id = models.CharField(
        max_length=30, unique=True, editable=False, db_index=True,
        help_text='Auto-generated: LF-YYYYMMDD-NNNN'
    )

    # Patient info (per-visit, not a separate table)
    patient_name = models.CharField(max_length=200)
    age = models.PositiveSmallIntegerField()
    gender = models.CharField(max_length=1, choices=Gender.choices)
    phone = models.CharField(max_length=15, help_text='For SMS notification')
    address = models.TextField(blank=True)
    referred_by = models.CharField(max_length=200, blank=True, help_text='Referring doctor, if any')

    # Workflow
    status = models.CharField(
        max_length=30,
        choices=VisitStatus.choices,
        default=VisitStatus.REGISTERED,
        db_index=True,
    )

    # Report access
    report_token = models.CharField(
        max_length=64, blank=True, editable=False, db_index=True,
        help_text='Unguessable token for public PDF download'
    )
    report_token_created_at = models.DateTimeField(null=True, blank=True, editable=False)

    # Meta
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='visits_created'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Notes
    notes = models.TextField(blank=True, help_text='Internal notes about this visit')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Visit'
        verbose_name_plural = 'Visits'
        indexes = [
            models.Index(fields=['patient_name'], name='idx_visit_patient_name'),
            models.Index(fields=['phone'], name='idx_visit_phone'),
            models.Index(fields=['-created_at'], name='idx_visit_created_desc'),
        ]

    def __str__(self):
        return f'{self.visit_id} — {self.patient_name}'

    def save(self, *args, **kwargs):
        if not self.visit_id:
            self.visit_id = self._generate_visit_id()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_visit_id():
        """Generate LF-YYYYMMDD-NNNN format ID."""
        today = timezone.localdate()
        date_str = today.strftime('%Y%m%d')
        prefix = f'LF-{date_str}-'

        # Find the max sequence number for today
        last_visit = (
            Visit.objects
            .filter(visit_id__startswith=prefix)
            .order_by('-visit_id')
            .first()
        )
        if last_visit:
            try:
                last_seq = int(last_visit.visit_id.split('-')[-1])
            except (ValueError, IndexError):
                last_seq = 0
        else:
            last_seq = 0

        return f'{prefix}{last_seq + 1:04d}'

    def generate_report_token(self):
        """Create a new time-limited download token."""
        self.report_token = secrets.token_urlsafe(48)
        self.report_token_created_at = timezone.now()
        self.save(update_fields=['report_token', 'report_token_created_at'])
        return self.report_token

    def is_report_token_valid(self):
        """Check if the current token is still valid."""
        if not self.report_token or not self.report_token_created_at:
            return False
        from datetime import timedelta
        expiry = timedelta(hours=settings.REPORT_TOKEN_EXPIRY_HOURS)
        return timezone.now() < self.report_token_created_at + expiry

    @property
    def total_amount(self):
        """Sum of all test order prices."""
        return sum(
            to.test.price for to in self.test_orders.exclude(status=TestOrderStatus.CANCELLED).select_related('test').all()
            if to.test
        )

    @property
    def active_test_orders(self):
        """Return all test orders that are not cancelled."""
        return self.test_orders.exclude(status=TestOrderStatus.CANCELLED)

    @property
    def all_tests_ready(self):
        """True if every active (non-cancelled) test order on this visit has status report_ready."""
        active_orders = self.active_test_orders
        if not active_orders.exists():
            return False
        return all(o.status == TestOrderStatus.REPORT_READY for o in active_orders)


# ── Test Order ─────────────────────────────────────────────────────

class TestOrder(models.Model):
    """One test within a visit. Each has its own status lifecycle."""
    visit = models.ForeignKey(
        Visit, on_delete=models.CASCADE, related_name='test_orders'
    )
    test = models.ForeignKey(
        TestCatalog, on_delete=models.PROTECT, related_name='orders'
    )

    # Independent status per test
    status = models.CharField(
        max_length=30,
        choices=TestOrderStatus.choices,
        default=TestOrderStatus.PENDING,
        db_index=True,
    )

    # Results — JSON for multi-parameter tests
    # e.g., {"Hemoglobin": "14.5", "WBC": "8000", "Platelets": "250000"}
    result_value = models.JSONField(
        null=True, blank=True,
        help_text='Test results as JSON. Keys = parameter names, values = result strings.'
    )

    # Audit: preserve original value if doctor edits
    original_value = models.JSONField(
        null=True, blank=True, editable=False,
        help_text='Preserved original result if doctor made corrections.'
    )

    # Who entered / reviewed
    result_entered_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='results_entered'
    )
    result_entered_at = models.DateTimeField(null=True, blank=True)

    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tests_reviewed'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    display_order = models.IntegerField(
        null=True, blank=True,
        help_text='Custom display order of the test order'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Test Order'
        verbose_name_plural = 'Test Orders'

    def __str__(self):
        return f'{self.visit.visit_id} → {self.test.name} [{self.get_status_display()}]'


# ── Sample ─────────────────────────────────────────────────────────

class Sample(models.Model):
    """Physical sample container linked to a visit."""
    visit = models.ForeignKey(
        Visit, on_delete=models.CASCADE, related_name='samples'
    )
    sample_type = models.CharField(max_length=20, choices=SampleType.choices)
    container_number = models.CharField(
        max_length=50,
        help_text='Number written on the physical container'
    )

    collected_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='samples_collected'
    )
    collected_at = models.DateTimeField(default=timezone.now)

    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-collected_at']
        verbose_name = 'Sample'
        verbose_name_plural = 'Samples'

    def __str__(self):
        return f'#{self.container_number} ({self.get_sample_type_display()}) — {self.visit.visit_id}'


# ── Payment ────────────────────────────────────────────────────────

class Payment(models.Model):
    """Payment record for a visit."""
    visit = models.OneToOneField(
        Visit, on_delete=models.CASCADE, related_name='payment'
    )
    method = models.CharField(max_length=10, choices=PaymentMethod.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    # Confirmation
    is_confirmed = models.BooleanField(default=False)
    confirmed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='payments_confirmed'
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    # UPI transaction reference (if applicable)
    transaction_ref = models.CharField(max_length=100, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Payment'
        verbose_name_plural = 'Payments'

    def __str__(self):
        status = '✓' if self.is_confirmed else '⏳'
        return f'{status} ₹{self.amount} ({self.get_method_display()}) — {self.visit.visit_id}'


# ── Audit Log ──────────────────────────────────────────────────────

class AuditLog(models.Model):
    """
    Immutable append-only audit trail.
    Every status change, result edit, and significant action is logged here.
    """
    visit = models.ForeignKey(
        Visit, on_delete=models.CASCADE, related_name='audit_logs'
    )
    test_order = models.ForeignKey(
        TestOrder, on_delete=models.CASCADE, null=True, blank=True,
        related_name='audit_logs',
        help_text='If this log entry is about a specific test order'
    )

    action = models.CharField(
        max_length=50,
        help_text='E.g., status_changed, result_edited, payment_confirmed'
    )
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    details = models.TextField(
        blank=True,
        help_text='Additional context about this action'
    )

    actor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='audit_actions'
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Audit Log'
        verbose_name_plural = 'Audit Log'

    def __str__(self):
        actor_name = self.actor.get_full_name() or self.actor.username if self.actor else 'System'
        return f'[{self.timestamp:%Y-%m-%d %H:%M}] {actor_name}: {self.action} on {self.visit.visit_id}'

    @property
    def actor_display(self):
        if self.actor:
            return self.actor.get_full_name() or self.actor.username
        return 'System'
