from rest_framework.routers import DefaultRouter

from .views import SupportRequestViewSet

router = DefaultRouter()
router.register(r'', SupportRequestViewSet, basename='support-request')

urlpatterns = router.urls
