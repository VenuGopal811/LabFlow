"""
LabFlow admin — station-specific views.

Each station (reception, chamber, collection, lab, doctor) sees a filtered,
role-appropriate view of visits with relevant actions.
"""
from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils import timezone

from .models import (
    Visit, VisitStatus, TestOrder, TestOrderStatus,
    Sample, Payment, AuditLog, TestCatalog,
)
from .services import (
    transition_visit_status, transition_test_order_status,
    confirm_payment, collect_sample, enter_test_result,
    edit_test_result, trigger_report_sms, check_visit_completion,
    TransitionError,
)


# ── Inline models ──────────────────────────────────────────────────

class TestOrderInline(admin.TabularInline):
    model = TestOrder
    extra = 1
    fields = ['test', 'status', 'result_value', 'result_entered_by', 'reviewed_by']
    readonly_fields = ['status', 'result_entered_by', 'reviewed_by']

    def get_extra(self, request, obj=None, **kwargs):
        # Only show extra rows for new visits
        if obj:
            return 0
        return 1


class SampleInline(admin.TabularInline):
    model = Sample
    extra = 0
    fields = ['sample_type', 'container_number', 'collected_by', 'collected_at', 'notes']
    readonly_fields = ['collected_by', 'collected_at']

    def get_extra(self, request, obj=None, **kwargs):
        if obj and obj.status in (VisitStatus.APPROVED_BY_CHAMBER, VisitStatus.SENT_TO_COLLECTION):
            return 1
        return 0


class PaymentInline(admin.StackedInline):
    model = Payment
    extra = 0
    fields = ['method', 'amount', 'is_confirmed', 'confirmed_by', 'confirmed_at', 'transaction_ref']
    readonly_fields = ['confirmed_by', 'confirmed_at']

    def get_extra(self, request, obj=None, **kwargs):
        if obj and not hasattr(obj, 'payment'):
            return 1
        return 0


class AuditLogInline(admin.TabularInline):
    model = AuditLog
    extra = 0
    fields = ['timestamp', 'action', 'old_value', 'new_value', 'actor', 'details']
    readonly_fields = ['timestamp', 'action', 'old_value', 'new_value', 'actor', 'details']
    ordering = ['-timestamp']

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ── Status badge helper ────────────────────────────────────────────

STATUS_COLORS = {
    'registered': '#6c757d',           # Gray
    'payment_pending': '#fd7e14',      # Orange
    'payment_confirmed': '#20c997',    # Teal
    'approved_by_chamber': '#0d6efd',  # Blue
    'sent_to_collection': '#6f42c1',   # Purple
    'doctor_reviewed': '#0dcaf0',      # Cyan
    'report_ready': '#198754',         # Green
    'report_delivered': '#198754',     # Green
    # Test order statuses
    'pending': '#6c757d',
    'sample_collected': '#6f42c1',
    'testing': '#fd7e14',
    'result_entered': '#0dcaf0',
    'retest_required': '#dc3545',
    'recollection_required': '#dc3545',
}


def status_badge(status_value, status_display=None):
    """Render a colored status badge."""
    color = STATUS_COLORS.get(status_value, '#6c757d')
    label = status_display or status_value.replace('_', ' ').title()
    return format_html(
        '<span style="background:{}; color:white; padding:3px 10px; '
        'border-radius:12px; font-size:11px; font-weight:600; '
        'text-transform:uppercase; letter-spacing:0.5px;">{}</span>',
        color, label
    )


# ── Visit Admin ────────────────────────────────────────────────────

