import uuid

from django.conf import settings
from django.db import models


class Recording(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey("epilepsy.Patient", on_delete=models.CASCADE, related_name="eeg_recordings")
    title = models.CharField(max_length=200)
    modality = models.CharField(max_length=4, choices=[("eeg", "EEG"), ("seeg", "sEEG")])
    status = models.CharField(max_length=20, default="empty")
    metadata = models.JSONField(default=dict, blank=True)
    primary_file = models.ForeignKey("EegAsset", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class EegAsset(models.Model):
    ROLES = [(name, name) for name in ("raw", "companion", "forward", "covariance", "trans", "bem", "src", "electrodes", "mri")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recording = models.ForeignKey(Recording, on_delete=models.CASCADE, related_name="files")
    role = models.CharField(max_length=20, choices=ROLES)
    name = models.CharField(max_length=255)
    name_key = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField()
    uploaded_size = models.PositiveBigIntegerField(default=0)
    complete = models.BooleanField(default=False)
    sha256 = models.CharField(max_length=64, blank=True)
    relative_path = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [models.UniqueConstraint(fields=["recording", "name_key"], name="eeg_asset_recording_name_unique")]


class AnalysisJob(models.Model):
    OPERATIONS = [(name, name) for name in ("inspect", "waveform", "psd", "tfr", "source", "preprocess")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recording = models.ForeignKey(Recording, on_delete=models.CASCADE, related_name="jobs")
    operation = models.CharField(max_length=20, choices=OPERATIONS)
    lane = models.CharField(max_length=10, db_index=True)
    parameters = models.JSONField(default=dict)
    request = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default="queued", db_index=True)
    progress = models.PositiveSmallIntegerField(default=0)
    message = models.CharField(max_length=300, default="等待处理")
    result = models.JSONField(default=dict, blank=True)
    error = models.CharField(max_length=1000, blank=True)
    cancel_requested = models.BooleanField(default=False)
    process_pid = models.PositiveIntegerField(null=True, blank=True)
    process_start = models.CharField(max_length=50, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["lane", "status", "created_at"], name="eeg_job_lane_state_idx")]
