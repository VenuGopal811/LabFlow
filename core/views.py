from collections import OrderedDict
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.utils import timezone
from django.http import HttpResponseForbidden, Http404
from django.contrib.auth import authenticate

from .models import (
    Visit, VisitStatus, TestOrder, TestOrderStatus,
    Sample, Payment, AuditLog, TestCatalog
)
from .forms import VisitRegistrationForm
from .services import (
    transition_visit_status, transition_test_order_status,
    confirm_payment, collect_sample, enter_test_result,
    edit_test_result, trigger_report_sms, check_visit_completion,
    finalize_visit_report, TransitionError
)


# Helper function to get user groups for template rendering and permission checks
def get_user_context(request):
    groups = list(request.user.groups.values_list('name', flat=True))
    return {
        'user_groups': groups,
    }


def in_group(user, group_names):
    """Check if a user is in any of the specified groups (or is superuser)."""
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=group_names).exists()


# ── Redirect post-login ───────────────────────────────────────────
@login_required
def dashboard_redirect(request):
    """Redirect users to their respective dashboards based on roles."""
    if request.user.is_superuser:
        return redirect('reception_dashboard')
    
    groups = request.user.groups.values_list('name', flat=True)
    if 'reception' in groups:
        return redirect('reception_dashboard')
    elif 'chamber' in groups:
        return redirect('chamber_dashboard')
    elif 'collection' in groups:
        return redirect('collection_dashboard')
    elif 'lab' in groups:
        return redirect('lab_dashboard')
    
    # Fallback/Default
    return redirect('reception_dashboard')


# ── Reception Station Views ───────────────────────────────────────

@login_required
def reception_dashboard(request):
    if not in_group(request.user, ['reception']):
        return HttpResponseForbidden("Access Denied: You do not have permission to view Reception.")

    from datetime import timedelta, date
    from django.core.paginator import Paginator
    from django.db.models import Q

    # ── Hard 2-year backend constraint ────────────────────────────
    today = timezone.localdate()
    two_years_ago = today - timedelta(days=730)
    base_qs = Visit.objects.filter(created_at__date__gte=two_years_ago)

    # ── Read GET params ───────────────────────────────────────────
    search_query = request.GET.get('q', '').strip()
    date_from_str = request.GET.get('date_from', '').strip()
    date_to_str = request.GET.get('date_to', '').strip()

    date_clamped = False
    date_from = None
    date_to = None

    # Parse & clamp date_from
    if date_from_str:
        try:
            date_from = date.fromisoformat(date_from_str)
            if date_from < two_years_ago:
                date_from = two_years_ago
                date_clamped = True
        except ValueError:
            date_from = None

    # Parse date_to
    if date_to_str:
        try:
            date_to = date.fromisoformat(date_to_str)
            if date_to > today:
                date_to = today
        except ValueError:
            date_to = None

    # ── Build filtered queryset ───────────────────────────────────
    qs = base_qs
    is_search = bool(search_query or date_from or date_to)

    if search_query:
        qs = qs.filter(
            Q(patient_name__icontains=search_query)
            | Q(phone__icontains=search_query)
            | Q(visit_id__icontains=search_query)
        )

    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    qs = qs.order_by('-created_at')

    # ── Pagination (25 per page) ──────────────────────────────────
    paginator = Paginator(qs, 25)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    # ── Clamp message ─────────────────────────────────────────────
    if date_clamped:
        messages.info(
            request,
            f"The 'from' date has been adjusted to {two_years_ago.strftime('%d %b %Y')}"
            " — search is limited to the last 2 years."
        )

    # ── Stats (always based on today, unchanged) ──────────────────
    stats = {
        'total_today': Visit.objects.filter(created_at__date=today).count(),
        'registered_today': Visit.objects.filter(created_at__date=today, status=VisitStatus.REGISTERED).count(),
        'payment_pending_today': Visit.objects.filter(created_at__date=today, status=VisitStatus.PAYMENT_PENDING).count(),
        'completed_today': Visit.objects.filter(created_at__date=today, status__in=[VisitStatus.REPORT_READY, VisitStatus.REPORT_DELIVERED]).count(),
    }

    context = {
        'visits': page_obj,
        'page_obj': page_obj,
        'stats': stats,
        'search_query': search_query,
        'date_from': date_from.isoformat() if date_from else '',
        'date_to': date_to.isoformat() if date_to else '',
        'is_search': is_search,
        'two_years_ago_iso': two_years_ago.isoformat(),
        'today_iso': today.isoformat(),
        **get_user_context(request)
    }
    return render(request, 'reception/dashboard.html', context)