@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = [
        'visit_id', 'patient_name', 'age', 'gender',
        'phone', 'status_badge', 'test_summary', 'created_at_short',
    ]
    list_filter = ['status', 'created_at', 'gender']
    search_fields = ['visit_id', 'patient_name', 'phone']
    date_hierarchy = 'created_at'
    readonly_fields = ['visit_id', 'created_by', 'created_at', 'updated_at', 'report_token']
    inlines = [TestOrderInline, PaymentInline, SampleInline, AuditLogInline]
    actions = [
        'action_mark_payment_pending',
        'action_confirm_payment_cash',
        'action_approve_by_chamber',
        'action_send_to_collection',
        'action_mark_doctor_reviewed',
        'action_mark_report_ready',
        'action_send_sms',
    ]

    fieldsets = (
        ('Patient Information', {
            'fields': ('patient_name', 'age', 'gender', 'phone', 'address', 'referred_by'),
        }),
        ('Visit Status', {
            'fields': ('visit_id', 'status', 'notes'),
        }),
        ('Metadata', {
            'classes': ('collapse',),
            'fields': ('created_by', 'created_at', 'updated_at', 'report_token'),
        }),
    )

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description='Status', ordering='status')
    def status_badge(self, obj):
        return status_badge(obj.status, obj.get_status_display())

    @admin.display(description='Tests')
    def test_summary(self, obj):
        orders = obj.test_orders.all()
        if not orders:
            return '—'
        total = orders.count()
        ready = sum(1 for o in orders if o.status == TestOrderStatus.REPORT_READY)
        return f'{ready}/{total} ready'

    @admin.display(description='Created', ordering='created_at')
    def created_at_short(self, obj):
        return obj.created_at.strftime('%d %b %H:%M')

    # ── Admin Actions ──────────────────────────────────────────────

    @admin.action(description='➤ Mark Payment Pending')
    def action_mark_payment_pending(self, request, queryset):
        self._bulk_transition(request, queryset, VisitStatus.PAYMENT_PENDING)

    @admin.action(description='💰 Confirm Payment (Cash)')
    def action_confirm_payment_cash(self, request, queryset):
        for visit in queryset:
            try:
                confirm_payment(visit, request.user, method='cash')
                messages.success(request, f'Payment confirmed for {visit.visit_id}')
            except TransitionError as e:
                messages.error(request, str(e))

    @admin.action(description='✅ Approve (Chamber)')
    def action_approve_by_chamber(self, request, queryset):
        self._bulk_transition(request, queryset, VisitStatus.APPROVED_BY_CHAMBER)

    @admin.action(description='🧪 Send to Collection')
    def action_send_to_collection(self, request, queryset):
        self._bulk_transition(request, queryset, VisitStatus.SENT_TO_COLLECTION)

    @admin.action(description='👨‍⚕️ Mark Doctor Reviewed')
    def action_mark_doctor_reviewed(self, request, queryset):
        self._bulk_transition(request, queryset, VisitStatus.DOCTOR_REVIEWED)

    @admin.action(description='📋 Mark Report Ready')
    def action_mark_report_ready(self, request, queryset):
        self._bulk_transition(request, queryset, VisitStatus.REPORT_READY)

    @admin.action(description='📱 Send SMS Notification')
    def action_send_sms(self, request, queryset):
        for visit in queryset:
            try:
                trigger_report_sms(visit, request.user)
                messages.success(request, f'SMS sent for {visit.visit_id}')
            except Exception as e:
                messages.error(request, f'SMS failed for {visit.visit_id}: {e}')

    def _bulk_transition(self, request, queryset, new_status):
        for visit in queryset:
            try:
                transition_visit_status(visit, new_status, request.user)
                messages.success(request, f'{visit.visit_id} → {new_status}')
            except TransitionError as e:
                messages.error(request, str(e))


# ── Test Order Admin ───────────────────────────────────────────────

