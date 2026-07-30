"""PDF rendering for result exports.

Kept apart from the ViewSet because laying out a document is a different job from
deciding who may download one — the view owns the queryset and the permission
boundary, this module only turns rows into bytes.

CSV and Excel are data formats a spreadsheet reopens; a PDF is the one a student
prints or emails, so these are laid out to be read rather than parsed: a heading
that says whose results they are, a ruled table, and a summary line.
"""
from __future__ import annotations

import io
import logging
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

# Deep slate for headers — readable when printed in greyscale, which is how a
# result sheet usually ends up.
HEADER_BG = colors.HexColor('#1e293b')
ROW_ALT_BG = colors.HexColor('#f1f5f9')
BORDER = colors.HexColor('#cbd5e1')
MUTED = colors.HexColor('#475569')


def _styles():
    base = getSampleStyleSheet()
    return {
        'title': ParagraphStyle(
            'ResultTitle', parent=base['Heading1'], fontSize=16, spaceAfter=2,
        ),
        'subtitle': ParagraphStyle(
            'ResultSubtitle', parent=base['Normal'], fontSize=9, textColor=MUTED,
        ),
        'cell': ParagraphStyle(
            'ResultCell', parent=base['Normal'], fontSize=8, leading=10,
        ),
        'cellHeader': ParagraphStyle(
            'ResultCellHeader', parent=base['Normal'], fontSize=8, leading=10,
            textColor=colors.white,
        ),
        'note': ParagraphStyle(
            'ResultNote', parent=base['Normal'], fontSize=8, textColor=MUTED,
        ),
        'empty': ParagraphStyle(
            'ResultEmpty', parent=base['Normal'], fontSize=10, textColor=MUTED,
            alignment=TA_CENTER, spaceBefore=20,
        ),
        'schoolName': ParagraphStyle(
            'SchoolName', parent=base['Heading1'], fontSize=17, leading=20, spaceAfter=0,
        ),
        'schoolMeta': ParagraphStyle(
            'SchoolMeta', parent=base['Normal'], fontSize=9, textColor=MUTED, leading=12,
        ),
        'cardTitle': ParagraphStyle(
            'CardTitle', parent=base['Heading2'], fontSize=12, alignment=TA_CENTER,
            textColor=HEADER_BG, spaceBefore=2, spaceAfter=2,
        ),
        'fieldLabel': ParagraphStyle(
            'FieldLabel', parent=base['Normal'], fontSize=8, textColor=MUTED, leading=10,
        ),
        'fieldValue': ParagraphStyle(
            'FieldValue', parent=base['Normal'], fontSize=10, leading=13,
        ),
        'signature': ParagraphStyle(
            'Signature', parent=base['Normal'], fontSize=8, textColor=MUTED,
            alignment=TA_CENTER, leading=10,
        ),
        'generated': ParagraphStyle(
            'Generated', parent=base['Normal'], fontSize=7.5, textColor=MUTED,
            alignment=TA_RIGHT,
        ),
    }


def _table(header: list[str], rows: list[list[str]], styles) -> Table:
    """A ruled table whose cells wrap.

    Cells are Paragraphs rather than bare strings so a long test title wraps inside
    its column instead of running under the next one — test titles are free text and
    routinely longer than the space available.
    """
    data = [[Paragraph(str(h), styles['cellHeader']) for h in header]]
    data += [[Paragraph(str(cell), styles['cell']) for cell in row] for row in rows]

    table = Table(data, repeatRows=1, hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.4, BORDER),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        # Zebra striping starts on the first BODY row, so the header is not counted.
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, ROW_ALT_BG]),
    ]))
    return table


def _document(buffer: io.BytesIO, wide: bool) -> SimpleDocTemplate:
    """Portrait for a handful of columns, landscape once they stop fitting."""
    return SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4) if wide else A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title='Results',
    )


