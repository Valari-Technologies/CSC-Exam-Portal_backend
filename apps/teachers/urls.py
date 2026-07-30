from rest_framework.routers import DefaultRouter

from .views import TeacherAssignmentViewSet, TeacherProfileViewSet

router = DefaultRouter()
# Register the explicit 'assignments' prefix BEFORE the empty prefix so its
# routes are not shadowed by TeacherProfileViewSet's detail pattern
# (^(?P<pk>[^/.]+)/$ would otherwise capture 'assignments' as a pk).
router.register(r'assignments', TeacherAssignmentViewSet, basename='teacher-assignment')
router.register(r'', TeacherProfileViewSet, basename='teacher')

urlpatterns = router.urls
