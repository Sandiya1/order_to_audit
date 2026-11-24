from django.urls import path

from . import views

urlpatterns = [
    path("", views.reports_index, name="reports_index"),
    path("<slug:slug>/", views.report_view, name="report_view"),
]
