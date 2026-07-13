"""
Tests for the LabFlow core state machine and business logic.
"""
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth.models import User

from .models import (
    Visit, VisitStatus, TestOrder, TestOrderStatus,
    TestCatalog, Sample, Payment, AuditLog, SampleType,
)
from .services import (
    transition_visit_status, transition_test_order_status,
    confirm_payment, collect_sample, enter_test_result,
    edit_test_result, check_visit_completion, TransitionError,
)


class BaseTestCase(TestCase):
    """Shared setup for all test cases."""

    def setUp(self):
        self.receptionist = User.objects.create_user('reception', password='test')
        self.chamber = User.objects.create_user('chamber', password='test')
        self.collector = User.objects.create_user('collector', password='test')
        self.lab_tech = User.objects.create_user('labtech', password='test')
        self.doctor = User.objects.create_user('doctor', password='test')

        self.cbc = TestCatalog.objects.create(
            name='Complete Blood Count',
            short_code='CBC',
            sample_type=SampleType.BLOOD,
            department='Hematology',
            price=Decimal('350.00'),
            parameters=[
                {'name': 'Hemoglobin', 'unit': 'g/dL', 'ref_min': 12.0, 'ref_max': 16.0},
                {'name': 'WBC', 'unit': 'cells/μL', 'ref_min': 4000, 'ref_max': 11000},
            ]
        )

        self.lft = TestCatalog.objects.create(
            name='Liver Function Test',
            short_code='LFT',
            sample_type=SampleType.BLOOD,
            department='Biochemistry',
            price=Decimal('500.00'),
            parameters=[
                {'name': 'SGOT', 'unit': 'U/L', 'ref_min': 5, 'ref_max': 40},
                {'name': 'SGPT', 'unit': 'U/L', 'ref_min': 7, 'ref_max': 56},
            ]
        )

    def _create_visit(self):
        """Helper to create a standard visit with 2 test orders."""
        visit = Visit.objects.create(
            patient_name='Test Patient',
            age=30,
            gender='M',
            phone='9876543210',
            created_by=self.receptionist,
        )
        TestOrder.objects.create(visit=visit, test=self.cbc)
        TestOrder.objects.create(visit=visit, test=self.lft)
        return visit


class VisitIDTest(BaseTestCase):
    """Test visit ID auto-generation."""

    def test_visit_id_format(self):
        visit = Visit.objects.create(
            patient_name='Test', age=25, gender='M', phone='1234567890',
            created_by=self.receptionist,
        )
        self.assertTrue(visit.visit_id.startswith('LF-'))
        self.assertRegex(visit.visit_id, r'LF-\d{8}-\d{4}')

    def test_sequential_ids(self):
        v1 = Visit.objects.create(
            patient_name='Test 1', age=25, gender='M', phone='1234567890',
            created_by=self.receptionist,
        )
        v2 = Visit.objects.create(
            patient_name='Test 2', age=30, gender='F', phone='1234567891',
            created_by=self.receptionist,
        )
        # Extract sequence numbers
        seq1 = int(v1.visit_id.split('-')[-1])
        seq2 = int(v2.visit_id.split('-')[-1])
        self.assertEqual(seq2, seq1 + 1)


class VisitStatusTransitionTest(BaseTestCase):
    """Test the visit status state machine."""

    def test_valid_forward_transitions(self):
        visit = self._create_visit()
        self.assertEqual(visit.status, VisitStatus.REGISTERED)

        transition_visit_status(visit, VisitStatus.PAYMENT_PENDING, self.receptionist)
        self.assertEqual(visit.status, VisitStatus.PAYMENT_PENDING)

    def test_invalid_transition_raises(self):
        visit = self._create_visit()
        with self.assertRaises(TransitionError):
            # Can't skip from registered to approved
            transition_visit_status(visit, VisitStatus.APPROVED_BY_CHAMBER, self.chamber)

    def test_transition_creates_audit_log(self):
        visit = self._create_visit()
        transition_visit_status(visit, VisitStatus.PAYMENT_PENDING, self.receptionist)

        log = AuditLog.objects.filter(visit=visit, action='visit_status_changed').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.old_value, VisitStatus.REGISTERED)
        self.assertEqual(log.new_value, VisitStatus.PAYMENT_PENDING)
        self.assertEqual(log.actor, self.receptionist)


