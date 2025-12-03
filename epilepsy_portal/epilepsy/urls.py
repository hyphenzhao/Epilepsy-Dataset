from django.urls import path
from . import views

app_name = "epilepsy"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),  # 全局总览
    path("patients/", views.PatientListView.as_view(), name="patient_list"),  # 浏览患者
    path("patients/new/", views.PatientCreateView.as_view(), name="patient_create"),  # 新建患者
    path("patients/<int:pk>/edit/", views.PatientUpdateView.as_view(), name="patient_update"),  # 修改
    path("patients/<int:pk>/delete/", views.patient_delete, name="patient_delete"),  # 删除 (POST)
    path("patients/<int:pk>/datasets/", views.PatientDatasetListView.as_view(),
         name="patient_datasets"),
    path("datasets/<int:pk>/download/", views.patient_dataset_download,
         name="dataset_download"),
    path("patients/<int:pk>/edit/", views.patient_edit, name="patient_edit"),
    path("patients/<int:pk>/detail/", views.patient_detail, name="patient_detail"),
    path("settings/users/", views.UserListView.as_view(), name="user_list"),  # 管理设置
]