def build_result_detail_pdf(
    *,
    title: str,
    subtitle: str,
    summary: list[tuple[str, str]],
    header: list[str],
    rows: list[list[str]],
    note: str | None = None,
) -> bytes:
    """Render ONE result — the score summary plus its per-question breakdown."""
    styles = _styles()
    buffer = io.BytesIO()
    doc = _document(buffer, wide=False)

    summary_table = Table(
        [[Paragraph(f'<b>{label}</b>', styles['cell']), Paragraph(value, styles['cell'])]
         for label, value in summary],
        colWidths=[45 * mm, None],
        hAlign='LEFT',
    )
    summary_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LINEBELOW', (0, 0), (-1, -2), 0.3, BORDER),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))

    story = [
        Paragraph(title, styles['title']),
        Paragraph(subtitle, styles['subtitle']),
        Spacer(1, 10),
        summary_table,
        Spacer(1, 12),
    ]

    if rows:
        story.append(_table(header, rows, styles))
    if note:
        story.append(Spacer(1, 8))
        story.append(Paragraph(note, styles['note']))

    doc.build(story)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Report card
# ---------------------------------------------------------------------------

# Enough room for a school crest without pushing the school name off the header.
LOGO_MAX_W = 20 * mm
LOGO_MAX_H = 20 * mm


def _logo_flowable(logo_path: str | None):
    """The school crest, or None if there isn't a usable one.

    A missing, unreadable or non-filesystem logo must never cost a family their
    report card, so every failure here degrades to a card without a crest rather
    than a 500. Remote storage backends expose no local path at all, which is the
    normal production case and lands in the same fallback.
    """
    if not logo_path or not os.path.exists(logo_path):
        return None
    try:
        image = Image(logo_path)
        # Fit inside the box while preserving the aspect ratio — a squashed crest
        # looks worse than no crest.
        ratio = min(LOGO_MAX_W / image.imageWidth, LOGO_MAX_H / image.imageHeight)
        image.drawWidth = image.imageWidth * ratio
        image.drawHeight = image.imageHeight * ratio
        return image
    except Exception:
        logger.warning('Could not render school logo %s on a report card.', logo_path)
        return None


def _school_header(school: dict, styles) -> Table:
    """Crest on the left, school identity on the right, ruled off underneath."""
    identity = [
        Paragraph(school.get('name') or 'School', styles['schoolName']),
        Paragraph(f"School ID: <b>{school.get('code') or '--'}</b>", styles['schoolMeta']),
    ]
    address = school.get('address_line')
    if address:
        identity.append(Paragraph(address, styles['schoolMeta']))

    logo = _logo_flowable(school.get('logo_path'))
    if logo is None:
        rows = [[identity]]
        widths = [None]
    else:
        rows = [[logo, identity]]
        widths = [LOGO_MAX_W + 6 * mm, None]

    table = Table(rows, colWidths=widths, hAlign='LEFT')
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LINEBELOW', (0, 0), (-1, -1), 1.1, HEADER_BG),
    ]))
    return table


def _student_block(student: dict, styles) -> Table:
    """Who the card is for — label-over-value pairs, two per row."""
    def pair(label: str, value) -> list:
        return [
            Paragraph(label, styles['fieldLabel']),
            Paragraph(str(value or '--'), styles['fieldValue']),
        ]

    top = pair('Student Name', student.get('name')) + pair('Student ID', student.get('student_id'))
    bottom = pair('Class', student.get('class_name')) + pair('Section', student.get('section_name'))

    table = Table(
        [top, bottom],
        colWidths=[26 * mm, None, 22 * mm, 32 * mm],
        hAlign='LEFT',
    )
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    return table


