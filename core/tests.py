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

        from django.contrib.auth.models import Group
        reception_group, _ = Group.objects.get_or_create(name='reception')
        lab_group, _ = Group.objects.get_or_create(name='lab')
        chamber_group, _ = Group.objects.get_or_create(name='chamber')
        collection_group, _ = Group.objects.get_or_create(name='collection')

        self.receptionist.groups.add(reception_group)
        self.chamber.groups.add(chamber_group)
        self.collector.groups.add(collection_group)
        self.lab_tech.groups.add(lab_group)
        self.doctor.groups.add(chamber_group)

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

    def test_invalid_non_numeric_result_raises(self):
        visit = self._create_visit()
        order = visit.test_orders.first()

        transition_test_order_status(order, TestOrderStatus.SAMPLE_COLLECTED, self.collector)
        transition_test_order_status(order, TestOrderStatus.TESTING, self.lab_tech)

        with self.assertRaises(TransitionError):
            enter_test_result(order, {'Hemoglobin': 'not-a-number'}, self.lab_tech)


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


class SampleCollectionTransitionTest(BaseTestCase):
    """Test visit transition to SAMPLE_COLLECTED."""

    def test_visit_transitions_on_collection(self):
        visit = self._create_visit()
        # Progress visit to sent_to_collection
        transition_visit_status(visit, VisitStatus.PAYMENT_PENDING, self.receptionist)
        confirm_payment(visit, self.chamber, method='cash', amount=Decimal('850.00'))
        transition_visit_status(visit, VisitStatus.APPROVED_BY_CHAMBER, self.chamber)
        transition_visit_status(visit, VisitStatus.SENT_TO_COLLECTION, self.chamber)

        self.assertEqual(visit.status, VisitStatus.SENT_TO_COLLECTION)

        # Collect sample (CBC and LFT both use BLOOD, so collecting BLOOD collects all)
        collect_sample(visit, SampleType.BLOOD, 'C-12345', self.collector)

        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.SAMPLE_COLLECTED)

    def test_recollect_reverts_visit_status(self):
        visit = self._create_visit()
        # Progress visit to SAMPLE_COLLECTED
        transition_visit_status(visit, VisitStatus.PAYMENT_PENDING, self.receptionist)
        confirm_payment(visit, self.chamber, method='cash', amount=Decimal('850.00'))
        transition_visit_status(visit, VisitStatus.APPROVED_BY_CHAMBER, self.chamber)
        transition_visit_status(visit, VisitStatus.SENT_TO_COLLECTION, self.chamber)
        collect_sample(visit, SampleType.BLOOD, 'C-12345', self.collector)

        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.SAMPLE_COLLECTED)

        # Trigger recollection for CBC
        order = visit.test_orders.filter(test__short_code='CBC').first()
        # Ensure status is RESULT_ENTERED first so it can transition to RECOLLECTION_REQUIRED
        order.status = TestOrderStatus.RESULT_ENTERED
        order.save()

        transition_test_order_status(order, TestOrderStatus.RECOLLECTION_REQUIRED, self.doctor)

        visit.refresh_from_db()
        # Visit status should have reverted back to SENT_TO_COLLECTION
        self.assertEqual(visit.status, VisitStatus.SENT_TO_COLLECTION)

    def test_recollect_reverts_from_report_ready(self):
        visit = self._create_visit()
        # Progress visit to REPORT_READY
        transition_visit_status(visit, VisitStatus.PAYMENT_PENDING, self.receptionist)
        confirm_payment(visit, self.chamber, method='cash', amount=Decimal('850.00'))
        transition_visit_status(visit, VisitStatus.APPROVED_BY_CHAMBER, self.chamber)
        transition_visit_status(visit, VisitStatus.SENT_TO_COLLECTION, self.chamber)
        collect_sample(visit, SampleType.BLOOD, 'C-12345', self.collector)
        
        # Complete all test orders
        for order in visit.test_orders.all():
            order.status = TestOrderStatus.REPORT_READY
            order.save()
        check_visit_completion(visit, self.doctor)
        
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.PENDING_REPORTING)

        # Finalize report
        from .services import finalize_visit_report
        finalize_visit_report(visit, self.doctor)
        
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.REPORT_DELIVERED)
        
        # Now trigger recollection on CBC
        order = visit.test_orders.filter(test__short_code='CBC').first()
        order.status = TestOrderStatus.RESULT_ENTERED
        order.save()
        
        transition_test_order_status(order, TestOrderStatus.RECOLLECTION_REQUIRED, self.doctor)
        
        visit.refresh_from_db()
        # Visit status should have reverted back to SENT_TO_COLLECTION
        self.assertEqual(visit.status, VisitStatus.SENT_TO_COLLECTION)


