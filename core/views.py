from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.utils import timezone
from django.http import HttpResponseForbidden, Http404

from .models import (
    Visit, VisitStatus, TestOrder, TestOrderStatus,
    Sample, Payment, AuditLog, TestCatalog
)
from .forms import VisitRegistrationForm
from .services import (
    transition_visit_status, transition_test_order_status,
    confirm_payment, collect_sample, enter_test_result,
    edit_test_result, trigger_report_sms, check_visit_completion,
    TransitionError
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

    visits = Visit.objects.all().order_by('-created_at')[:50]
    
    # Stats
    today = timezone.localdate()
    stats = {
        'total_today': Visit.objects.filter(created_at__date=today).count(),
        'registered_today': Visit.objects.filter(created_at__date=today, status=VisitStatus.REGISTERED).count(),
        'payment_pending_today': Visit.objects.filter(created_at__date=today, status=VisitStatus.PAYMENT_PENDING).count(),
        'completed_today': Visit.objects.filter(created_at__date=today, status__in=[VisitStatus.REPORT_READY, VisitStatus.REPORT_DELIVERED]).count(),
    }
    
    context = {
        'visits': visits,
        'stats': stats,
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
    test_orders = visit.test_orders.all()
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
    ).select_related('visit', 'test').order_by('created_at')
    
    # Stats
    today = timezone.localdate()
    stats = {
        'pending': lab_queue.count(),
        'entered_today': TestOrder.objects.filter(result_entered_at__date=today).count()
    }
    
    context = {
        'queue': lab_queue,
        'stats': stats,
        **get_user_context(request)
    }
    return render(request, 'lab/dashboard.html', context)


@login_required
def lab_enter_results(request, order_id):
    if not in_group(request.user, ['lab']):
        return HttpResponseForbidden("Access Denied.")
        
    order = get_object_or_404(TestOrder, id=order_id)
    
    # Transition to testing status once opened in lab, if it was in sample_collected or retest_required
    if order.status in (TestOrderStatus.SAMPLE_COLLECTED, TestOrderStatus.RETEST_REQUIRED):
        try:
            transition_test_order_status(order, TestOrderStatus.TESTING, request.user, "Started result entry process in laboratory")
        except TransitionError as e:
            messages.error(request, f"Unable to update status: {e}")
            
    parameters = order.test.parameters
    
    if request.method == 'POST':
        results = {}
        for param in parameters:
            param_name = param['name']
            value = request.POST.get(f"param_{param_name}", "").strip()
            results[param_name] = value
            
        try:
            enter_test_result(order, results, request.user)
            messages.success(request, f"Results entered for {order.test.name} (Visit: {order.visit.visit_id})")
            return redirect('lab_dashboard')
        except TransitionError as e:
            messages.error(request, str(e))
            
    # For GET, load existing result values if available
    existing_results = order.result_value or {}
    
    # Render form with parameter inputs
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
        
    context = {
        'order': order,
        'param_fields': param_fields,
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
    ).select_related('visit', 'test').order_by('result_entered_at')
    
    stats = {
        'pending': review_queue.count(),
        'reviewed_today': TestOrder.objects.filter(
            reviewed_at__date=timezone.localdate(),
            status__in=[TestOrderStatus.REPORT_READY, TestOrderStatus.RETEST_REQUIRED, TestOrderStatus.RECOLLECTION_REQUIRED]
        ).count()
    }
    
    context = {
        'queue': review_queue,
        'stats': stats,
        **get_user_context(request)
    }
    return render(request, 'doctor/dashboard.html', context)


@login_required
def doctor_review(request, order_id):
    if not in_group(request.user, ['chamber']):
        return HttpResponseForbidden("Access Denied.")
        
    order = get_object_or_404(TestOrder, id=order_id)
    parameters = order.test.parameters
    results = order.result_value or {}
    
    if request.method == 'POST':
        action = request.POST.get('action')
        reason = request.POST.get('reason', '')
        
        try:
            if action == 'approve':
                # Transition: RESULT_ENTERED -> DOCTOR_REVIEWED -> REPORT_READY
                transition_test_order_status(order, TestOrderStatus.DOCTOR_REVIEWED, request.user, "Approved results")
                transition_test_order_status(order, TestOrderStatus.REPORT_READY, request.user, "Results marked report_ready")
                messages.success(request, f"Test {order.test.name} results approved successfully.")
                
            elif action == 'edit':
                # Perform edits
                updated_results = {}
                for param in parameters:
                    name = param['name']
                    updated_results[name] = request.POST.get(f"param_{name}", "").strip()
                    
                edit_test_result(order, updated_results, request.user, reason)
                # Auto-approve after edit
                transition_test_order_status(order, TestOrderStatus.DOCTOR_REVIEWED, request.user, f"Edited and Approved. Reason: {reason}")
                transition_test_order_status(order, TestOrderStatus.REPORT_READY, request.user, "Results marked report_ready")
                messages.success(request, f"Test results edited and approved.")
                
            elif action == 'retest':
                transition_test_order_status(order, TestOrderStatus.RETEST_REQUIRED, request.user, f"Retest requested. Reason: {reason}")
                messages.warning(request, f"Retest requested for {order.test.name}.")
                
            elif action == 'recollect':
                transition_test_order_status(order, TestOrderStatus.RECOLLECTION_REQUIRED, request.user, f"Recollection requested. Reason: {reason}")
                messages.warning(request, f"Recollection requested for {order.test.name}.")
                
            return redirect('doctor_dashboard')
            
        except TransitionError as e:
            messages.error(request, str(e))
            
    # Load parameter lines
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
            'abnormal': abnormal
        })
        
    context = {
        'order': order,
        'param_fields': param_fields,
        **get_user_context(request)
    }
    return render(request, 'doctor/review.html', context)