def _marks_table(rows: list[dict], totals: dict, styles) -> Table:
    """Subject-wise marks with a bold totals row welded on the bottom.

    The totals sit inside the same table rather than below it so each figure lines
    up under its own column — a separate table would drift out of alignment the
    moment a long subject name widened a column.
    """
    header = ['S.No', 'Subject', 'Exam', 'Marks Obtained', 'Max Marks', 'Percentage', 'Result']
    data = [[Paragraph(h, styles['cellHeader']) for h in header]]

    for index, row in enumerate(rows, start=1):
        data.append([
            Paragraph(str(index), styles['cell']),
            Paragraph(row['subject'], styles['cell']),
            Paragraph(row['exam'], styles['cell']),
            Paragraph(row['obtained'], styles['cell']),
            Paragraph(row['total'], styles['cell']),
            Paragraph(row['percentage'], styles['cell']),
            Paragraph(row['result'], styles['cell']),
        ])

    data.append([
        Paragraph('', styles['cell']),
        Paragraph('<b>TOTAL</b>', styles['cell']),
        Paragraph('', styles['cell']),
        Paragraph('<b>%s</b>' % totals['obtained'], styles['cell']),
        Paragraph('<b>%s</b>' % totals['total'], styles['cell']),
        Paragraph('<b>%s</b>' % totals['percentage'], styles['cell']),
        Paragraph('<b>%s</b>' % totals['result'], styles['cell']),
    ])

    table = Table(
        data,
        colWidths=[12 * mm, None, None, 25 * mm, 21 * mm, 22 * mm, 17 * mm],
        repeatRows=1,
        hAlign='LEFT',
    )
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
        ('BACKGROUND', (0, -1), (-1, -1), ROW_ALT_BG),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (3, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.4, BORDER),
        ('LINEABOVE', (0, -1), (-1, -1), 0.9, HEADER_BG),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    return table


def _signature_block(teacher_name, principal_name, styles) -> Table:
    """Ruled signing lines.

    Both lines are drawn whether or not a name is known: the card is printed and
    signed by hand, so the RULE is the point and the printed name is a courtesy.
    """
    def cell(label: str, name) -> Paragraph:
        text = '%s<br/>%s' % (label, name) if name else label
        return Paragraph(text, styles['signature'])

    table = Table(
        [[cell('Teacher Signature', teacher_name),
          '',
          cell('Principal Signature', principal_name)]],
        colWidths=[55 * mm, None, 55 * mm],
        hAlign='LEFT',
    )
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
        ('TOPPADDING', (0, 0), (-1, -1), 22),
        ('LINEABOVE', (0, 0), (0, 0), 0.5, MUTED),
        ('LINEABOVE', (2, 0), (2, 0), 0.5, MUTED),
    ]))
    return table


def build_report_card_pdf(*, school: dict, cards: list[dict], generated_on: str) -> bytes:
    """Render one report card per student, each starting on its own page.

    `cards` is a list of ``{'student': {...}, 'rows': [...], 'totals': {...},
    'teacher_name': str | None}``. The student download passes a single card; the
    teacher's export passes one per student. The page break lives here rather than
    at either call site so both produce an identical document that differs only in
    how many pages it has — which is what "the teacher PDF must match the student
    PDF" actually requires.
    """
    styles = _styles()
    buffer = io.BytesIO()
    doc = _document(buffer, wide=False)

    story = []
    for index, card in enumerate(cards):
        if index:
            story.append(PageBreak())

        story.append(_school_header(school, styles))
        story.append(Spacer(1, 8))
        story.append(Paragraph('REPORT CARD', styles['cardTitle']))
        story.append(Spacer(1, 6))
        story.append(_student_block(card['student'], styles))
        story.append(Spacer(1, 10))
        story.append(_marks_table(card['rows'], card['totals'], styles))
        # Keep the signing lines with the date: a page containing nothing but two
        # blank rules reads as a printing fault, not as part of the card.
        story.append(KeepTogether([
            Spacer(1, 14),
            _signature_block(card.get('teacher_name'), school.get('principal_name'), styles),
            Spacer(1, 8),
            Paragraph('Report generated on %s' % generated_on, styles['generated']),
        ]))

    if not cards:
        story.append(_school_header(school, styles))
        story.append(Paragraph('No published results to report.', styles['empty']))

    doc.build(story)
    return buffer.getvalue()