@admin.register(TestOrder)
class TestOrderAdmin(admin.ModelAdmin):
    list_display = [
        'visit_link', 'test', 'status_badge',
        'result_value', 'result_entered_by', 'reviewed_by',
    ]
    list_filter = ['status', 'test', 'created_at']
    search_fields = ['visit__visit_id', 'visit__patient_name', 'test__name']
    readonly_fields = [
        'result_entered_by', 'result_entered_at',
        'reviewed_by', 'reviewed_at', 'original_value',
    ]
    actions = [
        'action_mark_sample_collected',
        'action_mark_testing',
        'action_approve_result',
        'action_flag_retest',
        'action_flag_recollection',
        'action_mark_report_ready',
    ]

    fieldsets = (
        ('Test Order', {
            'fields': ('visit', 'test', 'status'),
        }),
        ('Results', {
            'fields': ('result_value', 'original_value'),
        }),
        ('Tracking', {
            'classes': ('collapse',),
            'fields': ('result_entered_by', 'result_entered_at', 'reviewed_by', 'reviewed_at'),
        }),
    )

    @admin.display(description='Visit', ordering='visit__visit_id')
    def visit_link(self, obj):
        return format_html(
            '<a href="/admin/core/visit/{}/change/">{}</a> — {}',
            obj.visit.pk, obj.visit.visit_id, obj.visit.patient_name
        )

    @admin.display(description='Status', ordering='status')
    def status_badge(self, obj):
        return status_badge(obj.status, obj.get_status_display())

    # ── Test Order Actions ─────────────────────────────────────────

    @admin.action(description='🧫 Mark Sample Collected')
    def action_mark_sample_collected(self, request, queryset):
        self._bulk_test_transition(request, queryset, TestOrderStatus.SAMPLE_COLLECTED)

    @admin.action(description='🔬 Mark Testing (In Lab)')
    def action_mark_testing(self, request, queryset):
        self._bulk_test_transition(request, queryset, TestOrderStatus.TESTING)

    @admin.action(description='✅ Approve Result (Doctor)')
    def action_approve_result(self, request, queryset):
        for order in queryset:
            try:
                transition_test_order_status(order, TestOrderStatus.DOCTOR_REVIEWED, request.user)
                transition_test_order_status(order, TestOrderStatus.REPORT_READY, request.user)
                messages.success(request, f'Result approved: {order}')
            except TransitionError as e:
                messages.error(request, str(e))

    @admin.action(description='🔄 Flag for Retest')
    def action_flag_retest(self, request, queryset):
        self._bulk_test_transition(request, queryset, TestOrderStatus.RETEST_REQUIRED)

    @admin.action(description='🔄 Flag for Recollection')
    def action_flag_recollection(self, request, queryset):
        self._bulk_test_transition(request, queryset, TestOrderStatus.RECOLLECTION_REQUIRED)

    @admin.action(description='📋 Mark Report Ready')
    def action_mark_report_ready(self, request, queryset):
        self._bulk_test_transition(request, queryset, TestOrderStatus.REPORT_READY)

    def _bulk_test_transition(self, request, queryset, new_status):
        for order in queryset:
            try:
                transition_test_order_status(order, new_status, request.user)
                messages.success(request, f'{order} → {new_status}')
            except TransitionError as e:
                messages.error(request, str(e))


# ── Sample Admin ───────────────────────────────────────────────────

@admin.register(Sample)
class SampleAdmin(admin.ModelAdmin):
    list_display = ['container_number', 'visit_link', 'sample_type', 'collected_by', 'collected_at']
    list_filter = ['sample_type', 'collected_at']
    search_fields = ['container_number', 'visit__visit_id', 'visit__patient_name']
    readonly_fields = ['collected_by', 'collected_at']

    @admin.display(description='Visit')
    def visit_link(self, obj):
        return format_html(
            '<a href="/admin/core/visit/{}/change/">{}</a>',
            obj.visit.pk, obj.visit.visit_id
        )

    def save_model(self, request, obj, form, change):
        if not change:
            obj.collected_by = request.user
        super().save_model(request, obj, form, change)


# ── Payment Admin ──────────────────────────────────────────────────

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['visit_link', 'amount_display', 'method', 'is_confirmed', 'confirmed_by', 'confirmed_at']
    list_filter = ['method', 'is_confirmed', 'created_at']
    search_fields = ['visit__visit_id', 'visit__patient_name']
    readonly_fields = ['confirmed_by', 'confirmed_at']

    @admin.display(description='Visit')
    def visit_link(self, obj):
        return format_html(
            '<a href="/admin/core/visit/{}/change/">{}</a>',
            obj.visit.pk, obj.visit.visit_id
        )

    @admin.display(description='Amount')
    def amount_display(self, obj):
        return f'₹{obj.amount}'


# ── Audit Log Admin ────────────────────────────────────────────────

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['timestamp', 'visit_link', 'action', 'old_value', 'new_value', 'actor']
    list_filter = ['action', 'timestamp']
    search_fields = ['visit__visit_id', 'action', 'details']
    readonly_fields = [
        'visit', 'test_order', 'action', 'old_value',
        'new_value', 'details', 'actor', 'timestamp',
    ]
    date_hierarchy = 'timestamp'

    @admin.display(description='Visit')
    def visit_link(self, obj):
        return format_html(
            '<a href="/admin/core/visit/{}/change/">{}</a>',
            obj.visit.pk, obj.visit.visit_id
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# ── Test Catalog Admin ─────────────────────────────────────────────

@admin.register(TestCatalog)
class TestCatalogAdmin(admin.ModelAdmin):
    list_display = ['short_code', 'name', 'sample_type', 'department', 'price', 'is_active']
    list_filter = ['sample_type', 'department', 'is_active']
    search_fields = ['name', 'short_code']
    list_editable = ['price', 'is_active']
