from django.urls import path

from .views import ClassReportView, StudentReportView, SubjectReportView, TestReportView

urlpatterns = [
    path('class/', ClassReportView.as_view(), name='report-class'),
    path('subject/', SubjectReportView.as_view(), name='report-subject'),
    path('test/<int:test_id>/', TestReportView.as_view(), name='report-test'),
    path('student/<int:student_id>/', StudentReportView.as_view(), name='report-student'),
]
