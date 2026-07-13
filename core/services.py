"""
LabFlow business logic layer.

All status transitions go through here — never directly mutated in views/admin.
Every transition creates an AuditLog entry.
"""
from django.utils import timezone
from django.conf import settings

from .models import (
    Visit, VisitStatus, TestOrder, TestOrderStatus,
    Payment, AuditLog, Sample,
)


class TransitionError(Exception):
    """Raised when an invalid status transition is attempted."""
    pass


# ── Allowed transitions ───────────────────────────────────────────

VISIT_TRANSITIONS = {
    VisitStatus.REGISTERED: [VisitStatus.PAYMENT_PENDING],
    VisitStatus.PAYMENT_PENDING: [VisitStatus.PAYMENT_CONFIRMED],
    VisitStatus.PAYMENT_CONFIRMED: [VisitStatus.APPROVED_BY_CHAMBER],
    VisitStatus.APPROVED_BY_CHAMBER: [VisitStatus.SENT_TO_COLLECTION],
    VisitStatus.SENT_TO_COLLECTION: [VisitStatus.SAMPLE_COLLECTED],
    VisitStatus.SAMPLE_COLLECTED: [VisitStatus.DOCTOR_REVIEWED],
    VisitStatus.DOCTOR_REVIEWED: [VisitStatus.REPORT_READY],
    VisitStatus.REPORT_READY: [VisitStatus.REPORT_DELIVERED],
}

TEST_ORDER_TRANSITIONS = {
    TestOrderStatus.PENDING: [TestOrderStatus.SAMPLE_COLLECTED],
    TestOrderStatus.SAMPLE_COLLECTED: [TestOrderStatus.TESTING],
    TestOrderStatus.TESTING: [TestOrderStatus.RESULT_ENTERED],
    TestOrderStatus.RESULT_ENTERED: [
        TestOrderStatus.DOCTOR_REVIEWED,
        TestOrderStatus.RETEST_REQUIRED,
        TestOrderStatus.RECOLLECTION_REQUIRED,
    ],
    TestOrderStatus.DOCTOR_REVIEWED: [TestOrderStatus.REPORT_READY],
    TestOrderStatus.RETEST_REQUIRED: [TestOrderStatus.TESTING],
    TestOrderStatus.RECOLLECTION_REQUIRED: [TestOrderStatus.SAMPLE_COLLECTED],
    TestOrderStatus.REPORT_READY: [],  # Terminal state
}


# ── Visit status transitions ──────────────────────────────────────

def transition_visit_status(visit, new_status, actor, details=''):
    """
    Move a visit to a new status. Validates the transition is allowed,
    updates the visit, and creates an audit log entry.
    """
    old_status = visit.status
    allowed = VISIT_TRANSITIONS.get(old_status, [])

    if new_status not in allowed:
        raise TransitionError(
            f'Cannot transition visit {visit.visit_id} '
            f'from "{old_status}" to "{new_status}". '
            f'Allowed: {allowed}'
        )

    AuditLog.objects.create(
        visit=visit,
        action='visit_status_changed',
        old_value=old_status,
        new_value=new_status,
        details=details,
        actor=actor,
    )

    visit.status = new_status
    visit.save(update_fields=['status', 'updated_at'])

    return visit


# ── Test order status transitions ─────────────────────────────────

def transition_test_order_status(test_order, new_status, actor, details=''):
    """
    Move a test order to a new status. Validates the transition,
    updates, and logs.
    """
    old_status = test_order.status
    allowed = TEST_ORDER_TRANSITIONS.get(old_status, [])

    if new_status not in allowed:
        raise TransitionError(
            f'Cannot transition test order {test_order} '
            f'from "{old_status}" to "{new_status}". '
            f'Allowed: {allowed}'
        )

    test_order.status = new_status
    update_fields = ['status', 'updated_at']

    # Track timestamps for specific transitions
    if new_status == TestOrderStatus.RESULT_ENTERED:
        test_order.result_entered_at = timezone.now()
        test_order.result_entered_by = actor
        update_fields.extend(['result_entered_at', 'result_entered_by'])

    if new_status in (TestOrderStatus.DOCTOR_REVIEWED, TestOrderStatus.RETEST_REQUIRED,
                       TestOrderStatus.RECOLLECTION_REQUIRED):
        test_order.reviewed_by = actor
        test_order.reviewed_at = timezone.now()
        update_fields.extend(['reviewed_by', 'reviewed_at'])

    test_order.save(update_fields=update_fields)

    AuditLog.objects.create(
        visit=test_order.visit,
        test_order=test_order,
        action='test_order_status_changed',
        old_value=old_status,
        new_value=new_status,
        details=details,
        actor=actor,
    )

    # Check if this transition completes the visit
    if new_status == TestOrderStatus.REPORT_READY:
        check_visit_completion(test_order.visit, actor)

    return test_order


# ── Result editing ─────────────────────────────────────────────────

def enter_test_result(test_order, result_value, actor):
    """
    Enter test results for a test order.
    Only allowed when status is 'testing'.
    """
    if test_order.status != TestOrderStatus.TESTING:
        raise TransitionError(
            f'Cannot enter results for test order in status "{test_order.status}". '
            f'Must be in "testing" status.'
        )

    test_order.result_value = result_value
    test_order.result_entered_by = actor
    test_order.result_entered_at = timezone.now()
    test_order.status = TestOrderStatus.RESULT_ENTERED
    test_order.save(update_fields=[
        'result_value', 'result_entered_by', 'result_entered_at',
        'status', 'updated_at',
    ])

    AuditLog.objects.create(
        visit=test_order.visit,
        test_order=test_order,
        action='result_entered',
        new_value=str(result_value),
        actor=actor,
    )

    return test_order