@login_required
def reception_register(request):
    if not in_group(request.user, ['reception']):
        return HttpResponseForbidden("Access Denied.")

    if request.method == 'POST':
        form = VisitRegistrationForm(request.POST)
        if form.is_valid():
            visit = form.save(commit=False)
            visit.created_by = request.user
            visit.save()
            
            # Save selected tests as TestOrder instances
            tests = form.cleaned_data['tests']
            for test in tests:
                TestOrder.objects.create(visit=visit, test=test)
            
            # Create AuditLog for registration
            AuditLog.objects.create(
                visit=visit,
                action='registered',
                new_value=f"Registered patient with {len(tests)} tests.",
                actor=request.user
            )
            
            messages.success(request, f"Patient {visit.patient_name} registered successfully. ID: {visit.visit_id}")
            return redirect('visit_detail', visit_id=visit.id)
    else:
        form = VisitRegistrationForm()
    
    context = {
        'form': form,
        **get_user_context(request)
    }
    return render(request, 'reception/register.html', context)


@login_required
def visit_detail(request, visit_id):
    # Any logged in user can view details for reference
    visit = get_object_or_404(Visit, id=visit_id)
    test_orders = visit.test_orders.all()
    samples = visit.samples.all()
    payment = getattr(visit, 'payment', None)
    audit_logs = visit.audit_logs.all().order_by('-timestamp')
    
    context = {
        'visit': visit,
        'test_orders': test_orders,
        'samples': samples,
        'payment': payment,
        'audit_logs': audit_logs,
        **get_user_context(request)
    }
    return render(request, 'reception/detail.html', context)


@login_required
def print_bill(request, visit_id):
    visit = get_object_or_404(Visit, id=visit_id)
    test_orders = visit.test_orders.exclude(status=TestOrderStatus.CANCELLED)
    total = sum(order.test.price for order in test_orders)
    
    context = {
        'visit': visit,
        'test_orders': test_orders,
        'total': total,
    }
    return render(request, 'reception/bill_print.html', context)


@login_required
def transition_to_payment_pending(request, visit_id):
    if not in_group(request.user, ['reception']):
        return HttpResponseForbidden("Access Denied.")
        
    visit = get_object_or_404(Visit, id=visit_id)
    try:
        transition_visit_status(visit, VisitStatus.PAYMENT_PENDING, request.user, "Marked payment pending by reception")
        messages.success(request, f"Visit {visit.visit_id} marked as Payment Pending.")
    except TransitionError as e:
        messages.error(request, str(e))
        
    return redirect('visit_detail', visit_id=visit.id)


@login_required
def send_report_sms(request, visit_id):
    visit = get_object_or_404(Visit, id=visit_id)
    if visit.status not in (VisitStatus.REPORT_READY, VisitStatus.REPORT_DELIVERED):
        messages.error(request, "SMS can only be sent when the report is ready or delivered.")
        return redirect('visit_detail', visit_id=visit.id)
    try:
        trigger_report_sms(visit, request.user)
        messages.success(request, f"SMS report link successfully sent to {visit.phone}.")
    except Exception as e:
        messages.error(request, f"Failed to send SMS: {e}")
        
    return redirect('visit_detail', visit_id=visit.id)


# ── Chamber Station Views ─────────────────────────────────────────

