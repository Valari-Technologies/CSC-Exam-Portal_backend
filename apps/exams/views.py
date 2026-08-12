"""Exam session API — start, save answers, submit, cheat-event logging.

Permission model
- Student: start, save, submit, log cheat events for own sessions.
- Teacher / School Admin: list/retrieve sessions within own school.
- CSC Admin: full read access across all schools.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.audit.models import AuditLog
from apps.audit.services import log_action
from apps.notifications.models import Notification
from apps.notifications.services import notify_user
from apps.results.models import Result, ResultDetail
from apps.results.services import rerank_assignment
from apps.students.models import StudentProfile
from apps.tests.models import TestQuestion

from .models import AntiCheatLog, ExamAnswer, ExamSession
from .serializers import (
    AntiCheatEventSerializer,
    ExamSessionDetailSerializer,
    ExamSessionListSerializer,
    SaveAnswerSerializer,
    StartExamSerializer,
    SubmitExamSerializer,
)

logger = logging.getLogger(__name__)


class ExamSessionViewSet(viewsets.GenericViewSet):
    """
    Exam session endpoints.

    list       — GET  /exam/sessions/
    retrieve   — GET  /exam/sessions/{id}/
    start      — POST /exam/sessions/start/
    save_answer— POST /exam/sessions/{id}/save-answer/
    submit     — POST /exam/sessions/{id}/submit/
    cheat_event— POST /exam/sessions/{id}/cheat-event/
    """

    queryset = ExamSession.objects.select_related(
        'student',
        'student__student_profile__school_class',
        'student__student_profile__section',
        'test__subject',
        'assignment',
        'result',
    ).all()
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'test', 'assignment']
    search_fields = ['student__full_name', 'student__email', 'test__title', 'student__student_id']
    ordering_fields = ['started_at', 'submitted_at']
    ordering = ['-started_at']

    # ------------------------------------------------------------------
    # Queryset scoping
    # ------------------------------------------------------------------

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return ExamSession.objects.none()

        params = self.request.query_params
        qs = self.queryset

        if user.role == 'csc_admin':
            # optional school filter via query param
            school_id = self.request.query_params.get('school_id')
            if school_id:
                qs = qs.filter(test__school_id=school_id)
        elif user.role == 'student':
            qs = qs.filter(student=user)
        elif user.school_id:
            # teacher / school_admin — own school only
            qs = qs.filter(test__school_id=user.school_id)
            if user.role == 'teacher' and params.get('class_scope') == 'assigned':
                from django.db.models import Q
                from apps.teachers.models import TeacherAssignment
                assignments = TeacherAssignment.objects.filter(teacher__user=user)
                if not assignments.exists():
                    return ExamSession.objects.none()
                
                q_objects = Q()
                for assignment in assignments:
                    if assignment.section_id:
                        q_objects |= Q(
                            student__student_profile__school_class_id=assignment.school_class_id,
                            student__student_profile__section_id=assignment.section_id
                        )
                    else:
                        q_objects |= Q(
                            student__student_profile__school_class_id=assignment.school_class_id
                        )
                qs = qs.filter(q_objects)
        else:
            return ExamSession.objects.none()

        # Extra dashboard filters (evaluation dashboard)
        if params.get('status_in'):
            qs = qs.filter(status__in=params['status_in'].split(','))
        if params.get('subject'):
            qs = qs.filter(test__subject_id=params['subject'])
        if params.get('school_class'):
            qs = qs.filter(student__student_profile__school_class_id=params['school_class'])
        if params.get('section'):
            qs = qs.filter(student__student_profile__section_id=params['section'])
        if params.get('date_from'):
            qs = qs.filter(submitted_at__date__gte=params['date_from'])
        if params.get('date_to'):
            qs = qs.filter(submitted_at__date__lte=params['date_to'])

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
            return ExamSessionListSerializer
        if self.action == 'start':
            return StartExamSerializer
        if self.action == 'save_answer':
            return SaveAnswerSerializer
        if self.action == 'submit':
            return SubmitExamSerializer
        if self.action == 'cheat_event':
            return AntiCheatEventSerializer
        return ExamSessionDetailSerializer

    # ------------------------------------------------------------------
    # Server-authoritative timer
    # ------------------------------------------------------------------

    def _sync_server_timer(self, session: ExamSession, request) -> bool:
        """Recompute remaining time from the wall clock and persist it.

        The timer is anchored to ``started_at`` — refreshing the page, closing
        the browser, or re-logging never resets or pauses it. If time has run
        out the session is auto-submitted. Returns True if it auto-submitted.

        Only the test's own duration, counted from ``started_at``, determines the countdown timer.
        The assignment's ``end_datetime`` controls availability for starting, but not the duration.
        """
        if session.status != ExamSession.Status.IN_PROGRESS:
            return False

        now = timezone.now()
        elapsed = (now - session.started_at).total_seconds()
        remaining = max(0, session.test.duration_minutes * 60 - int(elapsed))

        if remaining <= 0:
            session.time_remaining_seconds = 0
            session.save(update_fields=['time_remaining_seconds'])
            self._perform_submit(session, request, auto=True)
            return True

        if remaining != session.time_remaining_seconds:
            session.time_remaining_seconds = remaining
            session.save(update_fields=['time_remaining_seconds'])
        return False

    # ------------------------------------------------------------------
    # list / retrieve
    # ------------------------------------------------------------------

    def list(self, request, *args, **kwargs):
        qs = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = ExamSessionListSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = ExamSessionListSerializer(qs, many=True)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        # Resume support: recompute remaining time from the wall clock and
        # auto-submit if the window has elapsed while the student was away.
        self._sync_server_timer(instance, request)
        instance.refresh_from_db()
        serializer = ExamSessionDetailSerializer(instance, context={'request': request})
        return Response(serializer.data)

    # ------------------------------------------------------------------
    # start
    # ------------------------------------------------------------------

    @action(detail=False, methods=['post'], url_path='start')
    def start(self, request):
        """Create a new exam session for the requesting student."""
        if request.user.role != 'student':
            raise PermissionDenied('Only students can start an exam.')

        serializer = StartExamSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        assignment = serializer.validated_data['assignment']
        test = serializer.validated_data['test']

        # Gather IP and browser info
        ip_address = (
            request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
            or request.META.get('REMOTE_ADDR')
        )
        browser_info = request.META.get('HTTP_USER_AGENT', '')[:500]

        with transaction.atomic():
            # Lifecycle: the attempt is created as STARTED (student opened the
            # exam) and immediately transitions to IN_PROGRESS (timer running).
            session = ExamSession.objects.create(
                student=request.user,
                assignment=assignment,
                test=test,
                time_remaining_seconds=test.duration_minutes * 60,
                status=ExamSession.Status.STARTED,
                ip_address=ip_address,
                browser_info=browser_info,
            )
            session.transition_to(ExamSession.Status.IN_PROGRESS)

            # Pre-populate an ExamAnswer row per question (null selected_option)
            test_questions = (
                TestQuestion.objects
                .filter(test=test)
                .select_related('question')
                .order_by('order_number')
            )
            answers_to_create = [
                ExamAnswer(
                    session=session,
                    question=tq.question,
                    selected_option=None,
                    time_spent_seconds=0,
                )
                for tq in test_questions
            ]
            ExamAnswer.objects.bulk_create(answers_to_create)

        # Bound the freshly-created timer the same way every later request is
        # bounded, rather than repeating the formula: a student entering with
        # less time left in the window than the test's duration must see the
        # shorter figure from the very first render, not a full clock that
        # jumps down on the first auto-save. Runs after the atomic block
        # because it may auto-submit, which needs the answers to exist.
        self._sync_server_timer(session, request)

        log_action(
            request.user,
            AuditLog.Action.EXAM_START,
            entity_type='ExamSession',
            entity_id=session.id,
            request=request,
            details={'test_id': test.id, 'test_title': test.title, 'assignment_id': assignment.id},
        )

        # Re-fetch with prefetched answers
        session = ExamSession.objects.select_related('student', 'test', 'assignment').prefetch_related(
            'answers__question',
        ).get(pk=session.pk)

        out = ExamSessionDetailSerializer(session, context={'request': request}).data
        return Response(out, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # save-answer
    # ------------------------------------------------------------------

    @action(detail=True, methods=['post'], url_path='save-answer')
    def save_answer(self, request, pk=None):
        """Save a single answer during an exam. Also updates time_remaining_seconds."""
        session = self.get_object()

        if session.student_id != request.user.id:
            raise PermissionDenied('You can only save answers for your own exam session.')

        # Enforce the wall-clock deadline before accepting the answer.
        if self._sync_server_timer(session, request):
            return Response(
                {'detail': 'Time is up — the exam was auto-submitted.', 'auto_submitted': True},
                status=status.HTTP_409_CONFLICT,
            )
        if session.status != ExamSession.Status.IN_PROGRESS:
            raise ValidationError({'detail': 'This exam session is not in progress.'})

        serializer = SaveAnswerSerializer(
            data=request.data,
            context={'request': request, 'session': session},
        )
        serializer.is_valid(raise_exception=True)

        answer = serializer._answer
        if 'selected_option' in serializer.validated_data:
            answer.selected_option = serializer.validated_data['selected_option']
        if 'time_spent_seconds' in serializer.validated_data:
            answer.time_spent_seconds = serializer.validated_data['time_spent_seconds']
        answer.save(update_fields=['selected_option', 'time_spent_seconds', 'last_updated_at'])

        # The server timer (synced above from started_at) is authoritative; a
        # client-sent value may only shorten it, never extend it.
        time_remaining = serializer.validated_data.get('time_remaining_seconds')
        if time_remaining is not None and time_remaining < session.time_remaining_seconds:
            session.time_remaining_seconds = time_remaining
            session.save(update_fields=['time_remaining_seconds'])

        return Response({
            'detail': 'Answer saved.',
            'question_id': answer.question_id,
            'selected_option': answer.selected_option,
            'time_remaining_seconds': session.time_remaining_seconds,
        })

    # ------------------------------------------------------------------
    # submit
    # ------------------------------------------------------------------

    @action(detail=True, methods=['post'], url_path='submit')
    def submit(self, request, pk=None):
        """Submit the exam — calculates result and creates Result + ResultDetails."""
        session = self.get_object()

        if session.student_id != request.user.id:
            raise PermissionDenied('You can only submit your own exam session.')
        if session.status in (
            ExamSession.Status.SUBMITTED,
            ExamSession.Status.EVALUATED,
            ExamSession.Status.PUBLISHED,
        ):
            return Response(
                {'detail': 'This exam session has already been submitted.'},
                status=status.HTTP_409_CONFLICT,
            )
        # If the deadline passed, the sync auto-submits — same outcome.
        if self._sync_server_timer(session, request):
            return Response({'detail': 'Exam submitted successfully.', 'session_id': session.id})
        if session.status != ExamSession.Status.IN_PROGRESS:
            raise ValidationError({'detail': 'This exam session is not in progress.'})

        self._perform_submit(session, request)

        return Response({'detail': 'Exam submitted successfully.', 'session_id': session.id})

    # ------------------------------------------------------------------
    # cheat-event
    # ------------------------------------------------------------------

    @action(detail=True, methods=['post'], url_path='cheat-event')
    def cheat_event(self, request, pk=None):
        """Log an anti-cheat event. Auto-submits after 3 tab_switch events."""
        session = self.get_object()

        if session.student_id != request.user.id:
            raise PermissionDenied('You can only log events for your own exam session.')
        if self._sync_server_timer(session, request):
            return Response(
                {'detail': 'Time is up — the exam was auto-submitted.', 'auto_submitted': True},
                status=status.HTTP_409_CONFLICT,
            )
        if session.status != ExamSession.Status.IN_PROGRESS:
            return Response(
                {'detail': 'Session is not in progress; event ignored.'},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = AntiCheatEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event_type = serializer.validated_data['event_type']

        AntiCheatLog.objects.create(
            session=session,
            event_type=event_type,
        )

        auto_submitted = False

        # Count rows with tab_switch for this session (each row = 1 event).
        if event_type == AntiCheatLog.EventType.TAB_SWITCH:
            tab_switch_count = AntiCheatLog.objects.filter(
                session=session,
                event_type=AntiCheatLog.EventType.TAB_SWITCH,
            ).count()

            if tab_switch_count >= 3:
                self._perform_submit(session, request, auto=True)
                auto_submitted = True

        return Response({
            'detail': 'Event logged.' + (' Exam auto-submitted due to anti-cheat threshold.' if auto_submitted else ''),
            'auto_submitted': auto_submitted,
        })

    # ------------------------------------------------------------------
    # Teacher review + evaluation
    # ------------------------------------------------------------------

    @staticmethod
    def _effective_max_marks(session: ExamSession) -> dict[int, Decimal]:
        """question_id -> effective max marks (TestQuestion.marks_override wins),
        matching the weighting used by _perform_submit."""
        return {
            tq.question_id: (
                Decimal(str(tq.marks_override)) if tq.marks_override is not None else tq.question.marks
            )
            for tq in TestQuestion.objects.filter(test_id=session.test_id).select_related('question')
        }

    def _require_evaluator(self, session: ExamSession) -> None:
        """Only the teacher who created the test (or CSC Admin) may evaluate.

        School Admins have read-only monitoring access.
        """
        user = self.request.user
        if user.role == 'csc_admin':
            return
        if user.role == 'teacher' and session.test.created_by_id == user.id:
            return
        raise PermissionDenied('Only the teacher who created this test can evaluate it.')

    @action(detail=True, methods=['get'], url_path='review')
    def review(self, request, pk=None):
        """Full answer-review payload for teachers: question, student answer,
        correct answer, and marks per question, plus the current result."""
        session = self.get_object()
        if request.user.role == 'student':
            raise PermissionDenied('Students cannot review exam sessions.')
        if session.status not in (
            ExamSession.Status.SUBMITTED,
            ExamSession.Status.EVALUATED,
            ExamSession.Status.PUBLISHED,
        ):
            raise ValidationError({'detail': 'This exam has not been submitted yet.'})

        try:
            result = (
                Result.objects
                .select_related('student', 'test', 'session')
                .prefetch_related('details__question')
                .get(session=session)
            )
        except Result.DoesNotExist:
            raise ValidationError({'detail': 'No result exists for this session yet.'})

        max_marks_map = self._effective_max_marks(session)

        questions = []
        for detail in result.details.all():
            q = detail.question
            questions.append({
                'question_id': q.pk,
                'question_text': q.question_text,
                'option_a': q.option_a,
                'option_b': q.option_b,
                'option_c': q.option_c,
                'option_d': q.option_d,
                'student_answer': detail.selected_option,
                'correct_answer': detail.correct_option,
                'is_correct': detail.is_correct,
                'marks_obtained': str(detail.marks_obtained),
                'max_marks': str(max_marks_map.get(q.pk, q.marks)),
                'explanation': q.explanation,
            })

        return Response({
            'session_id': session.pk,
            'status': session.status,
            'student_name': session.student.full_name,
            'test_title': session.test.title,
            'subject_name': session.test.subject.name,
            'submitted_at': session.submitted_at,
            'evaluated_at': session.evaluated_at,
            'result': {
                'id': result.pk,
                'total_marks': str(result.total_marks),
                'obtained_marks': str(result.obtained_marks),
                'percentage': str(result.percentage),
                'passed': result.passed,
                'is_published': result.is_published,
            },
            'questions': questions,
        })

    def _parse_evaluate_overrides(self, request_data: dict) -> dict[int, Decimal]:
        overrides: dict[int, Decimal] = {}
        for item in request_data.get('marks', []):
            try:
                qid = int(item['question_id'])
                awarded = Decimal(str(item['marks_awarded']))
            except (KeyError, TypeError, ValueError, ArithmeticError):
                raise ValidationError(
                    {'marks': 'Each item needs integer question_id and numeric marks_awarded.'}
                )
            if awarded < 0:
                raise ValidationError({'marks': 'marks_awarded cannot be negative.'})
            overrides[qid] = awarded
        return overrides

    @action(detail=True, methods=['post'], url_path='evaluate')
    def evaluate(self, request, pk=None):
        """Complete the teacher's answer review.

        Optional body: {"marks": [{"question_id": 1, "marks_awarded": "2.0"}, ...]}
        to override per-question marks (capped at the question's max marks).
        Recalculates the Result and moves the session to EVALUATED.
        """
        session = self.get_object()
        self._require_evaluator(session)

        if session.status not in (
            ExamSession.Status.SUBMITTED,
            ExamSession.Status.EVALUATED,
        ):
            raise ValidationError(
                {'detail': f'Cannot evaluate a session in status "{session.status}".'}
            )

        overrides = self._parse_evaluate_overrides(request.data)

        try:
            result = (
                Result.objects
                .prefetch_related('details__question')
                .get(session=session)
            )
        except Result.DoesNotExist:
            raise ValidationError({'detail': 'No result exists for this session yet.'})

        max_marks_map = self._effective_max_marks(session)

        with transaction.atomic():
            details = list(result.details.all())
            detail_by_qid = {d.question_id: d for d in details}

            unknown = set(overrides) - set(detail_by_qid)
            if unknown:
                raise ValidationError(
                    {'marks': f'Questions not part of this exam: {sorted(unknown)}'}
                )

            changed = []
            for qid, awarded in overrides.items():
                detail = detail_by_qid[qid]
                max_marks = max_marks_map.get(qid, detail.question.marks)
                if awarded > max_marks:
                    raise ValidationError(
                        {'marks': f'Question {qid}: awarded {awarded} exceeds max {max_marks}.'}
                    )
                if detail.marks_obtained != awarded:
                    detail.marks_obtained = awarded
                    detail.is_correct = awarded >= max_marks
                    changed.append(detail)
            if changed:
                ResultDetail.objects.bulk_update(
                    changed, ['marks_obtained', 'is_correct'], batch_size=500,
                )

            # Recalculate totals from the (possibly adjusted) details
            obtained = sum((d.marks_obtained for d in details), Decimal('0'))
            if obtained < Decimal('0'):
                obtained = Decimal('0')
            percentage = (
                obtained / result.total_marks * 100 if result.total_marks > 0 else Decimal('0')
            )
            result.obtained_marks = obtained
            result.percentage = round(percentage, 2)
            result.passed = obtained >= session.test.passing_marks
            result.correct_count = sum(1 for d in details if d.is_correct)
            result.wrong_count = sum(
                1 for d in details if d.selected_option is not None and not d.is_correct
            )
            result.save(update_fields=[
                'obtained_marks', 'percentage', 'passed', 'correct_count', 'wrong_count',
            ])

            session.evaluated_at = timezone.now()
            session.evaluated_by = request.user
            session.transition_to(
                ExamSession.Status.EVALUATED,
                extra_update_fields=['evaluated_at', 'evaluated_by'],
            )

            self._rerank_assignment(session.assignment)

        log_action(
            request.user,
            AuditLog.Action.EXAM_EVALUATED,
            entity_type='ExamSession',
            entity_id=session.pk,
            request=request,
            details={
                'test_id': session.test_id,
                'student_id': session.student_id,
                'overrides': {str(k): str(v) for k, v in overrides.items()},
                'obtained_marks': str(result.obtained_marks),
                'percentage': str(result.percentage),
            },
        )

        return Response({
            'detail': 'Evaluation saved.',
            'session_id': session.pk,
            'status': session.status,
            'obtained_marks': str(result.obtained_marks),
            'total_marks': str(result.total_marks),
            'percentage': str(result.percentage),
            'passed': result.passed,
        })

    # ------------------------------------------------------------------
    # Shared submit logic
    # ------------------------------------------------------------------

    def _perform_submit(self, session: ExamSession, request, auto: bool = False) -> None:
        """
        Mark session as submitted, calculate result, create Result + ResultDetails,
        and re-rank all results for the assignment.
        """
        test = session.test
        assignment = session.assignment

        with transaction.atomic():
            # 1. Update session status (in_progress -> submitted)
            session.submitted_at = timezone.now()
            session.transition_to(
                ExamSession.Status.SUBMITTED, extra_update_fields=['submitted_at'],
            )

            # 2. Fetch answers and corresponding questions / test_questions
            answers = ExamAnswer.objects.filter(session=session).select_related('question')

            # Build a lookup: question_id -> TestQuestion (for marks_override)
            tq_map: dict[int, TestQuestion] = {}
            for tq in TestQuestion.objects.filter(test=test):
                tq_map[tq.question_id] = tq

            # 3. Calculate result
            total_marks = Decimal('0')
            obtained_marks = Decimal('0')
            correct_count = 0
            wrong_count = 0
            unattempted_count = 0
            result_details_to_create: list[ResultDetail] = []

            for answer in answers:
                question = answer.question
                tq = tq_map.get(question.id)

                # Per-question marks: prefer TestQuestion.marks_override, else Question.marks
                q_marks = Decimal(str(tq.marks_override)) if (tq and tq.marks_override is not None) else question.marks
                total_marks += q_marks

                is_correct = False
                marks_obtained = Decimal('0')

                if answer.selected_option is None:
                    # Unanswered
                    unattempted_count += 1
                elif answer.selected_option == question.correct_option:
                    # Correct
                    is_correct = True
                    marks_obtained = q_marks
                    correct_count += 1
                    obtained_marks += q_marks
                else:
                    # Wrong — apply negative marks
                    wrong_count += 1
                    marks_obtained = -question.negative_marks
                    obtained_marks -= question.negative_marks

                result_details_to_create.append(
                    ResultDetail(
                        result=None,  # will be set after Result is created
                        question=question,
                        selected_option=answer.selected_option,
                        correct_option=question.correct_option,
                        is_correct=is_correct,
                        marks_obtained=marks_obtained,
                    )
                )

            # Clamp obtained_marks at 0 (no negative totals)
            if obtained_marks < Decimal('0'):
                obtained_marks = Decimal('0')

            percentage = (
                (obtained_marks / total_marks * 100) if total_marks > 0
                else Decimal('0')
            )

            time_taken = max(test.duration_minutes * 60 - session.time_remaining_seconds, 0)

            passed = obtained_marks >= test.passing_marks

            # 4. Create Result
            result = Result.objects.create(
                session=session,
                student=session.student,
                test=test,
                assignment=assignment,
                total_marks=total_marks,
                obtained_marks=obtained_marks,
                percentage=round(percentage, 2),
                passed=passed,
                correct_count=correct_count,
                wrong_count=wrong_count,
                unattempted_count=unattempted_count,
                time_taken_seconds=time_taken,
                # `Result.is_published` remains the SOLE authority on student visibility —
                # `show_result_immediately` simply decides whether submission itself
                # publishes, instead of waiting for the teacher. Safe to do here because
                # MCQs are auto-graded above, so the score already exists. A teacher can
                # still unpublish afterwards through the normal publish endpoint.
                is_published=test.show_result_immediately,
                published_at=timezone.now() if test.show_result_immediately else None,
            )

            # 5. Create ResultDetails
            for detail in result_details_to_create:
                detail.result = result
            ResultDetail.objects.bulk_create(result_details_to_create)

            # 6. Re-rank all results for this assignment
            self._rerank_assignment(assignment)

        # 7. Notify the teacher who created the test. Never let a
        # notification failure break the submission itself.
        try:
            self._notify_teacher_completed(session)
        except Exception:
            logger.exception('Failed to notify teacher for session %s', session.pk)

        log_action(
            request.user,
            AuditLog.Action.EXAM_SUBMIT,
            entity_type='ExamSession',
            entity_id=session.id,
            request=request,
            details={
                'test_id': test.id,
                'test_title': test.title,
                'assignment_id': assignment.id,
                'auto_submitted': auto,
                'obtained_marks': str(obtained_marks),
                'percentage': str(round(percentage, 2)),
            },
        )

    @staticmethod
    def _notify_teacher_completed(session: ExamSession) -> None:
        """In-app 'Exam Completed' notification for the test's creator."""
        test = session.test
        teacher = test.created_by
        if teacher is None:
            return

        student = session.student
        try:
            profile = student.student_profile
            class_name = profile.school_class.name if profile.school_class_id else ''
            section_name = profile.section.name if profile.section_id else ''
        except StudentProfile.DoesNotExist:
            class_name = section_name = ''

        class_section = f'{class_name}-{section_name}'.strip('-')
        submitted_at = session.submitted_at

        notify_user(
            teacher,
            Notification.Type.EXAM_COMPLETED,
            'Exam Completed',
            (
                f'Student {student.full_name}'
                + (f' ({class_section})' if class_section else '')
                + f' has completed {test.subject.name} - {test.title}.'
            ),
            data={
                'session_id': session.pk,
                'test_id': test.pk,
                'student_id': student.pk,
                'student_name': student.full_name,
                'class': class_name,
                'section': section_name,
                'subject': test.subject.name,
                'completed_at': submitted_at.isoformat() if submitted_at else None,
            },
        )

    @staticmethod
    def _rerank_assignment(assignment) -> None:
        """Re-rank every result of this assignment.

        A thin delegate: the algorithm moved to ``apps.results.services`` because
        publishing needs it too, and two copies of a ranking rule is how ties end
        up numbered one way on submit and another way on publish.
        """
        rerank_assignment(assignment.pk)
