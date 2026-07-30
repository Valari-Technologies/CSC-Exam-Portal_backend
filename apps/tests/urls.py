from rest_framework.routers import DefaultRouter

from .views import TestAssignmentViewSet, TestViewSet

router = DefaultRouter()
# Register the explicit 'assignments' prefix BEFORE the empty prefix so its
# routes are not shadowed by TestViewSet's detail pattern
# (^(?P<pk>[^/.]+)/$ would otherwise capture 'assignments' as a pk,
# making POST /tests/assignments/ return 405 Method Not Allowed).
router.register(r'assignments', TestAssignmentViewSet, basename='test-assignment')
router.register(r'', TestViewSet, basename='test')

urlpatterns = router.urls