@login_required
def chamber_dashboard(request):
    if not in_group(request.user, ['chamber']):
        return HttpResponseForbidden("Access Denied: You do not have permission to view Chamber.")
        
    # Chamber needs:
    # 1. Visits in payment_pending to confirm manually (Cash payments)
    pending_payments = Visit.objects.filter(status=VisitStatus.PAYMENT_PENDING).order_by('-created_at')
    # 2. Visits in payment_confirmed awaiting chamber approval to proceed to collection
    awaiting_approval = Visit.objects.filter(status=VisitStatus.PAYMENT_CONFIRMED).order_by('-created_at')
    
    # Stats
    today = timezone.localdate()
    stats = {
        'pending_payments': pending_payments.count(),
        'awaiting_approval': awaiting_approval.count(),
        'approved_today': Visit.objects.filter(created_at__date=today, status=VisitStatus.APPROVED_BY_CHAMBER).count()
    }
    
    context = {
        'pending_payments': pending_payments,
        'awaiting_approval': awaiting_approval,
        'stats': stats,
        **get_user_context(request)
    }
    return render(request, 'chamber/dashboard.html', context)


@login_required
def chamber_confirm_payment(request, visit_id):
    if not in_group(request.user, ['chamber']):
        return HttpResponseForbidden("Access Denied.")
        
    visit = get_object_or_404(Visit, id=visit_id)
    if request.method == 'POST':
        method = request.POST.get('method', 'cash')
        transaction_ref = request.POST.get('transaction_ref', '')
        try:
            confirm_payment(
                visit=visit,
                actor=request.user,
                method=method,
                amount=visit.total_amount,
                transaction_ref=transaction_ref
            )
            messages.success(request, f"Payment confirmed for visit {visit.visit_id}.")
        except TransitionError as e:
            messages.error(request, str(e))
            
    return redirect('chamber_dashboard')


@login_required
def chamber_approve_visit(request, visit_id):
    if not in_group(request.user, ['chamber']):
        return HttpResponseForbidden("Access Denied.")
        
    visit = get_object_or_404(Visit, id=visit_id)
    if request.method == 'POST':
        try:
            # 1. Approve visit
            transition_visit_status(visit, VisitStatus.APPROVED_BY_CHAMBER, request.user, "Approved by chamber doctor")
            # 2. Send to Collection automatically
            transition_visit_status(visit, VisitStatus.SENT_TO_COLLECTION, request.user, "Automatically sent to sample collection queue")
            messages.success(request, f"Visit {visit.visit_id} approved and sent to sample collection.")
        except TransitionError as e:
            messages.error(request, str(e))
            
    return redirect('chamber_dashboard')


# ── Sample Collection Station Views ───────────────────────────────

@login_required
def collection_dashboard(request):
    if not in_group(request.user, ['collection']):
        return HttpResponseForbidden("Access Denied.")
        
    # Queue is visits in SENT_TO_COLLECTION status
    collection_queue = Visit.objects.filter(status=VisitStatus.SENT_TO_COLLECTION).order_by('created_at')
    
    # Count of pending collections
    stats = {
        'pending': collection_queue.count(),
        'collected_today': Sample.objects.filter(collected_at__date=timezone.localdate()).count()
    }
    
    context = {
        'queue': collection_queue,
        'stats': stats,
        **get_user_context(request)
    }
    return render(request, 'collection/dashboard.html', context)


