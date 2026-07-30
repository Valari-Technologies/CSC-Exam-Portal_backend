"""Domain services for the tests app.

Currently: notification fan-out when a test is assigned.
"""
from __future__ import annotations

import logging

from apps.notifications.models import Notification
from apps.notifications.services import notify_users
from apps.students.models import StudentProfile

from .models import TestAssignment, TestAssignmentStudent

logger = logging.getLogger(__name__)


def resolve_assignment_recipients(assignment: TestAssignment) -> list[int]:
    """Return the user ids of every student targeted by *assignment*.

    Mirrors the eligibility rules in
    ``apps.exams.serializers._validate_student_assigned``:
    - CLASS    -> all active students in that class
    - SECTION  -> only active students in that section
    - STUDENTS -> only students explicitly linked via TestAssignmentStudent
    """
    assigned_type = assignment.assigned_to_type

    if assigned_type == TestAssignment.AssignedToType.CLASS:
        return list(
            StudentProfile.objects.filter(
                school_class_id=assignment.school_class_id,
                is_active=True,
                user__is_active=True,
            ).values_list('user_id', flat=True)
        )

    if assigned_type == TestAssignment.AssignedToType.SECTION:
        return list(
            StudentProfile.objects.filter(
                section_id=assignment.section_id,
                is_active=True,
                user__is_active=True,
            ).values_list('user_id', flat=True)
        )

    if assigned_type == TestAssignment.AssignedToType.STUDENTS:
        return list(
            TestAssignmentStudent.objects.filter(
                assignment=assignment,
                student__is_active=True,
            ).values_list('student_id', flat=True)
        )

    return []


def create_assignment_notifications(assignment: TestAssignment) -> int:
    """Create one 'New Test Assigned' notification per eligible student.

    The title/message is built once and inserted via ``bulk_create``
    (see ``notify_users``), so large classes don't cause N+1 writes.

    Returns the number of notifications created.
    """
    test = assignment.test
    user_ids = resolve_assignment_recipients(assignment)
    if not user_ids:
        logger.info(
            'Assignment %s (%s) has no eligible students to notify.',
            assignment.pk, assignment.assigned_to_type,
        )
        return 0

    title = 'New Test Assigned'
    message = (
        f'A new {test.subject.name} - {test.title} test has been assigned to you. '
        f'Please log in and complete the exam.'
    )
    data = {'test_id': test.pk, 'assignment_id': assignment.pk}

    return notify_users(
        user_ids,
        Notification.Type.TEST_ASSIGNED,
        title,
        message,
        data,
    )
