from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import shutil

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import F, Sum

from .models import EegAsset, Recording


class WorkspaceError(Exception):
    def __init__(self, message, status=400, **details):
        super().__init__(message)
        self.status = status
        self.details = details


EXTENSIONS = {
    "raw": (".edf", ".bdf", ".gdf", ".fif", ".fif.gz", ".vhdr", ".set", ".cnt"),
    "companion": (".eeg", ".vmrk", ".fdt", ".dat", ".fif", ".fif.gz"),
    "forward": (".fif", ".fif.gz"), "covariance": (".fif", ".fif.gz"),
    "trans": (".fif", ".fif.gz"), "bem": (".fif", ".fif.gz"),
    "src": (".fif", ".fif.gz"),
    "electrodes": (".csv", ".tsv", ".json", ".fif", ".fif.gz"),
    "mri": (".nii", ".nii.gz", ".mgz", ".mgh"),
}


def storage_root():
    root = Path(getattr(settings, "EEG_STORAGE_ROOT", settings.BASE_DIR.parent / "eeg_data")).resolve()
    for public in (getattr(settings, "MEDIA_ROOT", ""), getattr(settings, "STATIC_ROOT", "")):
        if public and root.is_relative_to(Path(public).resolve()):
            raise ImproperlyConfigured("EEG 文件目录必须位于公开媒体和静态文件目录之外。")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def private_path(relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise WorkspaceError("文件路径无效。")
    root = storage_root()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise WorkspaceError("文件路径无效。")
    return target


def safe_name(name):
    try:
        valid_length = isinstance(name, str) and 0 < len(name.encode("utf-8")) <= 240
    except UnicodeError:
        valid_length = False
    if (
        not valid_length or name != name.strip()
        or name in {".", ".."} or any(c in name for c in '/\\:\x00<>"|?*')
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
    ):
        raise WorkspaceError("文件名必须是原始文件的名称，不能包含目录、路径或特殊控制字符。")
    return name


def require_disk_space(additional=0, *, jobs=False):
    reserve = getattr(settings, "EEG_JOB_DISK_RESERVE_BYTES" if jobs else "EEG_UPLOAD_DISK_RESERVE_BYTES", (1 if jobs else 2) * 1024 ** 3)
    if shutil.disk_usage(storage_root()).free < reserve + additional:
        raise WorkspaceError("服务器存储空间不足，请联系管理员清理后重试。", 507)


@contextmanager
def locked(name, *, blocking=True):
    path = private_path("locks/" + name + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+b") as handle:
        try:
            import fcntl
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), flags)
            unlock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ImportError:
            import msvcrt
            handle.seek(0)
            if not handle.read(1):
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            unlock = lambda: (handle.seek(0), msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1))
        try:
            yield
        finally:
            unlock()


def begin_upload(recording, name, size, role):
    name = safe_name(name)
    if not isinstance(role, str) or role not in EXTENSIONS or not name.lower().endswith(EXTENSIONS[role]):
        raise WorkspaceError("该文件扩展名不适用于所选用途。")
    maximum = getattr(settings, "EEG_MAX_FILE_BYTES", 1024 ** 3)
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= maximum:
        raise WorkspaceError("文件大小无效，单文件上限为 1 GB。")
    with locked("storage-reservations"), locked(f"patient-{recording.patient_id}"):
        existing = recording.files.filter(name_key=name.casefold()).first()
        if existing:
            if (existing.name, existing.size, existing.role) != (name, size, role):
                raise WorkspaceError("同一记录中已存在同名文件，请保留原文件或创建新记录。", 409)
            return existing
        used = EegAsset.objects.filter(recording__patient_id=recording.patient_id).aggregate(value=Sum("size"))["value"] or 0
        if used + size > getattr(settings, "EEG_PATIENT_QUOTA_BYTES", 5 * 1024 ** 3):
            raise WorkspaceError("该患者 EEG 工作台的文件总量已达到 5 GB 上限。", 413)
        reserved = EegAsset.objects.filter(complete=False).aggregate(value=Sum(F("size") - F("uploaded_size")))["value"] or 0
        require_disk_space(reserved + size)
        asset = EegAsset.objects.create(
            recording=recording, name=name, name_key=name.casefold(), size=size, role=role,
            relative_path=f"recordings/{recording.pk}/files/{name}",
        )
        Recording.objects.filter(pk=recording.pk).update(status="uploading")
        return asset


