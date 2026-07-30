"""Multi-tenant isolation — a school admin must never see another school's data."""

from rest_framework import status
from rest_framework.test import APITestCase

from tests import factories


class StudentTenantIsolationTests(APITestCase):
    def setUp(self):
        # School A with one student
        self.school_a = factories.make_school()
        class_a = factories.make_class(self.school_a)
        section_a = factories.make_section(self.school_a, class_a)
        self.admin_a = factories.make_user("school_admin", self.school_a)
        self.student_a = factories.make_student(self.school_a, class_a, section_a)

        # School B with one student
        self.school_b = factories.make_school()
        class_b = factories.make_class(self.school_b)
        section_b = factories.make_section(self.school_b, class_b)
        self.student_b = factories.make_student(self.school_b, class_b, section_b)

    def test_admin_lists_only_own_school_students(self):
        self.client.force_authenticate(user=self.admin_a)
        resp = self.client.get("/api/v1/students/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)
        returned_emails = {row["user_email"] for row in resp.data["results"]}
        self.assertIn(self.student_a.email, returned_emails)
        self.assertNotIn(self.student_b.email, returned_emails)

    def test_admin_cannot_fetch_other_school_student_detail(self):
        self.client.force_authenticate(user=self.admin_a)
        other_profile_id = self.student_b.student_profile.id
        resp = self.client.get(f"/api/v1/students/{other_profile_id}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
