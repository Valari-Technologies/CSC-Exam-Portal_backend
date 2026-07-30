"""Root URL configuration."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, include


def root_view(request):
    return JsonResponse({
        'status': 'running',
        'service': 'CSC Online Exam Portal - Backend API',
        'api': '/api/v1/',
        'health': '/api/v1/auth/health/',
        'admin': '/admin/',
    })


urlpatterns = [
    path('', root_view),
    path('admin/', admin.site.urls),

    # Auth
    path('api/v1/auth/', include('apps.authentication.urls')),

    # Core resources
    path('api/v1/schools/', include('apps.schools.urls')),
    path('api/v1/', include('apps.academics.urls')),  # classes, sections, subjects, chapters
    path('api/v1/teachers/', include('apps.teachers.urls')),
    path('api/v1/students/', include('apps.students.urls')),
    path('api/v1/questions/', include('apps.questions.urls')),
    path('api/v1/tests/', include('apps.tests.urls')),
    path('api/v1/exam/', include('apps.exams.urls')),
    path('api/v1/results/', include('apps.results.urls')),
    path('api/v1/reports/', include('apps.reports.urls')),
    path('api/v1/notifications/', include('apps.notifications.urls')),
    path('api/v1/audit-logs/', include('apps.audit.urls')),
    path('api/v1/support-requests/', include('apps.support.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