@login_required
def collection_collect(request, visit_id):
    if not in_group(request.user, ['collection']):
        return HttpResponseForbidden("Access Denied.")
        
    visit = get_object_or_404(Visit, id=visit_id)
    
    # We need to collect samples matching the sample types needed by the test orders that need collection
    test_orders = visit.test_orders.exclude(status=TestOrderStatus.CANCELLED)
    pending_orders = visit.test_orders.filter(status__in=[TestOrderStatus.PENDING, TestOrderStatus.RECOLLECTION_REQUIRED])
    # Group required sample types
    required_samples = list(set(order.test.sample_type for order in pending_orders))
    
    if request.method == 'POST':
        # Process sample container numbers
        errors = []
        collected_any = False
        
        for sample_type in required_samples:
            container_num = request.POST.get(f'container_{sample_type}')
            notes = request.POST.get(f'notes_{sample_type}', '')
            
            if container_num:
                try:
                    collect_sample(
                        visit=visit,
                        sample_type=sample_type,
                        container_number=container_num,
                        actor=request.user,
                        notes=notes
                    )
                    collected_any = True
                except Exception as e:
                    errors.append(f"Error collecting {sample_type}: {str(e)}")
            else:
                errors.append(f"Container number required for {sample_type.upper()} sample.")
                
        if errors:
            for err in errors:
                messages.error(request, err)
        
        if collected_any and not errors:
            messages.success(request, f"Samples recorded for visit {visit.visit_id}.")
            return redirect('collection_dashboard')
            
    context = {
        'visit': visit,
        'required_samples': required_samples,
        'test_orders': test_orders,
        **get_user_context(request)
    }
    return render(request, 'collection/collect.html', context)


# ── Lab Tech Station Views ────────────────────────────────────────

@login_required
def lab_dashboard(request):
    if not in_group(request.user, ['lab']):
        return HttpResponseForbidden("Access Denied.")
        
    # Queue is TestOrder records in SAMPLE_COLLECTED, TESTING, or RETEST_REQUIRED status
    lab_queue = TestOrder.objects.filter(
        status__in=[TestOrderStatus.SAMPLE_COLLECTED, TestOrderStatus.TESTING, TestOrderStatus.RETEST_REQUIRED]
    ).select_related('visit', 'test').order_by('visit__created_at', 'created_at')
    
    # Group test orders by visit so multiple tests for same patient appear together
    grouped_queue = OrderedDict()
    for order in lab_queue:
        visit = order.visit
        if visit.id not in grouped_queue:
            grouped_queue[visit.id] = {
                'visit': visit,
                'orders': [],
            }
        grouped_queue[visit.id]['orders'].append(order)
    
    # Stats
    today = timezone.localdate()
    stats = {
        'pending': lab_queue.count(),
        'entered_today': TestOrder.objects.filter(result_entered_at__date=today).count()
    }
    
    context = {
        'grouped_queue': list(grouped_queue.values()),
        'stats': stats,
        **get_user_context(request)
    }
    return render(request, 'lab/dashboard.html', context)


@login_required
def lab_enter_results(request, order_id):
    """Legacy single-order URL — redirect to the visit-level page."""
    if not in_group(request.user, ['lab']):
        return HttpResponseForbidden("Access Denied.")
    order = get_object_or_404(TestOrder, id=order_id)
    return redirect('lab_enter_results_visit', visit_id=order.visit.id)


