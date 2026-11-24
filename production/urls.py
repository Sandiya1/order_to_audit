from django.urls import path

from . import views

urlpatterns = [
    path("production/", views.production_list, name="production_list"),
    path("production/new/", views.production_new, name="production_new"),
    path("production/<int:pk>/", views.production_detail, name="production_detail"),
]
