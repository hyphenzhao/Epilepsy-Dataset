"""Durable database queue with one isolated scientific process per lane."""

import json
from datetime import timedelta
import math
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

from django.conf import settings
from django.db import close_old_connections, OperationalError, transaction
from django.utils import timezone

from .models import AnalysisJob, Recording
from .storage import WorkspaceError, locked, private_path, require_disk_space, safe_name, snapshot_files


OPERATIONS = {name for name, _ in AnalysisJob.OPERATIONS}
ACTIVE_STATES = ("queued", "running")
ARTIFACT_SUFFIXES = (".png", ".jpg", ".jpeg", ".json", ".csv", ".tsv", ".txt", ".npz", ".npy", ".fif", ".fif.gz", ".stc")


def db_retry(call):
    for attempt in range(4):
        try:
            return call()
        except OperationalError as exc:
            if attempt == 3 or not any(word in str(exc).lower() for word in ("locked", "busy")):
                raise
            close_old_connections()
            time.sleep(0.2 * (attempt + 1))


def _reject_constant(value):
    raise ValueError("nonfinite JSON")


def _validate_json(value, depth=0):
    if depth > 6:
        raise WorkspaceError("分析参数层级过多。")
    if isinstance(value, dict):
        if len(value) > 100:
            raise WorkspaceError("分析参数过多。")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 80 or key.lower() in {"path", "file_path", "output_dir", "input_json", "output_json", "command", "executable", "script", "code"}:
                raise WorkspaceError("分析参数不能指定服务器路径或可执行内容。")
            _validate_json(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 1024:
            raise WorkspaceError("分析参数列表过长。")
        for item in value:
            _validate_json(item, depth + 1)
    elif isinstance(value, str):
        if len(value) > 4000 or "\x00" in value or "/" in value or "\\" in value:
            raise WorkspaceError("分析参数不能包含文件路径；引用上传文件时请使用文件名。")
    elif isinstance(value, (float, int)) and not isinstance(value, bool):
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise WorkspaceError("分析参数必须是有限数值。")
    elif value is not None and not isinstance(value, bool):
        raise WorkspaceError("分析参数类型无效。")


def enqueue(recording, operation, parameters, user):
    if not isinstance(operation, str) or operation not in OPERATIONS or not isinstance(parameters, dict):
        raise WorkspaceError("分析类型或参数无效。")
    _validate_json(parameters)
    if len(json.dumps(parameters, ensure_ascii=False).encode()) > 64 * 1024:
        raise WorkspaceError("分析参数过大。")
    with locked(f"patient-{recording.patient_id}"):
        count = AnalysisJob.objects.filter(recording__patient_id=recording.patient_id, status__in=ACTIVE_STATES).count()
        if count >= getattr(settings, "EEG_MAX_QUEUED_JOBS", 4):
            raise WorkspaceError("该患者已有多个分析任务，请等待完成后再提交。", 429)
        files = snapshot_files(recording)
        parameters = dict(parameters)
        parameters["n_jobs"] = 1
        if recording.primary_file_id:
            parameters.setdefault("primary_file", recording.primary_file.name)
        return AnalysisJob.objects.create(
            recording=recording, operation=operation,
            lane="preview" if operation in {"inspect", "waveform"} else "analysis",
            parameters=parameters,
            request={"operation": operation, "modality": recording.modality, "files": files, "parameters": parameters},
            created_by=user,
        )


def cancel(job):
    now = timezone.now()
    changed = AnalysisJob.objects.filter(pk=job.pk, status="queued").update(
        status="cancelled", cancel_requested=True, message="任务已取消", finished_at=now,
    )
    if not changed:
        AnalysisJob.objects.filter(pk=job.pk, status="running").update(cancel_requested=True, message="正在取消任务")
    job.refresh_from_db()
    return job


def job_directory(job_id):
    path = private_path(f"jobs/{job_id}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".temporary")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def process_identity(pid):
    try:
        # Linux start ticks distinguish a recorded process from PID reuse.
        text = Path(f"/proc/{pid}/stat").read_text()
        return text[text.rfind(")") + 2:].split()[19]
    except (OSError, IndexError):
        return ""


def _known_orphan(job):
    if not job.process_pid or not job.process_start or process_identity(job.process_pid) != job.process_start:
        return False
    try:
        arguments = Path(f"/proc/{job.process_pid}/cmdline").read_bytes().split(b"\x00")
        return b"eeg.engine" in arguments and str(job_directory(job.pk) / "input.json").encode() in arguments
    except OSError:
        return False


def _terminate(process):
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            return
        process.wait(timeout=5)


def recover_interrupted(lane):
    """Run only while holding the lane's exclusive worker lock."""
    for job in AnalysisJob.objects.filter(lane=lane, status="running"):
        if _known_orphan(job):
            try:
                os.killpg(job.process_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 3
            while _known_orphan(job) and time.monotonic() < deadline:
                time.sleep(0.1)
            if _known_orphan(job):
                try:
                    os.killpg(job.process_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        AnalysisJob.objects.filter(pk=job.pk, status="running").update(
            status="cancelled" if job.cancel_requested else "failed",
            message="任务已取消" if job.cancel_requested else "任务因服务重启中断，请重新提交",
            error="" if job.cancel_requested else "任务运行被中断，未标记为完成。",
            process_pid=None, process_start="", finished_at=timezone.now(),
        )


def claim_next(lane):
    for _ in range(4):
        job = AnalysisJob.objects.filter(lane=lane, status="queued").order_by("created_at").first()
        if job is None:
            return None
        if AnalysisJob.objects.filter(pk=job.pk, status="queued", cancel_requested=False).update(
            status="running", progress=3, message="正在加载记录", started_at=timezone.now(),
        ):
            job.refresh_from_db()
            return job
    return None


def cleanup_waveform_previews():
    """Expire regenerable previews after 24h, retaining each recording's last five."""
    cutoff = timezone.now() - timedelta(hours=24)
    root = private_path("jobs")
    kept = {}
    removed = 0
    rows = AnalysisJob.objects.filter(operation="waveform", status="succeeded").only("id", "recording_id", "finished_at").order_by("recording_id", "-created_at")
    for job in rows.iterator():
        kept[job.recording_id] = kept.get(job.recording_id, 0) + 1
        if kept[job.recording_id] <= 5 or not job.finished_at or job.finished_at >= cutoff:
            continue
        target = root / str(job.pk)
        if target.is_symlink() or target.resolve().parent != root:
            continue
        try:
            if target.exists():
                shutil.rmtree(target)
        except OSError:
            continue
        deleted, _ = AnalysisJob.objects.filter(pk=job.pk, operation="waveform", status="succeeded").delete()
        removed += deleted
    return removed


def _limits(lane):
    def configure():
        import resource
        memory = getattr(settings, "EEG_PREVIEW_MEMORY_BYTES", 2 * 1024 ** 3) if lane == "preview" else getattr(settings, "EEG_ANALYSIS_MEMORY_BYTES", 5 * 1024 ** 3)
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        cpu = int(getattr(settings, "EEG_JOB_TIMEOUT", 900)) + 10
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        file_size = getattr(settings, "EEG_MAX_OUTPUT_FILE_BYTES", 512 * 1024 ** 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_size, file_size))
        os.umask(0o077)
    return configure if os.name == "posix" else None


def _validated_result(job, directory):
    path = directory / "result.json"
    if not path.is_file() or path.stat().st_size > 16 * 1024 ** 2:
        raise WorkspaceError("分析程序没有返回有效结果。")
    result = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    if not isinstance(result, dict) or result.get("status") == "failed" or result.get("error"):
        message = str(result.get("error", "分析失败，请检查文件和参数。")) if isinstance(result, dict) else "分析结果格式无效。"
        raise WorkspaceError(message[:1000])
    artifacts = result.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise WorkspaceError("分析成果列表格式无效。")
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise WorkspaceError("分析成果格式无效。")
        name = safe_name(artifact.get("name"))
        path = (directory / name).resolve()
        if not name.lower().endswith(ARTIFACT_SUFFIXES) or not path.is_relative_to(directory.resolve()) or not path.is_file() or (directory / name).is_symlink():
            raise WorkspaceError("分析成果路径无效。")
    return result


def _publish_result(job, result):
    with transaction.atomic():
        published = AnalysisJob.objects.filter(pk=job.pk, status="running", cancel_requested=False).update(
            status="succeeded", progress=100, message="分析完成", result=result, error="",
            process_pid=None, process_start="", finished_at=timezone.now(),
        )
        if published and job.operation == "inspect":
            Recording.objects.filter(pk=job.recording_id).update(metadata=result.get("metadata", result), status="ready")
        return published


def run_job(job):
    directory = job_directory(job.pk)
    environment = {key: value for key, value in os.environ.items() if key in {"PATH", "PYTHONPATH", "VIRTUAL_ENV", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP", "MPLCONFIGDIR", "XDG_CACHE_HOME"} or (key.startswith(("EEG_", "MNE_")) and not any(part in key for part in ("KEY", "SECRET", "TOKEN", "PASSWORD")))}
    environment.update({name: "1" for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS", "OMP_THREAD_LIMIT", "NUMEXPR_NUM_THREADS")})
    environment["MPLBACKEND"] = "Agg"
    if getattr(settings, "EEG_SUBJECTS_DIR", None):
        environment.setdefault("EEG_SUBJECTS_DIR", str(settings.EEG_SUBJECTS_DIR))
    process = None
    try:
        require_disk_space(jobs=True)
        atomic_json(directory / "input.json", job.request)
        with (directory / "worker-private.log").open("ab") as log:
            process = subprocess.Popen(
                [getattr(settings, "EEG_ENGINE_PYTHON", sys.executable), "-m", "eeg.engine", str(directory / "input.json"), str(directory / "result.json")],
                cwd=settings.BASE_DIR, env=environment, stdout=log, stderr=log,
                start_new_session=os.name == "posix", preexec_fn=_limits(job.lane),
            )
            db_retry(lambda: AnalysisJob.objects.filter(pk=job.pk, status="running").update(process_pid=process.pid, process_start=process_identity(process.pid)))
            started = time.monotonic()
            cancelled = False
            while process.poll() is None:
                close_old_connections()
                current = db_retry(lambda: AnalysisJob.objects.filter(pk=job.pk).values("cancel_requested", "status").first())
                if current is None or current["cancel_requested"]:
                    cancelled = True
                    _terminate(process)
                    break
                if time.monotonic() - started > getattr(settings, "EEG_JOB_TIMEOUT", 900):
                    _terminate(process)
                    raise WorkspaceError("任务超过运行时限，请缩短分析时间窗或降低计算规模。")
                require_disk_space(jobs=True)
                progress_file = directory / "progress.json"
                if progress_file.is_file() and progress_file.stat().st_size <= 8192:
                    try:
                        progress = json.loads(progress_file.read_text(encoding="utf-8"), parse_constant=_reject_constant)
                        if not isinstance(progress, dict):
                            raise ValueError("invalid progress")
                        percentage = max(3, min(99, int(progress.get("progress", 3))))
                        db_retry(lambda: AnalysisJob.objects.filter(pk=job.pk, status="running").update(progress=percentage, message=str(progress.get("message", "正在分析"))[:300]))
                    except (ValueError, TypeError, OSError):
                        pass
                time.sleep(0.4)
            current = db_retry(lambda: AnalysisJob.objects.filter(pk=job.pk).values("cancel_requested").first())
            if cancelled or current is None or current["cancel_requested"]:
                db_retry(lambda: AnalysisJob.objects.filter(pk=job.pk).update(status="cancelled", message="任务已取消", finished_at=timezone.now(), process_pid=None, process_start=""))
                return
        result = _validated_result(job, directory)
        if process.returncode:
            raise WorkspaceError("分析进程异常结束，请检查输入或缩小计算规模。")
        published = db_retry(lambda: _publish_result(job, result))
        if not published:
            db_retry(lambda: AnalysisJob.objects.filter(pk=job.pk, status="running", cancel_requested=True).update(
                status="cancelled", message="任务已取消", finished_at=timezone.now(), process_pid=None, process_start="",
            ))
            return
    except Exception as exc:
        if process is not None and process.poll() is None:
            _terminate(process)
        message = str(exc)[:1000] if isinstance(exc, WorkspaceError) else "分析程序运行失败，请检查输入或联系管理员。"
        db_retry(lambda: AnalysisJob.objects.filter(pk=job.pk).update(
            status="failed", message="分析失败", error=message, process_pid=None, process_start="", finished_at=timezone.now(),
        ))