@login_required
def lab_enter_results_visit(request, visit_id):
    """Enter results for ALL pending test orders of a visit on one page."""
    if not in_group(request.user, ['lab']):
        return HttpResponseForbidden("Access Denied.")

    visit = get_object_or_404(Visit, id=visit_id)
    eligible_statuses = [TestOrderStatus.SAMPLE_COLLECTED, TestOrderStatus.TESTING, TestOrderStatus.RETEST_REQUIRED]
    orders = visit.test_orders.filter(status__in=eligible_statuses).select_related('test').order_by('created_at')

    if not orders.exists():
        messages.info(request, f"No tests currently awaiting result entry for {visit.patient_name}.")
        return redirect('lab_dashboard')

    # Transition each eligible order to TESTING if it was in SAMPLE_COLLECTED or RETEST_REQUIRED
    for order in orders:
        if order.status in (TestOrderStatus.SAMPLE_COLLECTED, TestOrderStatus.RETEST_REQUIRED):
            try:
                transition_test_order_status(order, TestOrderStatus.TESTING, request.user,
                                             "Started result entry process in laboratory")
            except TransitionError as e:
                messages.error(request, f"Unable to update status for {order.test.name}: {e}")

    # Re-fetch after transitions
    orders = visit.test_orders.filter(status__in=eligible_statuses).select_related('test').order_by('created_at')

    if request.method == 'POST':
        submitted_order_ids = request.POST.getlist('order_ids')
        all_success = True

        for order in orders:
            if str(order.id) not in submitted_order_ids:
                continue

            parameters = order.test.parameters
            results = {}
            for param in parameters:
                param_name = param['name']
                value = request.POST.get(f"order_{order.id}_param_{param_name}", "").strip()
                results[param_name] = value

            try:
                enter_test_result(order, results, request.user)
            except TransitionError as e:
                messages.error(request, f"{order.test.name}: {e}")
                all_success = False

        if all_success:
            messages.success(request, f"All results submitted for {visit.patient_name} ({visit.visit_id}).")
            return redirect('lab_dashboard')

    # Build per-order parameter field data for the template
    orders_with_fields = []
    for order in orders:
        parameters = order.test.parameters
        existing_results = order.result_value or {}
        param_fields = []
        for param in parameters:
            name = param['name']
            unit = param.get('unit', '')
            ref_min = param.get('ref_min')
            ref_max = param.get('ref_max')

            ref_range_str = ""
            if ref_min is not None and ref_max is not None:
                ref_range_str = f"{ref_min} - {ref_max}"
            elif ref_min is not None:
                ref_range_str = f"> {ref_min}"
            elif ref_max is not None:
                ref_range_str = f"< {ref_max}"

            param_fields.append({
                'name': name,
                'unit': unit,
                'ref_range': ref_range_str,
                'value': existing_results.get(name, '')
            })

        orders_with_fields.append({
            'order': order,
            'param_fields': param_fields,
        })

    import json
    calc_config = {}
    
    for order in orders:
        if order.test.parameter_groups:
            calc_config[order.id] = order.test.parameter_groups

    context = {
        'visit': visit,
        'orders_with_fields': orders_with_fields,
        'calc_config_json': json.dumps(calc_config),
        **get_user_context(request)
    }
    return render(request, 'lab/enter_results.html', context)


# ── Doctor Review / Pathology Approval Views ──────────────────────

@login_required
def doctor_dashboard(request):
    if not in_group(request.user, ['chamber']): # Chamber has doctor permissions
        return HttpResponseForbidden("Access Denied.")
        
    # Queue is TestOrder records in RESULT_ENTERED status
    review_queue = TestOrder.objects.filter(
        status=TestOrderStatus.RESULT_ENTERED
    ).select_related('visit', 'test').order_by('visit__created_at', 'result_entered_at')
    
    # Group test orders by visit so multiple tests for same patient appear together
    grouped_queue = OrderedDict()
    for order in review_queue:
        visit = order.visit
        if visit.id not in grouped_queue:
            grouped_queue[visit.id] = {
                'visit': visit,
                'orders': [],
            }
        grouped_queue[visit.id]['orders'].append(order)
    
    stats = {
        'pending': review_queue.count(),
        'reviewed_today': TestOrder.objects.filter(
            reviewed_at__date=timezone.localdate(),
            status__in=[TestOrderStatus.REPORT_READY, TestOrderStatus.RETEST_REQUIRED, TestOrderStatus.RECOLLECTION_REQUIRED]
        ).count()
    }
    
    context = {
        'grouped_queue': list(grouped_queue.values()),
        'stats': stats,
        **get_user_context(request)
    }
    return render(request, 'doctor/dashboard.html', context)


@login_required
def doctor_review(request, order_id):
    """Legacy single-order URL — redirect to the visit-level page."""
    if not in_group(request.user, ['chamber']):
        return HttpResponseForbidden("Access Denied.")
    order = get_object_or_404(TestOrder, id=order_id)
    return redirect('doctor_review_visit', visit_id=order.visit.id)


