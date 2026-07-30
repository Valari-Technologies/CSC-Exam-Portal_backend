"""Result visibility + publish permissions — the grade-critical path.

- A student sees a result ONLY after it is published.
- Only the creating teacher (or CSC admin) may publish; a school admin cannot.
"""

from rest_framework import status
from rest_framework.test import APITestCase

from apps.exams.models import ExamSession
from tests import factories


class ResultVisibilityTests(APITestCase):
    def setUp(self):
        self.school = factories.make_school()
        klass = factories.make_class(self.school)
        section = factories.make_section(self.school, klass)
        subject = factories.make_subject(self.school, klass)
        chapter = factories.make_chapter(subject)

        self.teacher = factories.make_user("teacher", self.school)
        self.school_admin = factories.make_user("school_admin", self.school)
        self.student = factories.make_student(self.school, klass, section)

        question = factories.make_question(self.school, subject, chapter, created_by=self.teacher)
        test = factories.make_test(self.school, subject, klass, self.teacher, questions=[question])
        assignment = factories.make_assignment(test, klass, self.teacher)
        self.session = factories.make_session(
            self.student,
            assignment,
            test,
            status=ExamSession.Status.SUBMITTED,
        )
        self.result = factories.make_result(self.session, is_published=False)

    def test_student_cannot_see_unpublished_result(self):
        self.client.force_authenticate(user=self.student)
        resp = self.client.get("/api/v1/results/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 0)

    def test_creating_teacher_publishes_then_student_sees_it(self):
        self.client.force_authenticate(user=self.teacher)
        pub = self.client.post(
            f"/api/v1/results/{self.result.id}/publish/",
            {"is_published": True},
            format="json",
        )
        self.assertEqual(pub.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(user=self.student)
        resp = self.client.get("/api/v1/results/")
        self.assertEqual(resp.data["count"], 1)
        self.assertTrue(resp.data["results"][0]["is_published"])

    def test_school_admin_cannot_publish(self):
        self.client.force_authenticate(user=self.school_admin)
        resp = self.client.post(
            f"/api/v1/results/{self.result.id}/publish/",
            {"is_published": True},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.result.refresh_from_db()
        self.assertFalse(self.result.is_published)