def upload_offset(asset):
    if asset.complete:
        return asset.size
    path = private_path(asset.relative_path + ".upload-part")
    if not path.exists():
        final = private_path(asset.relative_path)
        if final.is_file():
            path = final
    current = path.stat().st_size if path.exists() else 0
    if current > asset.size:
        raise WorkspaceError("上传文件大小异常，请创建新记录。", 409)
    return current


def append_chunk(asset, offset, data):
    if not data or len(data) > 2 * 1024 ** 2:
        raise WorkspaceError("每次上传的数据块须为 1 字节至 2 MB。", 413)
    with locked(f"upload-{asset.pk}"):
        try:
            asset.refresh_from_db()
        except EegAsset.DoesNotExist:
            raise WorkspaceError("上传记录已不存在。", 404) from None
        current = upload_offset(asset)
        if asset.complete or offset != current:
            raise WorkspaceError("上传进度已变化，请从返回的位置继续。", 409, offset=current)
        if current + len(data) > asset.size:
            raise WorkspaceError("上传内容超过申报的文件大小。", 413, offset=current)
        require_disk_space(len(data))
        path = private_path(asset.relative_path + ".upload-part")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("ab") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        asset.uploaded_size = current + len(data)
        asset.save(update_fields=["uploaded_size"])
        return asset.uploaded_size


def companion_references(path, name):
    if not name.lower().endswith((".vhdr", ".vmrk")):
        return []
    if path.stat().st_size > 4 * 1024 ** 2:
        raise WorkspaceError("BrainVision 头文件过大。")
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    references = []
    for match in re.finditer(r"^\s*(?:DataFile|MarkerFile)\s*=\s*(.*?)\s*$", text, re.I | re.M):
        references.append(safe_name(match.group(1)))
    return references


def complete_upload(asset):
    with locked(f"upload-{asset.pk}"):
        try:
            asset.refresh_from_db()
        except EegAsset.DoesNotExist:
            raise WorkspaceError("上传记录已不存在。", 404) from None
        if asset.complete:
            return asset
        temporary = private_path(asset.relative_path + ".upload-part")
        final = private_path(asset.relative_path)
        # Recover an atomic rename performed immediately before a process crash.
        source = temporary if temporary.exists() else final
        if not source.is_file() or source.stat().st_size != asset.size:
            raise WorkspaceError("文件尚未上传完整，请继续上传。", 409, offset=upload_offset(asset))
        companion_references(source, asset.name)
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 ** 2), b""):
                digest.update(chunk)
        if source != final:
            os.replace(source, final)
        asset.sha256 = digest.hexdigest()
        asset.uploaded_size = asset.size
        asset.complete = True
        asset.save(update_fields=["sha256", "uploaded_size", "complete"])
        if asset.role == "raw" and not asset.recording.primary_file_id:
            Recording.objects.filter(pk=asset.recording_id, primary_file__isnull=True).update(primary_file=asset)
        Recording.objects.filter(pk=asset.recording_id).update(status="ready")
        return asset


def discard_upload(asset):
    with locked(f"patient-{asset.recording.patient_id}"), locked(f"upload-{asset.pk}"):
        try:
            asset.refresh_from_db()
        except EegAsset.DoesNotExist:
            raise WorkspaceError("上传记录已不存在。", 404) from None
        if asset.complete:
            raise WorkspaceError("已完成的文件不能通过放弃上传删除。", 409)
        for relative in (asset.relative_path + ".upload-part", asset.relative_path):
            path = private_path(relative)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                raise WorkspaceError("暂时无法移除上传文件，请联系管理员。", 500) from None
        recording = asset.recording
        asset.delete()
        status = "ready" if recording.files.filter(complete=True).exists() else "uploading" if recording.files.exists() else "empty"
        Recording.objects.filter(pk=recording.pk).update(status=status)


def snapshot_files(recording):
    assets = list(recording.files.filter(complete=True))
    if not any(asset.role == "raw" for asset in assets):
        raise WorkspaceError("请先完整上传至少一个原始 EEG 文件。", 409)
    names = {asset.name for asset in assets}
    for asset in assets:
        path = private_path(asset.relative_path)
        if not path.is_file() or path.stat().st_size != asset.size:
            raise WorkspaceError("记录文件不存在或大小已改变，请重新上传。", 409)
        if any(reference not in names for reference in companion_references(path, asset.name)):
            raise WorkspaceError("BrainVision 记录缺少配套的数据或标记文件，请上传完整的同名文件组。", 409)
    assets.sort(key=lambda asset: asset.pk != recording.primary_file_id)
    return [{"path": str(private_path(asset.relative_path)), "name": asset.name, "role": asset.role,
             "size": asset.size, "sha256": asset.sha256} for asset in assets]