class PhoneValidationFormTest(BaseTestCase):
    """Test phone number validation in VisitRegistrationForm."""

    def test_valid_phone_formats(self):
        from core.forms import VisitRegistrationForm
        data = {
            'patient_name': 'Valid Patient',
            'age': 30,
            'gender': 'M',
            'phone': '9876543210',
            'tests': [self.cbc.id]
        }
        form = VisitRegistrationForm(data=data)
        self.assertTrue(form.is_valid())

        # Test with country code prefix
        data['phone'] = '+919876543210'
        form = VisitRegistrationForm(data=data)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['phone'], '9876543210')

    def test_invalid_phone_formats(self):
        from core.forms import VisitRegistrationForm
        data = {
            'patient_name': 'Invalid Patient',
            'age': 30,
            'gender': 'M',
            'phone': 'not-a-number',
            'tests': [self.cbc.id]
        }
        form = VisitRegistrationForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn('phone', form.errors)

        # Test too short
        data['phone'] = '12345'
        form = VisitRegistrationForm(data=data)
        self.assertFalse(form.is_valid())


class SendSMSViewTest(BaseTestCase):
    """Test manual send SMS report action."""

    def test_send_sms_action(self):
        from django.urls import reverse
        visit = self._create_visit()
        
        # Progress visit to SAMPLE_COLLECTED so check_visit_completion can promote it
        transition_visit_status(visit, VisitStatus.PAYMENT_PENDING, self.receptionist)
        confirm_payment(visit, self.chamber, method='cash', amount=Decimal('850.00'))
        transition_visit_status(visit, VisitStatus.APPROVED_BY_CHAMBER, self.chamber)
        transition_visit_status(visit, VisitStatus.SENT_TO_COLLECTION, self.chamber)
        collect_sample(visit, SampleType.BLOOD, 'C-12345', self.collector)
        
        # Complete all test orders
        for order in visit.test_orders.all():
            order.status = TestOrderStatus.REPORT_READY
            order.save()
        check_visit_completion(visit, self.doctor)
        
        # 1. Verify manual sending SMS in PENDING_REPORTING fails / is gated
        self.client.force_login(self.receptionist)
        url = reverse('send_report_sms', kwargs={'visit_id': visit.id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        visit.refresh_from_db()
        self.assertFalse(visit.audit_logs.filter(action='sms_sent').exists())

        # 2. Finalize report (this transitions to REPORT_READY and auto-triggers SMS)
        from .services import finalize_visit_report
        finalize_visit_report(visit, self.doctor)
        
        # 3. Manually send SMS again (now allowed because status is REPORT_READY or REPORT_DELIVERED)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(visit.audit_logs.filter(action='sms_sent').exists())


class ReportingPipelineTest(BaseTestCase):
    """Test cases for the new Reporting pipeline stage."""

    def test_visit_autopromotes_to_pending_reporting(self):
        visit = self._create_visit()
        # Complete all test orders
        for order in visit.test_orders.all():
            order.status = TestOrderStatus.REPORT_READY
            order.save()
        
        # Advance visit status to SAMPLE_COLLECTED to make it eligible
        transition_visit_status(visit, VisitStatus.PAYMENT_PENDING, self.receptionist)
        confirm_payment(visit, self.chamber, method='cash', amount=Decimal('850.00'))
        transition_visit_status(visit, VisitStatus.APPROVED_BY_CHAMBER, self.chamber)
        transition_visit_status(visit, VisitStatus.SENT_TO_COLLECTION, self.chamber)
        collect_sample(visit, SampleType.BLOOD, 'C-12345', self.collector)
        
        # Run completion check manually
        check_visit_completion(visit, self.doctor)
        
        visit.refresh_from_db()
        # check_visit_completion should have promoted it to PENDING_REPORTING
        self.assertEqual(visit.status, VisitStatus.PENDING_REPORTING)

    def test_reporting_dashboard_access_and_reorder(self):
        from django.urls import reverse
        visit = self._create_visit()
        for order in visit.test_orders.all():
            order.status = TestOrderStatus.REPORT_READY
            order.save()
        
        visit.status = VisitStatus.PENDING_REPORTING
        visit.save()

        # Login as receptionist
        self.client.force_login(self.receptionist)
        
        dashboard_url = reverse('reporting_dashboard')
        response = self.client.get(dashboard_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, visit.visit_id)

        # Post to reorder test orders
        orders = list(visit.test_orders.all())
        order_ids = [str(orders[1].id), str(orders[0].id)]
        
        detail_url = reverse('reporting_detail', kwargs={'visit_id': visit.id})
        response = self.client.post(detail_url, {'action': 'save_order', 'order_ids': order_ids})
        self.assertEqual(response.status_code, 302)

        # Verify display_order updated
        orders[0].refresh_from_db()
        orders[1].refresh_from_db()
        self.assertEqual(orders[1].display_order, 0)
        self.assertEqual(orders[0].display_order, 1)

    def test_reporting_finalize_transitions_and_sends_sms(self):
        from django.urls import reverse
        visit = self._create_visit()
        for order in visit.test_orders.all():
            order.status = TestOrderStatus.REPORT_READY
            order.save()
        
        visit.status = VisitStatus.PENDING_REPORTING
        visit.save()

        self.client.force_login(self.receptionist)
        detail_url = reverse('reporting_detail', kwargs={'visit_id': visit.id})
        
        orders = list(visit.test_orders.all())
        order_ids = [str(orders[1].id), str(orders[0].id)]
        
        response = self.client.post(detail_url, {'action': 'finalize', 'order_ids': order_ids})
        self.assertEqual(response.status_code, 302)

        visit.refresh_from_db()
        # Finalizing transitions to REPORT_READY, then SMS sends and shifts to REPORT_DELIVERED
        self.assertEqual(visit.status, VisitStatus.REPORT_DELIVERED)
        self.assertTrue(visit.audit_logs.filter(action='sms_sent').exists())

    def test_retest_reverts_visit_to_sample_collected(self):
        visit = self._create_visit()
        visit.status = VisitStatus.PENDING_REPORTING
        visit.save()

        order = visit.test_orders.first()
        order.status = TestOrderStatus.RESULT_ENTERED
        order.save()

        # Doctor requests a retest
        transition_test_order_status(order, TestOrderStatus.RETEST_REQUIRED, self.doctor)
        
        visit.refresh_from_db()
        # Should drop back to SAMPLE_COLLECTED
        self.assertEqual(visit.status, VisitStatus.SAMPLE_COLLECTED)


class CancelTestOrderTest(BaseTestCase):
    """Test cases for the TestOrder cancellation feature."""

    def _advance_visit_to_sample_collected(self, visit):
        """Helper to walk a visit through to SAMPLE_COLLECTED."""
        transition_visit_status(visit, VisitStatus.PAYMENT_PENDING, self.receptionist)
        confirm_payment(visit, self.chamber, method='cash', amount=Decimal('850.00'))
        transition_visit_status(visit, VisitStatus.APPROVED_BY_CHAMBER, self.chamber)
        transition_visit_status(visit, VisitStatus.SENT_TO_COLLECTION, self.chamber)
        collect_sample(visit, SampleType.BLOOD, 'C-99999', self.collector)

    def test_cancel_succeeds_with_correct_password(self):
        from django.urls import reverse
        visit = self._create_visit()
        order = visit.test_orders.first()

        self.client.force_login(self.doctor)
        url = reverse('cancel_test_order')
        response = self.client.post(url, {
            'order_id': order.id,
            'password': 'test',
            'reason': 'Patient opted out of this test.',
        })
        self.assertEqual(response.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(order.status, TestOrderStatus.CANCELLED)

        # Verify audit log records old status and reason
        log = AuditLog.objects.filter(
            test_order=order, action='test_order_status_changed', new_value='cancelled'
        ).first()
        self.assertIsNotNone(log)
        self.assertIn('Reason: Patient opted out', log.details)
        self.assertIn('Pending', log.details)

    def test_cancel_rejected_with_wrong_password(self):
        from django.urls import reverse
        visit = self._create_visit()
        order = visit.test_orders.first()

        self.client.force_login(self.doctor)
        url = reverse('cancel_test_order')
        response = self.client.post(url, {
            'order_id': order.id,
            'password': 'wrongpassword',
            'reason': 'Some reason',
        })
        self.assertEqual(response.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(order.status, TestOrderStatus.PENDING)

    def test_cancel_forbidden_for_non_chamber_user(self):
        from django.urls import reverse
        visit = self._create_visit()
        order = visit.test_orders.first()

        self.client.force_login(self.lab_tech)
        url = reverse('cancel_test_order')
        response = self.client.post(url, {
            'order_id': order.id,
            'password': 'test',
            'reason': 'Some reason',
        })
        self.assertEqual(response.status_code, 403)

        order.refresh_from_db()
        self.assertEqual(order.status, TestOrderStatus.PENDING)

    def test_cancel_report_ready_order_rejected(self):
        from django.urls import reverse
        visit = self._create_visit()
        order = visit.test_orders.first()
        order.status = TestOrderStatus.REPORT_READY
        order.save()

        self.client.force_login(self.doctor)
        url = reverse('cancel_test_order')
        response = self.client.post(url, {
            'order_id': order.id,
            'password': 'test',
            'reason': 'Trying to cancel a reported test',
        })
        self.assertEqual(response.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(order.status, TestOrderStatus.REPORT_READY)

    def test_cancel_advances_visit_when_remaining_orders_ready(self):
        """Cancelling the last blocking order should auto-promote the visit."""
        from django.urls import reverse
        visit = self._create_visit()
        self._advance_visit_to_sample_collected(visit)

        orders = list(visit.test_orders.all())
        # Mark first order as REPORT_READY (complete)
        orders[0].status = TestOrderStatus.REPORT_READY
        orders[0].save()

        # Cancel the second order — now all active orders are REPORT_READY
        self.client.force_login(self.doctor)
        url = reverse('cancel_test_order')
        response = self.client.post(url, {
            'order_id': orders[1].id,
            'password': 'test',
            'reason': 'Not needed anymore',
        })
        self.assertEqual(response.status_code, 302)

        orders[1].refresh_from_db()
        self.assertEqual(orders[1].status, TestOrderStatus.CANCELLED)

        visit.refresh_from_db()
        # Visit should have auto-promoted to PENDING_REPORTING
        self.assertEqual(visit.status, VisitStatus.PENDING_REPORTING)

    def test_cancel_requires_reason(self):
        from django.urls import reverse
        visit = self._create_visit()
        order = visit.test_orders.first()

        self.client.force_login(self.doctor)
        url = reverse('cancel_test_order')
        response = self.client.post(url, {
            'order_id': order.id,
            'password': 'test',
            'reason': '',
        })
        self.assertEqual(response.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(order.status, TestOrderStatus.PENDING)
