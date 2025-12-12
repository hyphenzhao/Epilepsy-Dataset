from django.urls import path
from . import views

app_name = "epilepsy"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),  # 全局总览
    path("patients/", views.PatientListView.as_view(), name="patient_list"),  # 浏览患者
    path("patients/new/", views.PatientCreateView.as_view(), name="patient_create"),  # 新建患者
    # path("patients/<int:pk>/edit/", views.PatientUpdateView.as_view(), name="patient_update"),  # 修改
    path("patients/<int:pk>/delete/", views.patient_delete, name="patient_delete"),  # 删除 (POST)
    path("patients/<int:pk>/datasets/", views.PatientDatasetListView.as_view(),
         name="patient_datasets"),
    path("datasets/<int:pk>/download/", views.patient_dataset_download,
         name="dataset_download"),
    # 新增：右侧下载管理抽屉的数据接口
    path("patients/<int:pk>/files/",views.patient_files_panel,name="patient_files_panel",),
    path("patients/<int:pk>/export/<str:fmt>/",views.patient_export,name="patient_export",),
    path("patients/<int:pk>/edit/", views.patient_edit, name="patient_edit"),
    path("patients/<int:pk>/detail/", views.patient_detail, name="patient_detail"),
    path('patients/batch_download_info/', views.batch_download_info, name='batch_download_info'),
    path('patients/batch_delete/', views.batch_delete_patients, name='batch_delete_patients'),
    path('patients/batch_download_files/', views.batch_download_files, name='batch_download_files'),
    
    path("settings/users/", views.UserListView.as_view(), name="user_list"),  # 管理设置
    path("users/", views.UserListView.as_view(), name="user_list"),
    path("users/create/", views.user_create, name="user_create"),
    path("users/<int:pk>/edit/", views.user_edit, name="user_edit"),
    path("users/<int:pk>/toggle-active/", views.user_toggle_active, name="user_toggle_active"),
    path("users/<int:pk>/delete/", views.user_delete, name="user_delete"),
    path("files/<str:file_type>/<int:file_id>/download/",views.patient_file_download,name="patient_file_download",),
    path("about/", views.AboutView.as_view(), name="about"),
    ]
