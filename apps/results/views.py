"""Result API — read-only access with publish controls for teachers/admins.

Permission model
- Student: sees own results where is_published=True.
- Teacher / School Admin: sees results within own school; can publish.
- CSC Admin: full read access + publish across all schools.
"""
from __future__ import annotations

import csv
import logging

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.text import slugify
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.audit.models import AuditLog
from apps.audit.services import log_action
from apps.exams.models import ExamSession
from apps.notifications.models import Notification

from .models import Result
from .pdf import build_report_card_pdf, build_result_detail_pdf
from .services import rerank_assignment
from .serializers import (
    PublishResultSerializer,
    ResultDetailSerializer,
    ResultListSerializer,
)

logger = logging.getLogger(__name__)


class ResultViewSet(viewsets.ReadOnlyModelViewSet):
    """
    list     — GET  /results/
    retrieve — GET  /results/{id}/
    publish  — POST /results/{id}/publish/
    publish_bulk — POST /results/publish-bulk/
    """

    queryset = Result.objects.select_related(
        'student',
        'student__student_profile__school_class',
        'student__student_profile__section',
        'test__subject',
        'assignment',
        'session',
    ).all()
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['test', 'assignment', 'student', 'is_published', 'passed']
    search_fields = ['student__full_name', 'student__email', 'test__title', 'student__student_id']
    ordering_fields = ['percentage', 'obtained_marks', 'rank', 'calculated_at']
    ordering = ['-calculated_at']

    # ------------------------------------------------------------------
    # Queryset scoping
    # ------------------------------------------------------------------

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Result.objects.none()

        qs = self.queryset

        if user.role == 'csc_admin':
            school_id = self.request.query_params.get('school_id')
            if school_id:
                qs = qs.filter(test__school_id=school_id)
        elif user.role == 'student':
            # Students only see their own *published* results
            qs = qs.filter(student=user, is_published=True)
        elif user.school_id:
            # Teacher / School Admin — own school only
            qs = qs.filter(test__school_id=user.school_id)
        else:
            return Result.objects.none()

        # Extra history filters (published-results dashboard + export)
        params = self.request.query_params
        if params.get('subject'):
            qs = qs.filter(test__subject_id=params['subject'])
        if params.get('school_class'):
            qs = qs.filter(student__student_profile__school_class_id=params['school_class'])
        if params.get('section'):
            qs = qs.filter(student__student_profile__section_id=params['section'])
        if params.get('date_from'):
            qs = qs.filter(published_at__date__gte=params['date_from'])
        if params.get('date_to'):
            qs = qs.filter(published_at__date__lte=params['date_to'])

        return qs

    def filter_queryset(self, queryset):
        search_type = self.request.query_params.get('search_type')
        if search_type == 'student_name':
            self.search_fields = ['student__full_name', 'student__email']
        elif search_type == 'chapter_name':
            self.search_fields = ['test__title']
        elif search_type == 'student_id':
            self.search_fields = ['student__student_id']
        else:
            self.search_fields = ['student__full_name', 'student__email', 'test__title', 'student__student_id']
        return super().filter_queryset(queryset)

    def get_serializer_class(self):
        if self.action == 'list':
            return ResultListSerializer
        if self.action == 'publish':
            return PublishResultSerializer
        return ResultDetailSerializer

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        # Prefetch details for the detail view
        instance = (
            Result.objects
            .select_related('student', 'test', 'assignment', 'session')
            .prefetch_related('details__question')
            .get(pk=instance.pk)
        )
        # Context matters: the serializer withholds the per-question breakdown from a
        # student when the test has allow_review_after_submit off, and needs the request
        # to know who is asking.
        serializer = ResultDetailSerializer(instance, context={'request': request})
        return Response(serializer.data)

    # ------------------------------------------------------------------
    # Publish single
    # ------------------------------------------------------------------

    @action(detail=True, methods=['post'], url_path='publish')
    def publish(self, request, pk=None):
        """Toggle is_published on a single result.

        Lifecycle: a submitted session is auto-evaluated on publish (MCQ marks
        are already auto-scored; evaluation is the teacher's review-and-confirm
        step, and publishing IS that confirmation). The session then moves
        evaluated -> published. Unpublishing steps back to evaluated.
        """
        result = self.get_object()
        self._require_publisher(request, result)

        serializer = PublishResultSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        publish_value = serializer.validated_data['is_published']

        session = result.session
        with transaction.atomic():
            if publish_value:
                if session.status == ExamSession.Status.SUBMITTED:
                    # Implicit evaluation (publish confirms the auto-scoring)
                    session.evaluated_at = timezone.now()
                    session.evaluated_by = request.user
                    session.transition_to(
                        ExamSession.Status.EVALUATED,
                        extra_update_fields=['evaluated_at', 'evaluated_by'],
                    )
                if session.status == ExamSession.Status.EVALUATED:
                    session.transition_to(ExamSession.Status.PUBLISHED)
                elif session.status != ExamSession.Status.PUBLISHED:
                    raise ValidationError(
                        {'detail': f'Cannot publish a session in status "{session.status}".'}
                    )
            else:
                if session.status == ExamSession.Status.PUBLISHED:
                    session.transition_to(ExamSession.Status.EVALUATED)

            # Result.is_published stays the single authority for student visibility.
            result.is_published = publish_value
            result.published_at = timezone.now() if publish_value else None
            result.save(update_fields=['is_published', 'published_at'])

            # Publishing is the moment the ranks are actually read — by the
            # teacher on the class list and by the student on their Rank card.
            # Ranks are maintained on submit and on evaluate, so this normally
            # changes nothing; it is here so that a cohort edited by any route
            # that did not re-rank still resolves before anyone sees it.
            rerank_assignment(result.assignment_id)

        if publish_value:
            self._notify_students_published([result])

        log_action(
            request.user,
            AuditLog.Action.RESULT_PUBLISHED,
            entity_type='Result',
            entity_id=result.id,
            request=request,
            details={
                'is_published': publish_value,
                'test_id': result.test_id,
                'student_id': result.student_id,
            },
        )

        return Response({
            'detail': f'Result {"published" if publish_value else "unpublished"}.',
            'result_id': result.id,
            'is_published': result.is_published,
        })

    # ------------------------------------------------------------------
    # Publish bulk
    # ------------------------------------------------------------------

    @action(detail=False, methods=['post'], url_path='publish-bulk')
    def publish_bulk(self, request):
        """Publish all results for a given assignment.

        Submitted sessions are auto-evaluated first (publish = review-confirm),
        then everything moves to published and students are notified.
        """
        assignment_id = request.data.get('assignment_id')
        if not assignment_id:
            raise ValidationError({'assignment_id': 'This field is required.'})

        # Build queryset scoped to user's school (or all for CSC Admin)
        qs = Result.objects.filter(assignment_id=assignment_id).select_related('session', 'test__subject')
        if request.user.role == 'teacher':
            qs = qs.filter(test__created_by=request.user)
        elif request.user.role != 'csc_admin':
            # School Admin has read-only monitoring access.
            raise PermissionDenied('Only the test\'s teacher or CSC Admin can publish results.')

        now = timezone.now()
        to_publish = list(qs.filter(is_published=False))

        with transaction.atomic():
            # Lifecycle: submitted -> evaluated (implicit) -> published
            sessions = [r.session for r in to_publish]
            submitted = [s for s in sessions if s.status == ExamSession.Status.SUBMITTED]
            for s in submitted:
                s.status = ExamSession.Status.EVALUATED
                s.evaluated_at = now
                s.evaluated_by = request.user
            if submitted:
                ExamSession.objects.bulk_update(
                    submitted, ['status', 'evaluated_at', 'evaluated_by'], batch_size=500,
                )
            evaluated = [s for s in sessions if s.status == ExamSession.Status.EVALUATED]
            for s in evaluated:
                s.status = ExamSession.Status.PUBLISHED
            if evaluated:
                ExamSession.objects.bulk_update(evaluated, ['status'], batch_size=500)

            updated_count = Result.objects.filter(
                pk__in=[r.pk for r in to_publish],
            ).update(is_published=True, published_at=now)

            # Settle the ranks before anyone reads them. Unlike the single-result
            # publish this covers the whole cohort in one pass, which is the case
            # that matters: "Publish All" is where a teacher first sees the
            # ranking as a ladder rather than one row at a time.
            rerank_assignment(assignment_id)

        if to_publish:
            self._notify_students_published(to_publish)

        log_action(
            request.user,
            AuditLog.Action.RESULT_PUBLISHED,
            entity_type='Result',
            request=request,
            details={
                'assignment_id': int(assignment_id),
                'bulk': True,
                'count': updated_count,
            },
        )

        return Response({
            'detail': f'{updated_count} result(s) published.',
            'assignment_id': int(assignment_id),
            'published_count': updated_count,
        })

    # ------------------------------------------------------------------
    # Export (CSV / Excel)
    # ------------------------------------------------------------------

    @action(detail=False, methods=['get'], url_path='export')
    def export(self, request):
        """Export the (filtered) result list as CSV, Excel or PDF.

        GET /results/export/?file_format=csv|excel|pdf&test=&assignment=&is_published=...
        ('file_format', not 'format' — DRF reserves 'format' for renderer negotiation.)

        Read-only, and scoped by ``get_queryset`` exactly like the list is: a student
        reaches only their OWN published results, so no extra permission check is
        needed to let them download their history — the queryset is the boundary.
        """
        fmt = request.query_params.get('file_format', 'csv').lower()
        if fmt not in ('csv', 'excel', 'xlsx', 'pdf'):
            raise ValidationError({'file_format': 'file_format must be csv, excel or pdf.'})

        qs = self.filter_queryset(self.get_queryset()).select_related(
            'test__subject',
            'student__student_profile__school_class',
            'student__student_profile__section',
        )

        # A student downloading their own history does not need their own name and
        # class repeated on every row, and must not be handed a Rank column — rank
        # was deliberately removed from every student-facing view. Staff exports keep
        # the wider column set unchanged so existing reports are unaffected.
        is_student = request.user.role == 'student'

        if is_student:
            header = [
                'Subject', 'Test', 'Obtained Marks', 'Total Marks',
                'Percentage', 'Result', 'Submitted At',
            ]
        else:
            header = [
                'Student', 'Email', 'Class', 'Section', 'Subject', 'Test',
                'Obtained Marks', 'Total Marks', 'Percentage', 'Result',
                'Rank', 'Published', 'Published At', 'Submitted At',
            ]

        def submitted(r: Result) -> str:
            when = r.session.submitted_at
            return when.strftime('%Y-%m-%d %H:%M') if when else ''

        def row_for(r: Result) -> list:
            if is_student:
                return [
                    r.test.subject.name,
                    r.test.title,
                    float(r.obtained_marks),
                    float(r.total_marks),
                    float(r.percentage),
                    'Pass' if r.passed else 'Fail',
                    submitted(r),
                ]
            try:
                profile = r.student.student_profile
                class_name = profile.school_class.name if profile.school_class_id else ''
                section_name = profile.section.name if profile.section_id else ''
            except Exception:
                class_name = section_name = ''
            return [
                r.student.full_name,
                r.student.email or '',  # Students may have no email on file.
                class_name,
                section_name,
                r.test.subject.name,
                r.test.title,
                float(r.obtained_marks),
                float(r.total_marks),
                float(r.percentage),
                'Pass' if r.passed else 'Fail',
                r.rank or '',
                'Yes' if r.is_published else 'No',
                r.published_at.strftime('%Y-%m-%d %H:%M') if r.published_at else '',
                submitted(r),
            ]

        timestamp = timezone.now().strftime('%Y%m%d_%H%M')

        if fmt == 'csv':
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="results_{timestamp}.csv"'
            writer = csv.writer(response)
            writer.writerow(header)
            for r in qs.iterator(chunk_size=500):
                writer.writerow(row_for(r))
        elif fmt == 'pdf':
            # PDF is the printable REPORT CARD — a different document from the CSV and
            # Excel exports, which stay as flat data grids. Both roles get the same
            # layout; the teacher's copy simply carries one card per student.
            #
            # The queryset is materialised because a card needs its totals before it
            # can be drawn. Bounded in practice: a student's history is one row per
            # test sat, and a staff export is scoped by the filters on screen.
            response = HttpResponse(content_type='application/pdf')
            response['Content-Disposition'] = (
                f'attachment; filename="report_card_{timestamp}.pdf"'
            )
            response.write(self._build_report_cards(request, list(qs)))
        else:
            import openpyxl

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = 'Results'
            ws.append(header)
            for r in qs.iterator(chunk_size=500):
                ws.append(row_for(r))
            response = HttpResponse(
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            )
            response['Content-Disposition'] = f'attachment; filename="results_{timestamp}.xlsx"'
            wb.save(response)

        log_action(
            request.user,
            AuditLog.Action.DATA_EXPORT,
            entity_type='Result',
            request=request,
            details={'format': fmt, 'filters': dict(request.query_params)},
        )
        return response

    @action(detail=True, methods=['get'], url_path='export')
    def export_one(self, request, pk=None):
        """Export ONE result — the score summary plus its per-question breakdown.

        GET /results/{id}/export/?file_format=pdf|csv|excel

        `get_object` runs through `get_queryset`, so a student can only ever reach
        their own published result. The breakdown obeys `allow_review_after_submit`
        the same way the detail endpoint does: a student barred from reviewing their
        answers on screen must not be handed them in a file instead.
        """
        result = self.get_object()

        fmt = request.query_params.get('file_format', 'pdf').lower()
        if fmt not in ('csv', 'excel', 'xlsx', 'pdf'):
            raise ValidationError({'file_format': 'file_format must be csv, excel or pdf.'})

        is_student = request.user.role == 'student'
        show_breakdown = not is_student or result.test.allow_review_after_submit

        header = ['#', 'Question', 'Your Answer', 'Correct Answer', 'Outcome', 'Marks']
        rows: list[list[str]] = []
        if show_breakdown:
            details = result.details.select_related('question').all()
            for index, d in enumerate(details, start=1):
                rows.append([
                    str(index),
                    d.question.question_text,
                    (d.selected_option or '--').upper(),
                    d.correct_option.upper(),
                    'Correct' if d.is_correct else (
                        'Not answered' if d.selected_option is None else 'Wrong'
                    ),
                    f'{float(d.marks_obtained):g}',
                ])

        summary: list[tuple[str, str]] = [
            ('Student', result.student.full_name),
            ('Test', result.test.title),
            ('Subject', result.test.subject.name),
            ('Marks', f'{float(result.obtained_marks):g} / {float(result.total_marks):g}'),
            ('Percentage', f'{float(result.percentage):g}%'),
            ('Result', 'Pass' if result.passed else 'Fail'),
            ('Correct', str(result.correct_count)),
            ('Wrong', str(result.wrong_count)),
            ('Unattempted', str(result.unattempted_count)),
            (
                'Submitted',
                result.session.submitted_at.strftime('%d %b %Y, %H:%M')
                if result.session.submitted_at else '--',
            ),
        ]
        # Rank is deliberately absent for students — it was removed from every
        # student-facing view, and a download is a student-facing view.
        if not is_student and result.rank:
            summary.append(('Rank', str(result.rank)))

        timestamp = timezone.now().strftime('%Y%m%d_%H%M')
        slug = slugify(result.test.title) or 'result'

        if fmt == 'pdf':
            note = None if show_breakdown else (
                'The per-question review is not enabled for this test, '
                'so only the score summary is shown.'
            )
            response = HttpResponse(content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="{slug}_{timestamp}.pdf"'
            response.write(
                build_result_detail_pdf(
                    title=result.test.title,
                    subtitle=f'{result.test.subject.name} — {result.student.full_name}',
                    summary=summary,
                    header=header,
                    rows=rows,
                    note=note,
                )
            )
        elif fmt == 'csv':
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="{slug}_{timestamp}.csv"'
            writer = csv.writer(response)
            # The summary rides above the table as label/value pairs so one file
            # carries the whole result, rather than a bare grid of answers.
            for label, value in summary:
                writer.writerow([label, value])
            if rows:
                writer.writerow([])
                writer.writerow(header)
                writer.writerows(rows)
        else:
            import openpyxl

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = 'Result'
            for label, value in summary:
                ws.append([label, value])
            if rows:
                ws.append([])
                ws.append(header)
                for row in rows:
                    ws.append(row)
            response = HttpResponse(
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            )
            response['Content-Disposition'] = f'attachment; filename="{slug}_{timestamp}.xlsx"'
            wb.save(response)

        log_action(
            request.user,
            AuditLog.Action.DATA_EXPORT,
            entity_type='Result',
            entity_id=result.pk,
            request=request,
            details={'format': fmt, 'result_id': result.pk},
        )
        return response

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_teacher_or_above(request) -> None:
        if request.user.role not in ('csc_admin', 'school_admin', 'teacher'):
            raise PermissionDenied('Only teachers or admins can perform this action.')

    @staticmethod
    def _school_for(request, rows: list[Result]):
        """The school whose identity heads the report card.

        Taken from the requesting user when they belong to one — a teacher's export
        is always within their own school. CSC Admin has no school of their own, so
        it falls back to the school of the results being printed. Returns None only
        when there is genuinely nothing to go on.
        """
        school = getattr(request.user, 'school', None)
        if school is not None:
            return school
        for result in rows:
            profile = getattr(result.student, 'student_profile', None)
            if profile is not None and profile.school_id:
                return profile.school
        return None

    def _build_report_cards(self, request, rows: list[Result]) -> bytes:
        """Group results by student and render one report card each.

        Grouping is what turns a flat result list into report cards: a student's row
        per subject belongs on THEIR card, not in a shared table. Insertion order is
        preserved (dict ordering), so the cards follow the list's own ordering rather
        than an arbitrary one.
        """
        school = self._school_for(request, rows)
        school_ctx = {
            # `code` IS the School ID — the human one (e.g. KAR_001), never the PK.
            'name': school.name if school else '',
            'code': school.code if school else '',
            'principal_name': (school.principal_name or None) if school else None,
            'address_line': ', '.join(
                part for part in (
                    school.city if school else '',
                    school.state if school else '',
                ) if part
            ) or None,
            'logo_path': self._logo_path(school),
        }

        grouped: dict[int, list[Result]] = {}
        for result in rows:
            grouped.setdefault(result.student_id, []).append(result)

        cards = []
        for student_results in grouped.values():
            cards.append(self._card_for(student_results))

        return build_report_card_pdf(
            school=school_ctx,
            cards=cards,
            generated_on=timezone.localtime().strftime('%d %b %Y, %H:%M'),
        )

    @staticmethod
    def _logo_path(school) -> str | None:
        """Local filesystem path of the school crest, if there is one.

        `.path` raises on storage backends without local files (S3 in production),
        so the failure is swallowed — a report card without a crest is fine, a
        download that 500s is not.
        """
        if school is None or not school.logo:
            return None
        try:
            return school.logo.path
        except Exception:
            return None

    @staticmethod
    def _card_for(student_results: list[Result]) -> dict:
        """One student's card: their subject rows plus the aggregate underneath."""
        student = student_results[0].student
        profile = getattr(student, 'student_profile', None)

        rows = []
        obtained_sum = total_sum = 0.0
        for result in student_results:
            obtained = float(result.obtained_marks)
            total = float(result.total_marks)
            obtained_sum += obtained
            total_sum += total
            rows.append({
                'subject': result.test.subject.name,
                'exam': result.test.title,
                'obtained': f'{obtained:g}',
                'total': f'{total:g}',
                'percentage': f'{float(result.percentage):g}%',
                'result': 'Pass' if result.passed else 'Fail',
            })

        overall_pct = (obtained_sum / total_sum * 100) if total_sum else 0.0
        # Overall Pass means every exam on the card was passed. Deriving it from the
        # aggregate percentage instead would let a strong subject mask a failed one,
        # which is not what a report card is asserting.
        all_passed = all(r.passed for r in student_results)

        # A single teacher can be named on the signature line; several cannot, and a
        # card spanning subjects taught by different teachers leaves it blank to sign.
        teachers = {
            r.test.created_by.full_name for r in student_results if r.test.created_by_id
        }
        teacher_name = teachers.pop() if len(teachers) == 1 else None

        return {
            'student': {
                'name': student.full_name,
                'student_id': student.student_id,
                'class_name': profile.school_class.name if profile else None,
                'section_name': profile.section.name if profile else None,
            },
            'rows': rows,
            'totals': {
                'obtained': f'{obtained_sum:g}',
                'total': f'{total_sum:g}',
                'percentage': f'{overall_pct:.1f}%',
                'result': 'Pass' if all_passed else 'Fail',
            },
            'teacher_name': teacher_name,
        }

    @staticmethod
    def _require_publisher(request, result: Result) -> None:
        """Publishing is restricted to the test's creator (teacher) or CSC Admin.

        School Admins keep read-only monitoring access.
        """
        user = request.user
        if user.role == 'csc_admin':
            return
        if user.role == 'teacher' and result.test.created_by_id == user.id:
            return
        raise PermissionDenied('Only the test\'s teacher or CSC Admin can publish results.')

    @staticmethod
    def _notify_students_published(results: list[Result]) -> None:
        """One 'Result Published' notification per student, in a single bulk insert."""
        try:
            notifications = [
                Notification(
                    user_id=r.student_id,
                    type=Notification.Type.RESULT_PUBLISHED,
                    title='Result Published',
                    message=(
                        f'Your {r.test.subject.name} - {r.test.title} exam result '
                        f'has been published.'
                    ),
                    data={'result_id': r.pk, 'test_id': r.test_id},
                )
                for r in results
            ]
            Notification.objects.bulk_create(notifications, batch_size=500)
        except Exception:
            logger.exception('Failed to create result-published notifications.')
