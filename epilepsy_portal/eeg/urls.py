from django.urls import path
from . import views

app_name = "eeg"
urlpatterns = [
    path("patients/<int:patient_id>/", views.workspace, name="workspace"),
    path("patients/<int:patient_id>/state/", views.state, name="state"),
    path("patients/<int:patient_id>/recordings/", views.create_recording, name="create_recording"),
    path("recordings/<uuid:recording_id>/uploads/", views.create_upload, name="create_upload"),
    path("uploads/<uuid:upload_id>/", views.upload_status, name="upload_status"),
    path("uploads/<uuid:upload_id>/chunk/", views.upload_chunk, name="upload_chunk"),
    path("uploads/<uuid:upload_id>/complete/", views.finish_upload, name="finish_upload"),
    path("uploads/<uuid:upload_id>/discard/", views.discard, name="discard_upload"),
    path("recordings/<uuid:recording_id>/jobs/", views.create_job, name="create_job"),
    path("jobs/<uuid:job_id>/", views.job_status, name="job_status"),
    path("jobs/<uuid:job_id>/cancel/", views.cancel_job, name="cancel_job"),
    path("jobs/<uuid:job_id>/artifacts/<str:name>/", views.artifact, name="artifact"),
    path("files/<uuid:file_id>/download/", views.download_file, name="download_file"),
]
