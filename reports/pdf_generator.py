"""
PDF report generator using ReportLab.

Generates a professional lab report PDF with:
- Lab header/branding
- Patient info
- Test results table with reference ranges
- Generated timestamp
"""
import io
from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT


def generate_report_pdf(visit):
    """
    Generate a PDF report for the given visit.
    Returns a BytesIO buffer containing the PDF.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=1.5 * cm, bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    styles.add(ParagraphStyle(
        'LabHeader', parent=styles['Title'],
        fontSize=18, textColor=colors.HexColor('#1a237e'),
        spaceAfter=2 * mm,
    ))
    styles.add(ParagraphStyle(
        'LabSubHeader', parent=styles['Normal'],
        fontSize=9, textColor=colors.HexColor('#616161'),
        alignment=TA_CENTER, spaceAfter=4 * mm,
    ))
    styles.add(ParagraphStyle(
        'SectionHeader', parent=styles['Heading2'],
        fontSize=12, textColor=colors.HexColor('#1a237e'),
        spaceAfter=3 * mm, spaceBefore=6 * mm,
    ))
    styles.add(ParagraphStyle(
        'InfoLabel', parent=styles['Normal'],
        fontSize=9, textColor=colors.HexColor('#757575'),
    ))
    styles.add(ParagraphStyle(
        'InfoValue', parent=styles['Normal'],
        fontSize=10, textColor=colors.HexColor('#212121'),
    ))

    elements = []

    # ── Lab Header ─────────────────────────────────────────────────
    lab_name = getattr(settings, 'LAB_NAME', 'LabFlow Diagnostics')
    lab_address = getattr(settings, 'LAB_ADDRESS', '')
    lab_phone = getattr(settings, 'LAB_PHONE', '')

    elements.append(Paragraph(lab_name, styles['LabHeader']))
    if lab_address or lab_phone:
        sub_parts = [p for p in [lab_address, lab_phone] if p]
        elements.append(Paragraph(' | '.join(sub_parts), styles['LabSubHeader']))

    elements.append(HRFlowable(
        width='100%', thickness=1.5,
        color=colors.HexColor('#1a237e'), spaceAfter=4 * mm,
    ))

    # ── Patient Info ───────────────────────────────────────────────
    elements.append(Paragraph('Patient Information', styles['SectionHeader']))

    patient_data = [
        ['Visit ID:', visit.visit_id, 'Date:', visit.created_at.strftime('%d %b %Y, %I:%M %p')],
        ['Name:', visit.patient_name, 'Age / Gender:', f'{visit.age} / {visit.get_gender_display()}'],
        ['Phone:', visit.phone, 'Referred By:', visit.referred_by or '—'],
    ]

    patient_table = Table(patient_data, colWidths=[70, 170, 80, 170])
    patient_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#757575')),
        ('TEXTCOLOR', (2, 0), (2, -1), colors.HexColor('#757575')),
        ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#212121')),
        ('TEXTCOLOR', (3, 0), (3, -1), colors.HexColor('#212121')),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica-Bold'),
        ('FONTNAME', (3, 0), (3, -1), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(patient_table)
    elements.append(Spacer(1, 4 * mm))

    # ── Test Results ───────────────────────────────────────────────
    elements.append(HRFlowable(
        width='100%', thickness=0.5,
        color=colors.HexColor('#bdbdbd'), spaceAfter=2 * mm,
    ))
    elements.append(Paragraph('Test Results', styles['SectionHeader']))

    test_orders = visit.test_orders.select_related('test').filter(
        status__in=['doctor_reviewed', 'report_ready']
    ).exclude(
        status='cancelled'
    ).order_by('display_order', 'created_at')

    for order in test_orders:
        # Test name header
        elements.append(Paragraph(
            f'<b>{order.test.name}</b> ({order.test.short_code})',
            styles['Normal']
        ))
        elements.append(Spacer(1, 2 * mm))

        # Build results table
        header = ['Parameter', 'Result', 'Unit', 'Reference Range']
        rows = [header]

        parameters = order.test.parameters or []
        results = order.result_value or {}

        for param in parameters:
            param_name = param.get('name', '')
            result_val = results.get(param_name, '—')
            unit = param.get('unit', '')
            ref_min = param.get('ref_min', '')
            ref_max = param.get('ref_max', '')

            if ref_min and ref_max:
                ref_range = f'{ref_min} – {ref_max}'
            elif ref_min:
                ref_range = f'> {ref_min}'
            elif ref_max:
                ref_range = f'< {ref_max}'
            else:
                ref_range = '—'

            rows.append([param_name, str(result_val), unit, ref_range])

        if len(rows) > 1:
            result_table = Table(rows, colWidths=[160, 100, 80, 120])
            result_table.setStyle(TableStyle([
                # Header
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e8eaf6')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1a237e')),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                # Body
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#212121')),
                # Grid
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e0e0e0')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fafafa')]),
                # Padding
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ]))
            elements.append(result_table)

        elements.append(Spacer(1, 4 * mm))

    # ── Footer ─────────────────────────────────────────────────────
    elements.append(Spacer(1, 10 * mm))
    elements.append(HRFlowable(
        width='100%', thickness=0.5,
        color=colors.HexColor('#bdbdbd'), spaceAfter=4 * mm,
    ))

    from django.utils import timezone
    generated_at = timezone.now().strftime('%d %b %Y, %I:%M %p')
    elements.append(Paragraph(
        f'<i>Report generated on {generated_at} | {lab_name}</i>',
        ParagraphStyle('Footer', parent=styles['Normal'],
                       fontSize=8, textColor=colors.HexColor('#9e9e9e'),
                       alignment=TA_CENTER),
    ))
    elements.append(Paragraph(
        '<i>This is a computer-generated report.</i>',
        ParagraphStyle('Footer2', parent=styles['Normal'],
                       fontSize=7, textColor=colors.HexColor('#bdbdbd'),
                       alignment=TA_CENTER),
    ))

    doc.build(elements)
    buffer.seek(0)
    return buffer
