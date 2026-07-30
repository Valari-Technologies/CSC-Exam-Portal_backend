from rest_framework.routers import DefaultRouter

from .views import ChapterViewSet, ClassViewSet, SectionViewSet, SubjectViewSet

router = DefaultRouter()
router.register(r'classes', ClassViewSet, basename='class')
router.register(r'sections', SectionViewSet, basename='section')
router.register(r'subjects', SubjectViewSet, basename='subject')
router.register(r'chapters', ChapterViewSet, basename='chapter')

urlpatterns = router.urls
