"""
Public report download view — token-gated, no login required.
This is the endpoint linked in the SMS sent to patients.
"""
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404

from core.models import Visit
from .pdf_generator import generate_report_pdf


def download_report(request, token):
    """
    GET /report/<token>/
    Validates the token, generates PDF, returns as download.
    """
    visit = get_object_or_404(Visit, report_token=token)

    if not visit.is_report_token_valid():
        raise Http404('This report link has expired. Please contact the lab for a new link.')

    # Generate PDF
    pdf_buffer = generate_report_pdf(visit)

    response = HttpResponse(pdf_buffer.read(), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="report_{visit.visit_id}.pdf"'
    return response