def edit_test_result(test_order, new_value, actor, reason=''):
    """
    Doctor edits a previously entered result.
    Preserves the original value and logs the change.
    """
    if test_order.status != TestOrderStatus.RESULT_ENTERED:
        raise TransitionError(
            f'Cannot edit results for test order in status "{test_order.status}". '
            f'Must be in "result_entered" status.'
        )

    # Preserve original value (only if not already preserved)
    if test_order.original_value is None:
        test_order.original_value = test_order.result_value

    old_value_str = str(test_order.result_value)
    test_order.result_value = new_value
    test_order.save(update_fields=['result_value', 'original_value', 'updated_at'])

    AuditLog.objects.create(
        visit=test_order.visit,
        test_order=test_order,
        action='result_edited',
        old_value=old_value_str,
        new_value=str(new_value),
        details=f'Edited by doctor. Reason: {reason}' if reason else 'Edited by doctor.',
        actor=actor,
    )

    return test_order


# ── Visit completion check ─────────────────────────────────────────

def check_visit_completion(visit, actor):
    """
    Auto-promote visit to report_ready when ALL test orders are report_ready.
    Then generate a download token and trigger SMS.
    """
    if not visit.all_tests_ready:
        return False

    # Walk the visit through intermediate states if needed
    # Visit should be at sent_to_collection or later at this point
    if visit.status in (VisitStatus.SENT_TO_COLLECTION, VisitStatus.SAMPLE_COLLECTED):
        if visit.status == VisitStatus.SENT_TO_COLLECTION:
            transition_visit_status(visit, VisitStatus.SAMPLE_COLLECTED, actor,
                                    'All samples collected (auto-promoted).')
        transition_visit_status(visit, VisitStatus.DOCTOR_REVIEWED, actor,
                                'All tests reviewed by doctor.')
        transition_visit_status(visit, VisitStatus.REPORT_READY, actor,
                                'All test orders are report_ready.')

        # Generate download token and send SMS
        visit.generate_report_token()
        trigger_report_sms(visit, actor)
        return True

    return False


# ── Payment ────────────────────────────────────────────────────────

def confirm_payment(visit, actor, method='cash', amount=None, transaction_ref=''):
    """Mark payment as confirmed and transition visit status."""
    if visit.status != VisitStatus.PAYMENT_PENDING:
        raise TransitionError(
            f'Cannot confirm payment for visit in status "{visit.status}". '
            f'Must be in "payment_pending" status.'
        )

    # Create or update payment record
    payment, created = Payment.objects.update_or_create(
        visit=visit,
        defaults={
            'method': method,
            'amount': amount or visit.total_amount,
            'is_confirmed': True,
            'confirmed_by': actor,
            'confirmed_at': timezone.now(),
            'transaction_ref': transaction_ref,
        }
    )

    # Transition visit status
    transition_visit_status(
        visit, VisitStatus.PAYMENT_CONFIRMED, actor,
        f'Payment confirmed: ₹{payment.amount} via {payment.get_method_display()}'
    )

    return payment


# ── Sample collection ──────────────────────────────────────────────

def collect_sample(visit, sample_type, container_number, actor, notes=''):
    """
    Record sample collection and mark related test orders as sample_collected.
    """
    sample = Sample.objects.create(
        visit=visit,
        sample_type=sample_type,
        container_number=container_number,
        collected_by=actor,
        collected_at=timezone.now(),
        notes=notes,
    )

    # Mark test orders for this sample type as collected
    test_orders = visit.test_orders.filter(
        test__sample_type=sample_type,
        status=TestOrderStatus.PENDING,
    )
    for order in test_orders:
        transition_test_order_status(
            order, TestOrderStatus.SAMPLE_COLLECTED, actor,
            f'Sample collected in container #{container_number}'
        )

    AuditLog.objects.create(
        visit=visit,
        action='sample_collected',
        new_value=f'Container #{container_number} ({sample_type})',
        actor=actor,
    )

    # Transition visit status if all orders have had samples collected
    all_collected = not visit.test_orders.filter(status=TestOrderStatus.PENDING).exists()
    if all_collected and visit.status == VisitStatus.SENT_TO_COLLECTION:
        transition_visit_status(visit, VisitStatus.SAMPLE_COLLECTED, actor, "All samples collected")

    return sample


# ── SMS notification ───────────────────────────────────────────────

def trigger_report_sms(visit, actor):
    """Send SMS with report download link."""
    from notifications.sms import get_sms_backend

    token = visit.report_token
    if not token:
        token = visit.generate_report_token()

    report_url = f'{settings.REPORT_BASE_URL}/report/{token}/'

    message = (
        f'Dear {visit.patient_name}, your lab report for visit {visit.visit_id} '
        f'is ready. Download: {report_url} '
        f'(Link valid for {settings.REPORT_TOKEN_EXPIRY_HOURS} hours)'
    )

    backend = get_sms_backend()
    backend.send(visit.phone, message)

    # Log and transition
    AuditLog.objects.create(
        visit=visit,
        action='sms_sent',
        new_value=f'SMS sent to {visit.phone}',
        actor=actor,
    )

    if visit.status == VisitStatus.REPORT_READY:
        transition_visit_status(visit, VisitStatus.REPORT_DELIVERED, actor,
                                f'SMS sent to {visit.phone}')

    return True
