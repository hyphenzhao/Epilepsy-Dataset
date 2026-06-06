from django.urls import path

from . import views

app_name = "knowledge"

urlpatterns = [
    path("", views.knowledge_list, name="list"),
    path("upload/", views.knowledge_upload, name="upload"),
    path("upload/preview/", views.knowledge_upload_preview, name="upload_preview"),
    path("<int:pk>/", views.knowledge_detail, name="detail"),
    path("<int:pk>/delete/", views.knowledge_delete, name="delete"),
    path("edit/save/", views.save_report_edit, name="save_edit"),
    path("settings/", views.knowledge_settings, name="settings"),
]
