import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL), ("epilepsy", "0051_llm_provider")]
    operations = [
        migrations.CreateModel(name="Recording", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("title", models.CharField(max_length=200)),
            ("modality", models.CharField(choices=[("eeg", "EEG"), ("seeg", "sEEG")], max_length=4)),
            ("status", models.CharField(default="empty", max_length=20)),
            ("metadata", models.JSONField(blank=True, default=dict)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ("patient", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="eeg_recordings", to="epilepsy.patient")),
        ], options={"ordering": ["-created_at"]}),
        migrations.CreateModel(name="EegAsset", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("role", models.CharField(choices=[(name, name) for name in ("raw", "companion", "forward", "covariance", "trans", "bem", "src", "electrodes", "mri")], max_length=20)),
            ("name", models.CharField(max_length=255)), ("name_key", models.CharField(max_length=255)),
            ("size", models.PositiveBigIntegerField()), ("uploaded_size", models.PositiveBigIntegerField(default=0)),
            ("complete", models.BooleanField(default=False)), ("sha256", models.CharField(blank=True, max_length=64)),
            ("relative_path", models.CharField(max_length=500)), ("created_at", models.DateTimeField(auto_now_add=True)),
            ("recording", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="files", to="eeg.recording")),
        ], options={"ordering": ["created_at"], "constraints": [models.UniqueConstraint(fields=("recording", "name_key"), name="eeg_asset_recording_name_unique")]}),
        migrations.AddField(model_name="recording", name="primary_file", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="eeg.eegasset")),
        migrations.CreateModel(name="AnalysisJob", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("operation", models.CharField(choices=[(name, name) for name in ("inspect", "waveform", "psd", "tfr", "source", "preprocess")], max_length=20)),
            ("lane", models.CharField(db_index=True, max_length=10)),
            ("parameters", models.JSONField(default=dict)), ("request", models.JSONField(default=dict)),
            ("status", models.CharField(db_index=True, default="queued", max_length=20)),
            ("progress", models.PositiveSmallIntegerField(default=0)),
            ("message", models.CharField(default="等待处理", max_length=300)),
            ("result", models.JSONField(blank=True, default=dict)), ("error", models.CharField(blank=True, max_length=1000)),
            ("cancel_requested", models.BooleanField(default=False)), ("process_pid", models.PositiveIntegerField(blank=True, null=True)),
            ("process_start", models.CharField(blank=True, max_length=50)), ("created_at", models.DateTimeField(auto_now_add=True)),
            ("started_at", models.DateTimeField(blank=True, null=True)), ("finished_at", models.DateTimeField(blank=True, null=True)),
            ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ("recording", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="jobs", to="eeg.recording")),
        ], options={"ordering": ["created_at"], "indexes": [models.Index(fields=["lane", "status", "created_at"], name="eeg_job_lane_state_idx")]}),
    ]
