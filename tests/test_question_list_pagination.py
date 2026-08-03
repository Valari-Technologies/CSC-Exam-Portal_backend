"""The questions list must honour ?page_size.

Both callers of this endpoint group questions client-side — the Question Bank by
subject, the test question picker by chapter — and a group silently cut in half by
an invisible page boundary is simply wrong. Both therefore request a large page.

The trap this guards: DRF's stock ``PageNumberPagination`` has
``page_size_query_param = None``, so it does not reject ``?page_size=200`` — it
IGNORES it and returns 20 with no error anywhere. The frontend's "fetch 200 and
group" was quietly operating on 20 rows. Mirrors
``test_subject_list_pagination`` for SubjectViewSet.
"""
from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from .factories import (
    make_chapter,
    make_class,
    make_question,
    make_school,
    make_subject,
    make_user,
)


class QuestionListPaginationTests(APITestCase):
    """A bank larger than the old 20-row cap."""

    TOTAL = 45

    def setUp(self):
        self.school = make_school()
        self.klass = make_class(self.school, numeric_value=10)
        self.subject = make_subject(self.school, self.klass)
        self.admin = make_user('school_admin', self.school)

        # Several chapters, so a truncated response would also lose whole groups.
        self.chapters = [make_chapter(self.subject) for _ in range(3)]
        for i in range(self.TOTAL):
            make_question(
                self.school,
                self.subject,
                self.chapters[i % len(self.chapters)],
                marks=Decimal('1'),
            )

        self.client.force_authenticate(self.admin)

    def _list(self, **params):
        response = self.client.get('/api/v1/questions/', params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def test_page_size_is_honoured(self):
        """The regression itself: 200 requested, 200 (well, all 45) delivered."""
        data = self._list(page_size=200)
        self.assertEqual(data['count'], self.TOTAL)
        self.assertEqual(len(data['results']), self.TOTAL)
        self.assertIsNone(data['next'])

    def test_every_chapter_survives_the_fetch(self):
        """What the grouping actually depends on — no group is cut off."""
        data = self._list(page_size=200)
        returned = {q['chapter']['id'] for q in data['results']}
        self.assertEqual(returned, {c.pk for c in self.chapters})

    def test_a_small_page_size_is_still_respected(self):
        """Honouring the param must mean honouring it in both directions."""
        data = self._list(page_size=5)
        self.assertEqual(len(data['results']), 5)
        self.assertIsNotNone(data['next'])

    def test_page_size_is_capped(self):
        """LargePagination.max_page_size — an absurd value must not be a DoS."""
        data = self._list(page_size=100000)
        self.assertLessEqual(len(data['results']), 1000)

    def test_default_page_size_needs_no_param(self):
        """A caller that passes nothing still gets the whole bank here (45 < 100)."""
        data = self._list()
        self.assertEqual(len(data['results']), self.TOTAL)
