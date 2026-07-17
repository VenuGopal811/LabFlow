from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    # Auth
    path('', auth_views.LoginView.as_view(template_name='login.html', redirect_authenticated_user=True), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path('dashboard/', views.dashboard_redirect, name='dashboard_redirect'),

    # Reception
    path('reception/', views.reception_dashboard, name='reception_dashboard'),
    path('reception/register/', views.reception_register, name='reception_register'),
    path('visit/<int:visit_id>/', views.visit_detail, name='visit_detail'),
    path('visit/<int:visit_id>/bill/', views.print_bill, name='print_bill'),
    path('visit/<int:visit_id>/pay-pending/', views.transition_to_payment_pending, name='transition_to_payment_pending'),
    path('visit/<int:visit_id>/send-sms/', views.send_report_sms, name='send_report_sms'),
    path('test-order/cancel/', views.cancel_test_order, name='cancel_test_order'),

    # Chamber (Approvals / Billing)
    path('chamber/', views.chamber_dashboard, name='chamber_dashboard'),
    path('chamber/confirm-payment/<int:visit_id>/', views.chamber_confirm_payment, name='chamber_confirm_payment'),
    path('chamber/approve/<int:visit_id>/', views.chamber_approve_visit, name='chamber_approve_visit'),

    # Collection
    path('collection/', views.collection_dashboard, name='collection_dashboard'),
    path('collection/collect/<int:visit_id>/', views.collection_collect, name='collection_collect'),

    # Lab Tech
    path('lab/', views.lab_dashboard, name='lab_dashboard'),
    path('lab/results/<int:order_id>/', views.lab_enter_results, name='lab_enter_results'),
    path('lab/visit/<int:visit_id>/results/', views.lab_enter_results_visit, name='lab_enter_results_visit'),

    # Doctor Review (Pathologist Approval)
    path('doctor/', views.doctor_dashboard, name='doctor_dashboard'),
    path('doctor/review/<int:order_id>/', views.doctor_review, name='doctor_review'),
    path('doctor/visit/<int:visit_id>/review/', views.doctor_review_visit, name='doctor_review_visit'),

    # Reporting
    path('reporting/', views.reporting_dashboard, name='reporting_dashboard'),
    path('reporting/detail/<int:visit_id>/', views.reporting_detail, name='reporting_detail'),
]
