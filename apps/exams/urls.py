from rest_framework.routers import DefaultRouter

from .views import ExamSessionViewSet

router = DefaultRouter()
router.register(r'sessions', ExamSessionViewSet, basename='exam-session')

urlpatterns = router.urls
