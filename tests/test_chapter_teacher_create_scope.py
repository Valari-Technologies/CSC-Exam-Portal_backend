"""A teacher may manage chapters only for the subjects assigned to them.

Enforcement lives in ChapterViewSet: teachers are let through create/update/partial_update/
destroy, but perform_create/perform_update/destroy reject any subject outside their
assignment scope. Two assignment shapes are honoured (mirrors QuestionViewSet.get_queryset):
a subject-bound row grants exactly that subject; a whole-class row (no subject named) grants
every subject of that class. Editing, activating/deactivating and deleting are all scoped —
the trailing tests lock in both the allowed (in-scope) and forbidden (out-of-scope) paths.
"""
from rest_framework import status
from rest_framework.test import APITestCase

from apps.teachers.models import TeacherAssignment, TeacherProfile

from .factories import (
    make_chapter,
    make_class,
    make_school,
    make_subject,
    make_user,
)


class TeacherChapterCreateScopeTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.class_a = make_class(self.school)
        self.class_b = make_class(self.school)
        # Class A has two subjects; a subject-bound teacher gets only one of them.
        self.subject_a1 = make_subject(self.school, self.class_a)
        self.subject_a2 = make_subject(self.school, self.class_a)
        self.subject_b = make_subject(self.school, self.class_b)

        self.teacher_user = make_user('teacher', self.school)
        self.teacher = TeacherProfile.objects.create(
            user=self.teacher_user, school=self.school, teacher_id='X_TR_001',
        )

    def _assign_subject(self, subject, school_class):
        TeacherAssignment.objects.create(
            teacher=self.teacher, subject=subject, school_class=school_class,
        )

    def _assign_whole_class(self, school_class):
        TeacherAssignment.objects.create(teacher=self.teacher, school_class=school_class)

    def _payload(self, subject):
        return {
            'subject': subject.id,
            'name': 'A New Chapter',
            'order_number': 1,
            'description': '',
            'is_active': True,
        }

    def test_teacher_creates_chapter_for_assigned_subject(self):
        self._assign_subject(self.subject_a1, self.class_a)
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.post('/api/v1/chapters/', self._payload(self.subject_a1))

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['subject'], self.subject_a1.id)

    def test_teacher_blocked_from_unassigned_subject_in_assigned_class(self):
        """Subject-bound to subject_a1 — a sibling subject of the same class is forbidden."""
        self._assign_subject(self.subject_a1, self.class_a)
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.post('/api/v1/chapters/', self._payload(self.subject_a2))

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_whole_class_assignment_allows_all_subjects_of_that_class(self):
        self._assign_whole_class(self.class_a)
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.post('/api/v1/chapters/', self._payload(self.subject_a2))

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_teacher_blocked_from_subject_in_unassigned_class(self):
        self._assign_subject(self.subject_a1, self.class_a)
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.post('/api/v1/chapters/', self._payload(self.subject_b))

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_teacher_with_no_assignments_cannot_create(self):
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.post('/api/v1/chapters/', self._payload(self.subject_a1))

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_school_admin_can_create_chapter(self):
        admin = make_user('school_admin', self.school)
        self.client.force_authenticate(admin)

        resp = self.client.post('/api/v1/chapters/', self._payload(self.subject_b))

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_teacher_updates_chapter_in_assigned_scope(self):
        """Edit is open for teachers within their assigned subject scope."""
        self._assign_subject(self.subject_a1, self.class_a)
        chapter = make_chapter(self.subject_a1)
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.patch(
            f'/api/v1/chapters/{chapter.id}/', {'name': 'Renamed'},
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['name'], 'Renamed')

    def test_teacher_toggles_status_in_assigned_scope(self):
        """Activate/deactivate (PATCH is_active) is allowed within scope."""
        self._assign_subject(self.subject_a1, self.class_a)
        chapter = make_chapter(self.subject_a1)
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.patch(
            f'/api/v1/chapters/{chapter.id}/', {'is_active': False},
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['is_active'])

    def test_teacher_cannot_update_chapter_outside_scope(self):
        """Subject-bound to subject_a1 — a sibling subject's chapter is off-limits to edit."""
        self._assign_subject(self.subject_a1, self.class_a)
        chapter = make_chapter(self.subject_a2)
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.patch(
            f'/api/v1/chapters/{chapter.id}/', {'name': 'Renamed'},
        )

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_teacher_deletes_chapter_in_assigned_scope(self):
        self._assign_subject(self.subject_a1, self.class_a)
        chapter = make_chapter(self.subject_a1)
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.delete(f'/api/v1/chapters/{chapter.id}/')

        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_teacher_cannot_delete_chapter_outside_scope(self):
        self._assign_subject(self.subject_a1, self.class_a)
        chapter = make_chapter(self.subject_a2)
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.delete(f'/api/v1/chapters/{chapter.id}/')

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