@login_required
def doctor_review_visit(request, visit_id):
    """Review ALL pending test orders for a visit on one page."""
    if not in_group(request.user, ['chamber']):
        return HttpResponseForbidden("Access Denied.")

    visit = get_object_or_404(Visit, id=visit_id)
    orders = visit.test_orders.filter(
        status=TestOrderStatus.RESULT_ENTERED
    ).select_related('test').order_by('created_at')

    if not orders.exists():
        messages.info(request, f"No tests currently awaiting review for {visit.patient_name}.")
        return redirect('doctor_dashboard')

    if request.method == 'POST':
        # The form posts a per-order action: action_<order_id> = approve|edit|retest|recollect
        reason = request.POST.get('reason', '')
        processed_any = False

        for order in orders:
            action = request.POST.get(f'action_{order.id}')
            if not action:
                continue

            try:
                if action == 'approve':
                    transition_test_order_status(order, TestOrderStatus.DOCTOR_REVIEWED, request.user, "Approved results")
                    transition_test_order_status(order, TestOrderStatus.REPORT_READY, request.user, "Results marked report_ready")
                    messages.success(request, f"✅ {order.test.name} — approved.")
                    processed_any = True

                elif action == 'edit':
                    updated_results = {}
                    for param in order.test.parameters:
                        name = param['name']
                        updated_results[name] = request.POST.get(f"order_{order.id}_param_{name}", "").strip()
                    edit_test_result(order, updated_results, request.user, reason)
                    transition_test_order_status(order, TestOrderStatus.DOCTOR_REVIEWED, request.user, f"Edited and Approved. Reason: {reason}")
                    transition_test_order_status(order, TestOrderStatus.REPORT_READY, request.user, "Results marked report_ready")
                    messages.success(request, f"✏️ {order.test.name} — edited & approved.")
                    processed_any = True

                elif action == 'retest':
                    transition_test_order_status(order, TestOrderStatus.RETEST_REQUIRED, request.user, f"Retest requested. Reason: {reason}")
                    messages.warning(request, f"🔄 {order.test.name} — retest requested.")
                    processed_any = True

                elif action == 'recollect':
                    transition_test_order_status(order, TestOrderStatus.RECOLLECTION_REQUIRED, request.user, f"Recollection requested. Reason: {reason}")
                    messages.warning(request, f"🔄 {order.test.name} — recollection requested.")
                    processed_any = True

            except TransitionError as e:
                messages.error(request, f"{order.test.name}: {e}")

        if processed_any:
            return redirect('doctor_dashboard')

    # Build per-order parameter field data for the template
    orders_with_fields = []
    for order in orders:
        parameters = order.test.parameters
        results = order.result_value or {}
        param_fields = []
        for param in parameters:
            name = param['name']
            unit = param.get('unit', '')
            ref_min = param.get('ref_min')
            ref_max = param.get('ref_max')

            ref_range_str = ""
            if ref_min is not None and ref_max is not None:
                ref_range_str = f"{ref_min} - {ref_max}"
            elif ref_min is not None:
                ref_range_str = f"> {ref_min}"
            elif ref_max is not None:
                ref_range_str = f"< {ref_max}"

            val = results.get(name, '')

            # Simple high/low check if numeric
            abnormal = False
            try:
                f_val = float(val)
                if ref_min is not None and f_val < float(ref_min):
                    abnormal = True
                if ref_max is not None and f_val > float(ref_max):
                    abnormal = True
            except ValueError:
                pass

            param_fields.append({
                'name': name,
                'unit': unit,
                'ref_range': ref_range_str,
                'value': val,
                'abnormal': abnormal,
            })

        orders_with_fields.append({
            'order': order,
            'param_fields': param_fields,
        })

    context = {
        'visit': visit,
        'orders_with_fields': orders_with_fields,
        **get_user_context(request)
    }
    return render(request, 'doctor/review.html', context)


# ── Reporting Station Views ───────────────────────────────────────

