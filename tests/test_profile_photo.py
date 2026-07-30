"""Profile photo upload — item 3.

Every user (all four roles share one Profile page) can upload a photo to /auth/me/ via a
multipart PATCH. Type and size are validated on the SERVER, not just the form: the tests
here send the file the way the browser does and pin both limits (JPG/JPEG/PNG, max 5MB).

Uploads are written to a throwaway MEDIA_ROOT so the test never touches real media.
"""
import tempfile
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from apps.authentication.serializers import UserSerializer

from .factories import make_school, make_user


def _image_upload(name='photo.png', fmt='PNG', size=(12, 12)) -> SimpleUploadedFile:
    buf = BytesIO()
    Image.new('RGB', size, (200, 30, 30)).save(buf, format=fmt)
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type=f'image/{fmt.lower()}')


class _FakeUpload:
    """Stands in for an uploaded file so the size check can be tested without a 5MB blob."""

    def __init__(self, name: str, size: int):
        self.name = name
        self.size = size


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ProfilePhotoUploadTests(APITestCase):
    ME_URL = '/api/v1/auth/me/'

    def setUp(self):
        self.school = make_school()
        self.user = make_user('teacher', self.school)

    def test_upload_sets_photo_and_returns_absolute_url(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch(
            self.ME_URL, {'profile_photo': _image_upload()}, format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(response.data['profile_photo_url'])
        self.assertTrue(response.data['profile_photo_url'].startswith('http'))

    def test_url_is_null_when_no_photo(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(self.ME_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['profile_photo_url'])

    def test_rejects_a_non_allowed_extension(self):
        """A real image, but a GIF — outside the allowed JPG/JPEG/PNG set."""
        self.client.force_authenticate(self.user)
        response = self.client.patch(
            self.ME_URL,
            {'profile_photo': _image_upload(name='photo.gif', fmt='GIF')},
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('profile_photo', response.data)

    def test_rejects_a_file_over_5mb(self):
        # Tested at the validator so the check doesn't depend on allocating a 5MB image.
        oversized = _FakeUpload('big.png', 5 * 1024 * 1024 + 1)
        serializer = UserSerializer()
        with self.assertRaises(Exception):
            serializer.validate_profile_photo(oversized)

    def test_a_5mb_file_is_accepted_by_the_validator(self):
        exactly_max = _FakeUpload('ok.png', 5 * 1024 * 1024)
        serializer = UserSerializer()
        # Must not raise.
        self.assertIs(serializer.validate_profile_photo(exactly_max), exactly_max)

    def test_requires_authentication(self):
        response = self.client.patch(
            self.ME_URL, {'profile_photo': _image_upload()}, format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_students_can_upload_too(self):
        """All roles share the Profile page — a student is not a special case."""
        from .factories import make_class, make_section, make_student

        klass = make_class(self.school)
        section = make_section(self.school, klass, name='A')
        student = make_student(self.school, klass, section)
        self.client.force_authenticate(student)
        response = self.client.patch(
            self.ME_URL, {'profile_photo': _image_upload()}, format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(response.data['profile_photo_url'])
