from functools import wraps
import json
import mimetypes

from django.core.exceptions import RequestDataTooBig
from django.http import FileResponse, JsonResponse
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from epilepsy.mixins import role_required
from epilepsy.models import Patient, UserRole
from .models import AnalysisJob, EegAsset, Recording
from .queue import _reject_constant, cancel, enqueue, job_directory
from .storage import WorkspaceError, append_chunk, begin_upload, complete_upload, discard_upload, private_path, safe_name, upload_offset


READ_ROLES = (UserRole.ADMIN, UserRole.STAFF, UserRole.GUEST)
WRITE_ROLES = (UserRole.ADMIN, UserRole.STAFF)
SOURCE_ALGORITHMS = ["MNE", "dSPM", "sLORETA", "eLORETA", "LCMV", "DICS", "dipole", "rap_music", "trap_music", "mixed_norm", "ir_mxne", "tf_mixed_norm", "ir_tf_mixed_norm", "gamma_map"]


def api_errors(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except WorkspaceError as exc:
            return JsonResponse({"error": str(exc), **exc.details}, status=exc.status)
        except RequestDataTooBig:
            return JsonResponse({"error": "上传的数据块过大，请分块上传。"}, status=413)
    return wrapped


def _json(request):
    if len(request.body) > 128 * 1024:
        raise WorkspaceError("请求数据过大。", 413)
    try:
        data = json.loads(request.body.decode("utf-8"), parse_constant=_reject_constant)
        json.dumps(data, ensure_ascii=False).encode("utf-8")
    except (UnicodeError, ValueError, RecursionError):
        raise WorkspaceError("请提交有效的 JSON 数据。") from None
    if not isinstance(data, dict):
        raise WorkspaceError("请求必须是 JSON 对象。")
    return data


def _can_write(user):
    return user.profile.role in WRITE_ROLES


def _job_json(job, *, full=True):
    result = job.result if full and job.status == "succeeded" else {}
    payload = {
        "id": str(job.pk), "operation": job.operation, "status": job.status,
        "progress": job.progress, "message": job.message, "result": result,
        "error": job.error, "cancel_requested": job.cancel_requested,
        "created_at": job.created_at.isoformat(),
    }
    if full:
        payload["parameters"] = job.parameters
    return payload


def _asset_json(asset):
    offset = upload_offset(asset)
    return {"id": str(asset.pk), "name": asset.name, "role": asset.role, "size": asset.size,
            "complete": asset.complete, "offset": offset, "uploaded_size": offset}


@role_required(*READ_ROLES)
@require_GET
def workspace(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    return render(request, "eeg/workspace.html", {"patient": patient, "can_write": _can_write(request.user)})


@role_required(*READ_ROLES)
@require_GET
@api_errors
def state(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    rows = []
    jobs = Prefetch("jobs", queryset=AnalysisJob.objects.defer("result", "request", "parameters"))
    for recording in Recording.objects.filter(patient=patient).prefetch_related("files", jobs):
        rows.append({
            "id": str(recording.pk), "title": recording.title, "modality": recording.modality,
            "status": recording.status, "metadata": recording.metadata,
            "primary_file": str(recording.primary_file_id) if recording.primary_file_id else None,
            "files": [_asset_json(asset) for asset in recording.files.all()],
            "jobs": [_job_json(job, full=False) for job in recording.jobs.all()],
        })
    return JsonResponse({"recordings": rows, "can_write": _can_write(request.user),
                         "algorithms": SOURCE_ALGORITHMS})


@role_required(*WRITE_ROLES)
@require_POST
@api_errors
def create_recording(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    data = _json(request)
    title = data.get("title", "")
    modality = data.get("modality", "eeg")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200 or not isinstance(modality, str) or modality not in {"eeg", "seeg"}:
        raise WorkspaceError("请填写记录名称并选择 EEG 或 sEEG。")
    recording = Recording.objects.create(patient=patient, title=title.strip(), modality=modality, created_by=request.user)
    return JsonResponse({"id": str(recording.pk), "title": recording.title, "modality": recording.modality}, status=201)


@role_required(*WRITE_ROLES)
@require_POST
@api_errors
def create_upload(request, recording_id):
    recording = get_object_or_404(Recording, pk=recording_id)
    data = _json(request)
    asset = begin_upload(recording, data.get("name"), data.get("size"), data.get("role", "raw"))
    return JsonResponse(_asset_json(asset), status=201)


@role_required(*READ_ROLES)
@require_GET
@api_errors
def upload_status(request, upload_id):
    asset = get_object_or_404(EegAsset, pk=upload_id)
    return JsonResponse(_asset_json(asset))


@role_required(*WRITE_ROLES)
@require_POST
@api_errors
def upload_chunk(request, upload_id):
    asset = get_object_or_404(EegAsset, pk=upload_id)
    try:
        offset = int(request.GET.get("offset", ""))
        length = int(request.META.get("CONTENT_LENGTH") or "0")
    except ValueError:
        raise WorkspaceError("上传位置无效。") from None
    if offset < 0 or length > 2 * 1024 ** 2:
        raise WorkspaceError("上传位置或数据块大小无效。", 413)
    return JsonResponse({"id": str(asset.pk), "offset": append_chunk(asset, offset, request.body)})


@role_required(*WRITE_ROLES)
@require_POST
@api_errors
def finish_upload(request, upload_id):
    asset = complete_upload(get_object_or_404(EegAsset, pk=upload_id))
    return JsonResponse({**_asset_json(asset), "sha256": asset.sha256})


@role_required(*WRITE_ROLES)
@require_POST
@api_errors
def discard(request, upload_id):
    discard_upload(get_object_or_404(EegAsset.objects.select_related("recording"), pk=upload_id))
    return JsonResponse({"id": str(upload_id), "status": "discarded"})


@role_required(*WRITE_ROLES)
@require_POST
@api_errors
def create_job(request, recording_id):
    recording = get_object_or_404(Recording.objects.select_related("primary_file"), pk=recording_id)
    data = _json(request)
    job = enqueue(recording, data.get("operation"), data.get("parameters", {}), request.user)
    return JsonResponse(_job_json(job), status=202)


@role_required(*READ_ROLES)
@require_GET
def job_status(request, job_id):
    return JsonResponse(_job_json(get_object_or_404(AnalysisJob, pk=job_id)))


@role_required(*WRITE_ROLES)
@require_POST
def cancel_job(request, job_id):
    return JsonResponse(_job_json(cancel(get_object_or_404(AnalysisJob, pk=job_id))))


@role_required(*READ_ROLES)
@require_GET
@api_errors
def artifact(request, job_id, name):
    job = get_object_or_404(AnalysisJob, pk=job_id, status="succeeded")
    name = safe_name(name)
    allowed = {item.get("name") for item in job.result.get("artifacts", []) if isinstance(item, dict)}
    if name not in allowed:
        raise WorkspaceError("分析成果不存在。", 404)
    path = (job_directory(job.pk) / name).resolve()
    if not path.is_relative_to(job_directory(job.pk)) or not path.is_file() or (job_directory(job.pk) / name).is_symlink():
        raise WorkspaceError("分析成果不存在。", 404)
    kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
    response = FileResponse(path.open("rb"), filename=name, content_type=kind,
                            as_attachment=request.GET.get("download") == "1" or not kind.startswith("image/"))
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@role_required(*READ_ROLES)
@require_GET
@api_errors
def download_file(request, file_id):
    asset = get_object_or_404(EegAsset, pk=file_id, complete=True)
    path = private_path(asset.relative_path)
    if not path.is_file():
        raise WorkspaceError("文件不存在。", 404)
    response = FileResponse(path.open("rb"), as_attachment=True, filename=asset.name)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