@login_required
def reporting_dashboard(request):
    if not in_group(request.user, ['reception', 'lab', 'chamber']):
        return HttpResponseForbidden("Access Denied.")

    queue = Visit.objects.filter(status=VisitStatus.PENDING_REPORTING).order_by('created_at')

    stats = {
        'pending': queue.count(),
        'finalized_today': Visit.objects.filter(
            updated_at__date=timezone.localdate(),
            status__in=[VisitStatus.REPORT_READY, VisitStatus.REPORT_DELIVERED]
        ).count()
    }

    context = {
        'queue': queue,
        'stats': stats,
        **get_user_context(request)
    }
    return render(request, 'reporting/dashboard.html', context)


@login_required
def reporting_detail(request, visit_id):
    if not in_group(request.user, ['reception', 'lab', 'chamber']):
        return HttpResponseForbidden("Access Denied.")

    visit = get_object_or_404(Visit, id=visit_id)
    if visit.status != VisitStatus.PENDING_REPORTING:
        messages.warning(request, f"Visit {visit.visit_id} is not in Pending Reporting status.")
        return redirect('reporting_dashboard')

    # Approved test orders sorted by display_order, created_at
    test_orders = visit.test_orders.filter(
        status=TestOrderStatus.REPORT_READY
    ).select_related('test').order_by('display_order', 'created_at')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'save_order':
            submitted_order_ids = request.POST.getlist('order_ids')
            for index, order_id in enumerate(submitted_order_ids):
                TestOrder.objects.filter(id=order_id, visit=visit).update(display_order=index)
            messages.success(request, "Test order layout saved successfully.")
            return redirect('reporting_detail', visit_id=visit.id)

        elif action == 'finalize':
            submitted_order_ids = request.POST.getlist('order_ids')
            if submitted_order_ids:
                for index, order_id in enumerate(submitted_order_ids):
                    TestOrder.objects.filter(id=order_id, visit=visit).update(display_order=index)

            try:
                finalize_visit_report(visit, request.user)
                messages.success(request, f"Report for visit {visit.visit_id} finalized and SMS sent.")
                return redirect('reporting_dashboard')
            except TransitionError as e:
                messages.error(request, f"Failed to finalize report: {e}")
                return redirect('reporting_detail', visit_id=visit.id)

    context = {
        'visit': visit,
        'test_orders': test_orders,
        **get_user_context(request)
    }
    return render(request, 'reporting/detail.html', context)


# ── Test Order Cancellation ────────────────────────────────────────

@login_required
def cancel_test_order(request):
    """Cancel a TestOrder. Requires doctor role and password re-verification."""
    if request.method != 'POST':
        return HttpResponseForbidden("Method not allowed.")

    if not in_group(request.user, ['chamber']):
        return HttpResponseForbidden("Access Denied: Only doctors can cancel test orders.")

    order_id = request.POST.get('order_id')
    password = request.POST.get('password', '')
    reason = request.POST.get('reason', '').strip()

    order = get_object_or_404(TestOrder, id=order_id)
    visit = order.visit

    # Validate reason is provided
    if not reason:
        messages.error(request, "A cancellation reason is required.")
        return redirect('visit_detail', visit_id=visit.id)

    # Authenticate doctor with their own password
    auth_user = authenticate(username=request.user.username, password=password)
    if auth_user is None or auth_user.pk != request.user.pk:
        messages.error(request, "Incorrect password. Cancellation denied.")
        return redirect('visit_detail', visit_id=visit.id)

    # Validate the test order is in a cancellable state
    non_cancellable = (TestOrderStatus.REPORT_READY, TestOrderStatus.CANCELLED)
    if order.status in non_cancellable:
        messages.error(request, f"Cannot cancel a test order with status '{order.get_status_display()}'.")
        return redirect('visit_detail', visit_id=visit.id)

    # Perform the cancellation
    old_status = order.get_status_display()
    try:
        transition_test_order_status(
            order, TestOrderStatus.CANCELLED, request.user,
            f"Cancelled by doctor. Previous status: {old_status}. Reason: {reason}"
        )
        messages.success(request, f"Test '{order.test.name}' has been cancelled.")
    except TransitionError as e:
        messages.error(request, f"Failed to cancel test order: {e}")

    return redirect('visit_detail', visit_id=visit.id)
