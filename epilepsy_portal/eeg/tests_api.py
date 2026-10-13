"""Private workspace contracts; synthetic bytes and Django's test database only."""

from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError, connection
from django.middleware.csrf import get_token
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from epilepsy.models import Patient, UserProfile, UserRole
from .models import AnalysisJob, EegAsset, Recording
from .queue import _known_orphan, _validated_result, cancel, claim_next, cleanup_waveform_previews, db_retry, enqueue, job_directory, recover_interrupted, run_job
from .storage import WorkspaceError, append_chunk, begin_upload, complete_upload, discard_upload, private_path, snapshot_files, storage_root


class WorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.patient = Patient.objects.create(name="Synthetic EEG", gender="M", birthday=date(1990, 1, 1), handedness="R", admission_date=date(2025, 1, 1))
        cls.users = {}
        for role in (UserRole.ADMIN, UserRole.STAFF, UserRole.GUEST):
            user = get_user_model().objects.create_user(username="eeg_" + role.lower())
            UserProfile.objects.create(user=user, role=role)
            cls.users[role] = user
        cls.unprofiled = get_user_model().objects.create_user(username="eeg_no_role")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="eeg-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        settings = override_settings(EEG_STORAGE_ROOT=self.root, EEG_UPLOAD_DISK_RESERVE_BYTES=0, EEG_JOB_DISK_RESERVE_BYTES=0)
        settings.enable()
        self.addCleanup(settings.disable)
        self.client.force_login(self.users[UserRole.STAFF])
        self.recording = Recording.objects.create(patient=self.patient, title="Synthetic recording", modality="eeg")

    def url(self, name, *args):
        return reverse("eeg:" + name, args=args)

    def post(self, name, args, data):
        return self.client.post(self.url(name, *args), data=json.dumps(data), content_type="application/json")

    def asset(self, recording=None, name="synthetic.edf", data=b"synthetic", role="raw"):
        asset = begin_upload(recording or self.recording, name, len(data), role)
        append_chunk(asset, 0, data)
        return complete_upload(asset)

    def job(self, operation="waveform"):
        if not self.recording.files.filter(complete=True).exists():
            self.asset()
        self.recording.refresh_from_db()
        return enqueue(self.recording, operation, {}, self.users[UserRole.STAFF])

    def test_anonymous_and_unprofiled_routes_are_protected(self):
        asset = self.asset()
        job = self.job()
        reads = [("workspace", self.patient.pk), ("state", self.patient.pk), ("upload_status", asset.pk), ("job_status", job.pk), ("artifact", job.pk, "plot.png"), ("download_file", asset.pk)]
        for user, expected in ((None, 302), (self.unprofiled, 403)):
            self.client.logout()
            if user:
                self.client.force_login(user)
            for route in reads:
                with self.subTest(user=user, route=route):
                    self.assertEqual(self.client.get(self.url(*route)).status_code, expected)
            self.assertEqual(self.post("create_recording", [self.patient.pk], {"title": "x"}).status_code, expected)

    def test_guest_reads_but_every_mutation_is_forbidden(self):
        asset = self.asset()
        job = self.job()
        self.client.force_login(self.users[UserRole.GUEST])
        self.assertEqual(self.client.get(self.url("workspace", self.patient.pk)).status_code, 200)
        self.assertFalse(self.client.get(self.url("state", self.patient.pk)).json()["can_write"])
        self.assertEqual(self.client.get(self.url("upload_status", asset.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url("job_status", job.pk)).status_code, 200)
        response = self.client.get(self.url("download_file", asset.pk))
        self.assertEqual(response.status_code, 200)
        response.close()
        requests = [("create_recording", self.patient.pk), ("create_upload", self.recording.pk), ("upload_chunk", asset.pk), ("finish_upload", asset.pk), ("discard_upload", asset.pk), ("create_job", self.recording.pk), ("cancel_job", job.pk)]
        for route in requests:
            with self.subTest(route=route):
                self.assertEqual(self.client.post(self.url(*route)).status_code, 403)

    def test_staff_and_admin_create_recordings(self):
        for role in (UserRole.STAFF, UserRole.ADMIN):
            self.client.force_login(self.users[role])
            response = self.post("create_recording", [self.patient.pk], {"title": "New synthetic", "modality": "seeg"})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()["modality"], "seeg")

    def test_missing_patients_and_uuid_assets_return_404(self):
        other = Recording.objects.create(patient=self.patient, title="deleted", modality="eeg")
        pk = other.pk
        other.delete()
        self.assertEqual(self.client.get(self.url("state", 99999999)).status_code, 404)
        self.assertEqual(self.post("create_upload", [pk], {"name": "x.edf", "size": 1}).status_code, 404)

    def test_csrf_is_required_for_json_and_raw_upload(self):
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.users[UserRole.STAFF])
        url = self.url("create_recording", self.patient.pk)
        self.assertEqual(csrf.post(url, data='{"title":"x"}', content_type="application/json").status_code, 403)
        request = RequestFactory().get("/")
        token = get_token(request)
        csrf.cookies["csrftoken"] = request.META["CSRF_COOKIE"]
        self.assertEqual(csrf.post(url, data='{"title":"x"}', content_type="application/json", HTTP_X_CSRFTOKEN=token).status_code, 201)
        asset = begin_upload(self.recording, "x.edf", 1, "raw")
        url = self.url("upload_chunk", asset.pk) + "?offset=0"
        self.assertEqual(csrf.post(url, data=b"x", content_type="application/octet-stream").status_code, 403)
        self.assertEqual(csrf.post(url, data=b"x", content_type="application/octet-stream", HTTP_X_CSRFTOKEN=token).status_code, 200)

    def test_filename_extension_and_type_validation(self):
        for name in ("../x.edf", "a/x.edf", "a\\x.edf", "C:x.edf", ".", "x.exe", " x.edf", "x\x00.edf", "脑" * 90 + ".edf"):
            with self.subTest(name=name):
                self.assertEqual(self.post("create_upload", [self.recording.pk], {"name": name, "size": 1}).status_code, 400)
        for value in ([], {}, None):
            self.assertEqual(self.post("create_upload", [self.recording.pk], {"name": "x.edf", "size": 1, "role": value}).status_code, 400)
            self.assertEqual(self.post("create_recording", [self.patient.pk], {"title": "x", "modality": value}).status_code, 400)
        self.assertEqual(self.post("create_upload", [self.recording.pk], {"name": "x.gdf", "size": 1}).status_code, 201)

    def test_helper_upload_roles_match_engine_coordinate_formats(self):
        for suffix in (".csv", ".tsv", ".json", ".fif", ".fif.gz"):
            self.assertEqual(self.post("create_upload", [self.recording.pk], {"name": "electrodes" + suffix, "size": 1, "role": "electrodes"}).status_code, 201)
        for suffix in (".sfp", ".elc", ".xyz", ".txt"):
            self.assertEqual(self.post("create_upload", [self.recording.pk], {"name": "electrodes" + suffix, "size": 1, "role": "electrodes"}).status_code, 400)
        self.assertEqual(self.post("create_upload", [self.recording.pk], {"name": "transform.txt", "size": 1, "role": "trans"}).status_code, 400)
        for suffix in (".fif", ".fif.gz"):
            self.assertEqual(self.post("create_upload", [self.recording.pk], {"name": "head-trans" + suffix, "size": 1, "role": "trans"}).status_code, 201)

    def test_all_advertised_source_algorithms_resolve_engine_aliases(self):
        from .source_algorithms import _method
        from .engine import SOURCE_METHODS
        from .views import SOURCE_ALGORITHMS
        self.assertEqual(len(SOURCE_ALGORITHMS), 14)
        self.assertEqual({_method(name) for name in SOURCE_ALGORITHMS}, set(SOURCE_METHODS))

    def test_upload_initialization_is_idempotent_and_case_collision_conflicts(self):
        first = begin_upload(self.recording, "Same.edf", 3, "raw")
        self.assertEqual(begin_upload(self.recording, "Same.edf", 3, "raw").pk, first.pk)
        for name, size in (("same.edf", 3), ("Same.edf", 4)):
            with self.assertRaises(WorkspaceError) as error:
                begin_upload(self.recording, name, size, "raw")
            self.assertEqual(error.exception.status, 409)

    @override_settings(EEG_MAX_FILE_BYTES=4, EEG_PATIENT_QUOTA_BYTES=6)
    def test_file_size_and_patient_quota_count_reservations(self):
        for size in (0, -1, 5, True, "3"):
            with self.assertRaises(WorkspaceError):
                begin_upload(self.recording, "bad.edf", size, "raw")
        begin_upload(self.recording, "first.edf", 4, "raw")
        other = Recording.objects.create(patient=self.patient, title="other", modality="eeg")
        with self.assertRaises(WorkspaceError) as error:
            begin_upload(other, "second.edf", 3, "raw")
        self.assertEqual(error.exception.status, 413)

    def test_global_disk_reservations_and_append_floor(self):
        with patch("eeg.storage.shutil.disk_usage", return_value=Mock(free=9)):
            begin_upload(self.recording, "first.edf", 5, "raw")
            with self.assertRaises(WorkspaceError) as error:
                begin_upload(self.recording, "next.edf", 5, "raw")
            self.assertEqual(error.exception.status, 507)
        with patch("eeg.storage.shutil.disk_usage", return_value=Mock(free=0)):
            asset = self.recording.files.get(name="first.edf")
            with self.assertRaises(WorkspaceError):
                append_chunk(asset, 0, b"x")
        self.assertFalse(private_path(asset.relative_path + ".upload-part").exists())

    def test_offset_replay_returns_current_and_resume_uses_physical_bytes(self):
        asset = begin_upload(self.recording, "resume.edf", 4, "raw")
        url = self.url("upload_chunk", asset.pk) + "?offset=0"
        self.assertEqual(self.client.post(url, data=b"ab", content_type="application/octet-stream").json()["offset"], 2)
        replay = self.client.post(url, data=b"ab", content_type="application/octet-stream")
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(replay.json()["offset"], 2)
        EegAsset.objects.filter(pk=asset.pk).update(uploaded_size=0)
        self.assertEqual(self.client.get(self.url("upload_status", asset.pk)).json()["offset"], 2)
        self.assertEqual(self.client.post(self.url("finish_upload", asset.pk)).status_code, 409)
        self.assertEqual(append_chunk(asset, 2, b"cd"), 4)
        finalized = complete_upload(asset)
        self.assertEqual(finalized.sha256, hashlib.sha256(b"abcd").hexdigest())
        self.assertEqual(complete_upload(finalized).pk, asset.pk)
        with self.assertRaises(WorkspaceError):
            append_chunk(asset, 4, b"x")
        self.assertEqual(private_path(asset.relative_path).read_bytes(), b"abcd")

    def test_rename_crash_can_be_resumed_and_completed(self):
        asset = begin_upload(self.recording, "crash.edf", 3, "raw")
        append_chunk(asset, 0, b"abc")
        os.replace(private_path(asset.relative_path + ".upload-part"), private_path(asset.relative_path))
        self.assertEqual(self.client.get(self.url("upload_status", asset.pk)).json()["offset"], 3)
        self.assertTrue(complete_upload(asset).complete)

    @override_settings(EEG_PATIENT_QUOTA_BYTES=4)
    def test_discard_incomplete_upload_releases_quota_and_bytes(self):
        asset = begin_upload(self.recording, "abandoned.edf", 4, "raw")
        append_chunk(asset, 0, b"ab")
        path = private_path(asset.relative_path + ".upload-part")
        self.assertTrue(path.exists())
        response = self.client.post(self.url("discard_upload", asset.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "discarded")
        self.assertFalse(path.exists())
        self.assertFalse(EegAsset.objects.filter(pk=asset.pk).exists())
        self.assertEqual(self.post("create_upload", [self.recording.pk], {"name": "new.edf", "size": 4}).status_code, 201)
        with self.assertRaises(WorkspaceError) as error:
            append_chunk(asset, 2, b"cd")
        self.assertEqual(error.exception.status, 404)

    def test_discard_cannot_delete_completed_or_other_recording_files(self):
        completed = self.asset()
        incomplete = begin_upload(self.recording, "incomplete.edf", 3, "raw")
        other = Recording.objects.create(patient=self.patient, title="other", modality="eeg")
        separate = self.asset(recording=other, name="incomplete.edf", data=b"xyz")
        self.assertEqual(self.client.post(self.url("discard_upload", completed.pk)).status_code, 409)
        discard_upload(incomplete)
        self.assertEqual(private_path(separate.relative_path).read_bytes(), b"xyz")
        self.assertEqual(private_path(completed.relative_path).read_bytes(), b"synthetic")

    def test_upload_chunks_cannot_overrun_declared_or_request_limits(self):
        asset = begin_upload(self.recording, "tiny.edf", 1, "raw")
        self.assertEqual(self.client.post(self.url("upload_chunk", asset.pk) + "?offset=0", data=b"ab", content_type="application/octet-stream").status_code, 413)
        self.assertEqual(self.client.post(self.url("upload_chunk", asset.pk) + "?offset=-1", data=b"a", content_type="application/octet-stream").status_code, 413)
        with self.assertRaises(WorkspaceError):
            append_chunk(asset, 0, b"x" * (2 * 1024 ** 2 + 1))

    def test_same_basename_is_isolated_between_recordings(self):
        first = self.asset(data=b"first")
        other = Recording.objects.create(patient=self.patient, title="other", modality="eeg")
        second = self.asset(recording=other, data=b"second")
        self.assertNotEqual(first.relative_path, second.relative_path)
        self.assertEqual(private_path(first.relative_path).read_bytes(), b"first")
        self.assertEqual(private_path(second.relative_path).read_bytes(), b"second")

    def test_brainvision_header_rejects_escaping_references(self):
        data = b"[Common Infos]\nDataFile=../outside.eeg\n"
        asset = begin_upload(self.recording, "unsafe.vhdr", len(data), "raw")
        append_chunk(asset, 0, data)
        with self.assertRaises(WorkspaceError):
            complete_upload(asset)
        asset.refresh_from_db()
        self.assertFalse(asset.complete)

    def test_brainvision_companions_must_exist_in_same_recording(self):
        self.asset(name="safe.vhdr", data=b"DataFile=safe.eeg\nMarkerFile=safe.vmrk\n")
        other = Recording.objects.create(patient=self.patient, title="other", modality="eeg")
        self.asset(recording=other, name="safe.eeg", role="companion")
        with self.assertRaises(WorkspaceError):
            snapshot_files(self.recording)
        self.asset(name="safe.eeg", role="companion")
        self.asset(name="safe.vmrk", role="companion", data=b"DataFile=safe.eeg\n")
        self.assertEqual(len(snapshot_files(self.recording)), 3)

    def test_private_storage_rejects_public_roots_and_traversal(self):
        with self.assertRaises(WorkspaceError):
            private_path("../public")
        with override_settings(MEDIA_ROOT=self.root):
            with self.assertRaises(ImproperlyConfigured):
                storage_root()

    def test_job_snapshots_are_immutable_and_new_assets_are_not_added(self):
        job = self.job()
        self.assertEqual(job.request["files"][0]["sha256"], hashlib.sha256(b"synthetic").hexdigest())
        self.assertEqual(job.parameters["n_jobs"], 1)
        self.asset(name="later.csv", role="electrodes")
        job.refresh_from_db()
        self.assertEqual(len(job.request["files"]), 1)

    def test_job_parameters_reject_paths_code_nonfinite_and_wrong_types(self):
        self.asset()
        for parameters in ({"path": "/tmp/x"}, {"file": "../x"}, {"command": "rm"}, {"snr": float("nan")}, {"snr": 10 ** 1000}, [], {"x": ["a"] * 1025}):
            with self.subTest(parameters_type=type(parameters)):
                response = self.post("create_job", [self.recording.pk], {"operation": "source", "parameters": parameters})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.post("create_job", [self.recording.pk], {"operation": []}).status_code, 400)

    @override_settings(EEG_MAX_QUEUED_JOBS=2)
    def test_job_queue_limit_and_independent_lanes(self):
        preview = self.job("inspect")
        analysis = self.job("psd")
        with self.assertRaises(WorkspaceError) as error:
            self.job("waveform")
        self.assertEqual(error.exception.status, 429)
        self.assertEqual(claim_next("analysis").pk, analysis.pk)
        self.assertEqual(claim_next("preview").pk, preview.pk)
        self.assertIsNone(claim_next("preview"))

    def test_queued_cancel_is_terminal_and_running_cancel_is_requested(self):
        job = self.job()
        self.assertEqual(cancel(job).status, "cancelled")
        self.assertIsNone(claim_next("preview"))
        running = self.job()
        running = claim_next("preview")
        cancelled = cancel(running)
        self.assertTrue(cancelled.cancel_requested)
        self.assertEqual(cancelled.status, "running")

    def test_restart_recovery_never_kills_unrelated_pid(self):
        job = self.job()
        AnalysisJob.objects.filter(pk=job.pk).update(status="running", process_pid=12345, process_start="old")
        with patch("eeg.queue.process_identity", return_value="different"), patch("eeg.queue.os.killpg") as kill:
            recover_interrupted("preview")
        kill.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIsNone(job.process_pid)

    def test_restart_recovery_preserves_cancelled_intent(self):
        job = self.job()
        AnalysisJob.objects.filter(pk=job.pk).update(status="running", cancel_requested=True)
        recover_interrupted("preview")
        job.refresh_from_db()
        self.assertEqual(job.status, "cancelled")

    def test_state_omits_large_results_and_job_detail_preserves_parameters(self):
        job = self.job()
        job.status = "succeeded"
        job.result = {"waveform": [1] * 2000, "artifacts": [{"name": "plot.png"}]}
        job.save(update_fields=["status", "result"])
        with CaptureQueriesContext(connection) as queries:
            state = self.client.get(self.url("state", self.patient.pk)).json()
        self.assertEqual(state["recordings"][0]["jobs"][0]["result"], {})
        selected = [item["sql"] for item in queries if 'FROM "eeg_analysisjob"' in item["sql"]]
        self.assertEqual(len(selected), 1)
        self.assertNotIn('"result"', selected[0])
        self.assertNotIn('"request"', selected[0])
        full = self.client.get(self.url("job_status", job.pk)).json()
        self.assertEqual(full["result"]["waveform"], [1] * 2000)
        self.assertEqual(full["parameters"]["n_jobs"], 1)

    def test_preview_expiry_preserves_last_five_new_previews_and_scientific_results(self):
        old = timezone.now() - timedelta(days=2)
        jobs = []
        for index in range(8):
            job = AnalysisJob.objects.create(recording=self.recording, operation="waveform", lane="preview", status="succeeded", finished_at=old)
            AnalysisJob.objects.filter(pk=job.pk).update(created_at=old + timedelta(minutes=index))
            (job_directory(job.pk) / "result.json").write_text("{}")
            jobs.append(job)
        current = AnalysisJob.objects.create(recording=self.recording, operation="waveform", lane="preview", status="succeeded", finished_at=timezone.now())
        inspect = AnalysisJob.objects.create(recording=self.recording, operation="inspect", lane="preview", status="succeeded", finished_at=old)
        source = AnalysisJob.objects.create(recording=self.recording, operation="source", lane="analysis", status="succeeded", finished_at=old)
        active = AnalysisJob.objects.create(recording=self.recording, operation="waveform", lane="preview", status="running", finished_at=old)
        self.assertEqual(cleanup_waveform_previews(), 4)
        self.assertEqual(set(AnalysisJob.objects.filter(operation="waveform", status="succeeded").values_list("id", flat=True)), {current.pk, *[job.pk for job in jobs[-4:]]})
        for job in jobs[:4]:
            self.assertFalse((self.root / "jobs" / str(job.pk)).exists())
        for job in (inspect, source, active):
            self.assertTrue(AnalysisJob.objects.filter(pk=job.pk).exists())

    def test_artifacts_require_listed_successful_result_and_stay_private(self):
        job = self.job()
        directory = job_directory(job.pk)
        (directory / "plot.png").write_bytes(b"synthetic png")
        (directory / "worker-private.log").write_text("private")
        job.status = "succeeded"
        job.result = {"artifacts": [{"name": "plot.png", "kind": "image"}]}
        job.save(update_fields=["status", "result"])
        response = self.client.get(self.url("artifact", job.pk, "plot.png"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertIn("inline", response["Content-Disposition"])
        self.assertEqual(response["Cache-Control"], "private, no-store")
        response.close()
        response = self.client.get(self.url("artifact", job.pk, "plot.png") + "?download=1")
        self.assertIn("attachment", response["Content-Disposition"])
        response.close()
        self.assertEqual(self.client.get(self.url("artifact", job.pk, "worker-private.log")).status_code, 404)
        self.assertEqual(self.client.get(self.url("artifact", job.pk, "plot.png").replace("/plot.png/", "/../plot.png/")).status_code, 404)

    def test_nonfinite_results_and_unlisted_symlink_paths_are_rejected(self):
        job = self.job()
        directory = job_directory(job.pk)
        result = directory / "result.json"
        result.write_text('{"value":NaN}', encoding="utf-8")
        with self.assertRaises(ValueError):
            _validated_result(job, directory)
        result.write_text('{"artifacts":[{"name":"../plot.png"}]}', encoding="utf-8")
        with self.assertRaises(WorkspaceError):
            _validated_result(job, directory)
        result.write_text('{"artifacts":[{"name":"unsafe.html"}]}', encoding="utf-8")
        (directory / "unsafe.html").write_text("x")
        with self.assertRaises(WorkspaceError):
            _validated_result(job, directory)

    def test_db_retry_only_retries_transient_locks(self):
        call = Mock(side_effect=[OperationalError("database is locked"), 12])
        with patch("eeg.queue.time.sleep"):
            self.assertEqual(db_retry(call), 12)
        self.assertEqual(call.call_count, 2)
        with self.assertRaises(OperationalError):
            db_retry(Mock(side_effect=OperationalError("no such table")))

    def test_worker_success_uses_isolated_credentials_and_metadata(self):
        job = self.job("inspect")
        job = claim_next("preview")
        directory = job_directory(job.pk)
        process = Mock(returncode=0, pid=99999999)
        process.poll.return_value = 0
        def start(*args, **kwargs):
            (directory / "result.json").write_text(json.dumps({"metadata": {"sfreq": 100}, "artifacts": []}), encoding="utf-8")
            return process
        with patch.dict(os.environ, {"DJANGO_SECRET_KEY": "synthetic secret", "PROVIDER_API_KEY": "synthetic key", "EEG_SUBJECTS_DIR": "/tmp/synthetic-subjects"}), patch("eeg.queue.subprocess.Popen", side_effect=start) as launch:
            run_job(job)
        environment = launch.call_args.kwargs["env"]
        self.assertNotIn("DJANGO_SECRET_KEY", environment)
        self.assertNotIn("PROVIDER_API_KEY", environment)
        self.assertEqual(environment["EEG_SUBJECTS_DIR"], "/tmp/synthetic-subjects")
        self.assertEqual(environment["OMP_NUM_THREADS"], "1")
        job.refresh_from_db()
        self.recording.refresh_from_db()
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(self.recording.metadata, {"sfreq": 100})

    def test_worker_missing_or_invalid_result_never_succeeds(self):
        job = self.job()
        job = claim_next("preview")
        process = Mock(returncode=0, pid=99999999)
        process.poll.return_value = 0
        with patch("eeg.queue.subprocess.Popen", return_value=process):
            run_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.result, {})

    def test_cancellation_before_result_publication_wins(self):
        job = self.job()
        job = claim_next("preview")
        process = Mock(returncode=0, pid=99999999)
        process.poll.return_value = 0
        def validation(*args):
            cancel(job)
            return {"artifacts": [], "data": [1, 2, 3]}
        with patch("eeg.queue.subprocess.Popen", return_value=process), patch("eeg.queue._validated_result", side_effect=validation):
            run_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(job.result, {})

    def test_worker_running_cancellation_terminates_child_and_does_not_publish(self):
        job = self.job()
        job = claim_next("preview")
        cancel(job)
        process = Mock(returncode=0, pid=99999999)
        process.poll.return_value = None
        with patch("eeg.queue.subprocess.Popen", return_value=process), patch("eeg.queue._terminate") as terminate:
            run_job(job)
        terminate.assert_called_once_with(process)
        job.refresh_from_db()
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(job.result, {})

    @override_settings(EEG_JOB_TIMEOUT=1)
    def test_worker_timeout_terminates_child(self):
        job = self.job()
        job = claim_next("preview")
        process = Mock(returncode=0, pid=99999999)
        process.poll.return_value = None
        with patch("eeg.queue.subprocess.Popen", return_value=process), patch("eeg.queue._terminate") as terminate, patch("eeg.queue.time.monotonic", side_effect=[0, 2]):
            run_job(job)
        self.assertTrue(terminate.called)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIn("时限", job.error)

    def test_worker_disk_floor_prevents_launch(self):
        job = self.job()
        job = claim_next("preview")
        with patch("eeg.queue.require_disk_space", side_effect=WorkspaceError("disk full", 507)), patch("eeg.queue.subprocess.Popen") as launch:
            run_job(job)
        launch.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")

    @override_settings(EEG_JOB_TIMEOUT=1)
    def test_real_worker_timeout_reaps_child_and_clears_pid(self):
        job = self.job()
        job = claim_next("preview")
        launches = []
        popen = subprocess.Popen
        def start(*args, **kwargs):
            process = popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
            launches.append(process)
            return process
        with patch("eeg.queue.subprocess.Popen", side_effect=start):
            run_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIn("时限", job.error)
        self.assertIsNone(job.process_pid)
        self.assertEqual(job.process_start, "")
        self.assertIsNotNone(launches[0].poll())
        self.assertEqual(job.result, {})
        self.assertEqual(private_path(self.recording.files.get(role="raw").relative_path).read_bytes(), b"synthetic")

    @override_settings(EEG_JOB_TIMEOUT=60)
    def test_real_mne_child_inspects_and_renders_synthetic_fif(self):
        import mne
        import numpy as np
        mne.set_log_level("ERROR")
        times = np.arange(1000) / 100
        data = np.stack([np.sin(2 * np.pi * (8 + index) * times) for index in range(4)]) * 1e-5
        raw = mne.io.RawArray(data, mne.create_info(["Fp1", "Fp2", "C3", "C4"], 100, "eeg"), verbose=False)
        raw.set_montage("standard_1020")
        fixture = self.root / "synthetic-raw.fif"
        raw.save(fixture, overwrite=True, verbose=False)
        self.asset(name=fixture.name, data=fixture.read_bytes())
        for operation in ("inspect", "waveform", "psd"):
            with self.subTest(operation=operation):
                self.recording.refresh_from_db()
                job = enqueue(self.recording, operation, {"duration": 5}, self.users[UserRole.STAFF])
                job = claim_next("preview" if operation in {"inspect", "waveform"} else "analysis")
                run_job(job)
                job.refresh_from_db()
                traceback = job_directory(job.pk) / "traceback-private.txt"
                diagnostic = traceback.read_text() if traceback.exists() else ""
                self.assertEqual(job.status, "succeeded", job.error + "\n" + diagnostic)
                self.assertEqual(job.progress, 100)
                json.dumps(job.result, allow_nan=False)
                if operation == "inspect":
                    self.recording.refresh_from_db()
                    self.assertEqual(self.recording.metadata["sfreq"], 100)
                elif operation == "waveform":
                    self.assertEqual(len(job.result["data_uv"]), 4)
                    self.assertTrue(job.result["times"])
                else:
                    self.assertTrue(job.result["artifacts"])
                    for artifact in job.result["artifacts"]:
                        self.assertTrue((job_directory(job.pk) / artifact["name"]).is_file())