class PaymentTest(BaseTestCase):
    """Test payment confirmation flow."""

    def test_confirm_cash_payment(self):
        visit = self._create_visit()
        transition_visit_status(visit, VisitStatus.PAYMENT_PENDING, self.receptionist)

        payment = confirm_payment(visit, self.chamber, method='cash', amount=Decimal('850.00'))
        self.assertTrue(payment.is_confirmed)
        self.assertEqual(payment.confirmed_by, self.chamber)
        self.assertEqual(visit.status, VisitStatus.PAYMENT_CONFIRMED)

    def test_cannot_confirm_without_pending(self):
        visit = self._create_visit()
        with self.assertRaises(TransitionError):
            confirm_payment(visit, self.chamber)


class TestOrderTransitionTest(BaseTestCase):
    """Test the test order state machine."""

    def test_full_happy_path(self):
        visit = self._create_visit()
        order = visit.test_orders.first()

        transition_test_order_status(order, TestOrderStatus.SAMPLE_COLLECTED, self.collector)
        transition_test_order_status(order, TestOrderStatus.TESTING, self.lab_tech)

        enter_test_result(order, {'Hemoglobin': '14.5', 'WBC': '8500'}, self.lab_tech)
        self.assertEqual(order.status, TestOrderStatus.RESULT_ENTERED)

        transition_test_order_status(order, TestOrderStatus.DOCTOR_REVIEWED, self.doctor)
        transition_test_order_status(order, TestOrderStatus.REPORT_READY, self.doctor)
        self.assertEqual(order.status, TestOrderStatus.REPORT_READY)

    def test_retest_cycle(self):
        visit = self._create_visit()
        order = visit.test_orders.first()

        # Progress to result_entered
        transition_test_order_status(order, TestOrderStatus.SAMPLE_COLLECTED, self.collector)
        transition_test_order_status(order, TestOrderStatus.TESTING, self.lab_tech)
        enter_test_result(order, {'Hemoglobin': '5.0'}, self.lab_tech)

        # Doctor flags for retest
        transition_test_order_status(order, TestOrderStatus.RETEST_REQUIRED, self.doctor)
        self.assertEqual(order.status, TestOrderStatus.RETEST_REQUIRED)

        # Goes back to testing
        transition_test_order_status(order, TestOrderStatus.TESTING, self.lab_tech)
        self.assertEqual(order.status, TestOrderStatus.TESTING)


class ResultEditTest(BaseTestCase):
    """Test result editing with audit trail."""

    def test_edit_preserves_original(self):
        visit = self._create_visit()
        order = visit.test_orders.first()

        transition_test_order_status(order, TestOrderStatus.SAMPLE_COLLECTED, self.collector)
        transition_test_order_status(order, TestOrderStatus.TESTING, self.lab_tech)

        original = {'Hemoglobin': '14.5', 'WBC': '8500'}
        enter_test_result(order, original, self.lab_tech)

        corrected = {'Hemoglobin': '14.2', 'WBC': '8500'}
        edit_test_result(order, corrected, self.doctor, reason='Rechecked value')

        order.refresh_from_db()
        self.assertEqual(order.result_value, corrected)
        self.assertEqual(order.original_value, original)

    def test_edit_creates_audit_log(self):
        visit = self._create_visit()
        order = visit.test_orders.first()

        transition_test_order_status(order, TestOrderStatus.SAMPLE_COLLECTED, self.collector)
        transition_test_order_status(order, TestOrderStatus.TESTING, self.lab_tech)
        enter_test_result(order, {'Hemoglobin': '14.5'}, self.lab_tech)
        edit_test_result(order, {'Hemoglobin': '14.2'}, self.doctor)

        log = AuditLog.objects.filter(
            test_order=order, action='result_edited'
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.doctor)


class VisitCompletionTest(BaseTestCase):
    """Test auto-promotion when all tests are ready."""

    def test_visit_not_ready_until_all_tests_done(self):
        visit = self._create_visit()
        self.assertFalse(visit.all_tests_ready)

    def test_all_tests_ready_property(self):
        visit = self._create_visit()
        for order in visit.test_orders.all():
            order.status = TestOrderStatus.REPORT_READY
            order.save()

        visit.refresh_from_db()
        self.assertTrue(visit.all_tests_ready)


class ReportTokenTest(BaseTestCase):
    """Test report token generation and validation."""

    def test_token_generation(self):
        visit = self._create_visit()
        token = visit.generate_report_token()
        self.assertTrue(len(token) > 30)
        self.assertTrue(visit.is_report_token_valid())

    def test_token_uniqueness(self):
        v1 = self._create_visit()
        v2 = Visit.objects.create(
            patient_name='Another', age=40, gender='F', phone='9999999999',
            created_by=self.receptionist,
        )
        t1 = v1.generate_report_token()
        t2 = v2.generate_report_token()
        self.assertNotEqual(t1, t2)
