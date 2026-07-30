from rest_framework.routers import DefaultRouter

from .views import ResultViewSet

router = DefaultRouter()
router.register(r'', ResultViewSet, basename='result')

urlpatterns = router.urls
