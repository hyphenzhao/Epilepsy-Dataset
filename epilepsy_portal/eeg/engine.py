"""Headless, patient-file-only EEG analysis. No Django or remote downloads."""

import contextlib
import csv
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import traceback
import warnings as python_warnings

SOURCE_METHODS = ("mne", "dspm", "sloreta", "eloreta", "lcmv", "dics", "mxne", "irmxne",
                  "tf_mxne", "ir_tf_mxne", "gamma_map", "dipole", "rap_music", "trap_music")
VOLTAGE_TYPES = {"eeg", "seeg", "ecog", "eog", "ecg", "emg"}
MAX_SAMPLES = 8_000_000


class AnalysisError(ValueError):
    """An actionable error safe to return without server paths or traceback."""


def _libs():
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS"):
        os.environ.setdefault(name, "1")
    import numpy as np
    import mne
    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt
    mne.set_log_level("ERROR")
    return np, mne, plt


def _number(value, default=None, minimum=None, maximum=None):
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise AnalysisError("分析参数必须是有效数字。") from None
    if not math.isfinite(number) or (minimum is not None and number < minimum) or (maximum is not None and number > maximum):
        raise AnalysisError("分析参数超出允许范围。")
    return number


def _names(value):
    if not value:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise AnalysisError("通道选择须为名称列表。")
    return list(dict.fromkeys(value))


def _basename(name):
    if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\\" in name or any(ord(c) < 32 for c in name):
        raise AnalysisError("文件引用必须使用已上传文件的名称。")
    return name


def _suffix(path):
    name = str(path).lower()
    return ".fif" if name.endswith(".fif.gz") else Path(name).suffix


class Context:
    def __init__(self, spec, output_dir, progress):
        if not isinstance(spec, dict) or spec.get("operation") not in {"inspect", "waveform", "psd", "tfr", "source", "preprocess"}:
            raise AnalysisError("请选择有效的分析操作。")
        self.spec = spec
        self.operation = spec["operation"]
        self.modality = spec.get("modality", "eeg")
        if self.modality not in {"eeg", "seeg"}:
            raise AnalysisError("请选择 EEG 或 sEEG 数据类型。")
        self.p = spec.get("parameters") or {}
        if not isinstance(self.p, dict):
            raise AnalysisError("分析参数格式无效。")
        self.output = Path(output_dir).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.progress = progress
        self.warnings = []
        self.artifacts = []
        self.files = []
        for entry in spec.get("files") or []:
            path = Path(entry.get("path", ""))
            name = _basename(entry.get("name") or path.name)
            if not path.is_absolute() or not path.is_file():
                raise AnalysisError("输入文件不存在或不是有效的私有文件。")
            self.files.append({"name": name, "path": path.resolve(), "role": entry.get("role", "raw")})
        if not self.files or len({item["name"] for item in self.files}) != len(self.files):
            raise AnalysisError("请提供输入文件，且各文件名称不能重复。")

    def emit(self, percent, message):
        if self.progress:
            self.progress(percent, message)

    def file(self, role):
        preferred = self.p.get("primary_file" if role == "raw" else role + "_file")
        if preferred:
            preferred = _basename(preferred)
            selected = next((item for item in self.files if item["name"] == preferred and item["role"] == role), None)
            if selected is None:
                raise AnalysisError("选择的模型或数据文件未包含在本次上传中。")
            return selected
        return next((item for item in self.files if item["role"] == role), None)

    def artifact(self, name, label, kind="data"):
        name = _basename(name)
        destination = self.output / name
        if destination.resolve() in {item["path"] for item in self.files}:
            raise AnalysisError("输出位置与输入文件冲突，请使用独立分析目录。")
        self.artifacts.append({"name": name, "label": label, "kind": kind})
        return destination


def _check_references(path, names, suffix, mne):
    def checked(reference):
        reference = str(reference).strip()
        if _basename(reference) not in names:
            raise AnalysisError("缺少配套数据文件，请同时上传所有伴随文件并保留原名称。")
    if suffix in {".vhdr", ".ahdr", ".vmrk"}:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for ref in re.findall(r"(?im)^\s*(?:DataFile|MarkerFile)\s*=\s*(.+)$", text):
            checked(ref)
    elif suffix == ".set":
        if path.stat().st_size > 128 * 1024**2:
            raise AnalysisError("内嵌 EEGLAB 文件过大，请转为 EDF/FIF 或使用分离的 SET/FDT 文件。")
        from pymatreader import read_mat
        content = read_mat(str(path))
        eeg = content.get("EEG", content)
        data = eeg.get("data") if isinstance(eeg, dict) else None
        if isinstance(data, str):
            checked(data)
    elif suffix == ".fif":
        # Inspect split-file references before the reader follows them. These
        # helpers are pinned with MNE 1.12; no file outside the manifest is read.
        from mne._fiff.open import fiff_open
        from mne._fiff.tag import read_tag
        from mne._fiff.constants import FIFF
        fid, tree, directory = fiff_open(path, verbose=False)
        with fid:
            for entry in directory:
                if entry.kind == FIFF.FIFF_REF_FILE_NAME:
                    checked(read_tag(fid, entry.pos).data)


@contextlib.contextmanager
def _read(ctx, mne):
    selected = ctx.file("raw")
    if selected is None:
        raise AnalysisError("请选择一个原始 EEG/sEEG 文件。")
    with tempfile.TemporaryDirectory(prefix="eeg-input-") as folder:
        names = {item["name"] for item in ctx.files}
        for item in ctx.files:
            os.symlink(item["path"], Path(folder) / item["name"])
        path = Path(folder) / selected["name"]
        suffix = _suffix(path)
        for item in ctx.files:
            if item["role"] in {"raw", "companion"}:
                _check_references(Path(folder) / item["name"], names, _suffix(item["name"]), mne)
        readers = {".edf": mne.io.read_raw_edf, ".bdf": mne.io.read_raw_bdf,
                   ".gdf": mne.io.read_raw_gdf, ".fif": mne.io.read_raw_fif,
                   ".vhdr": mne.io.read_raw_brainvision, ".ahdr": mne.io.read_raw_brainvision,
                   ".set": mne.io.read_raw_eeglab, ".cnt": mne.io.read_raw_cnt,
                   ".mff": mne.io.read_raw_egi}
        if suffix not in readers:
            raise AnalysisError("该格式尚不支持，请使用 EDF/BDF/GDF/FIF、BrainVision 或 EEGLAB 数据。")
        options = {"preload": False, "verbose": False}
        if suffix in {".edf", ".bdf"}:
            options["infer_types"] = True
        raw = readers[suffix](str(path), **options)
        try:
            if ctx.modality == "seeg":
                raw.set_channel_types({name: "seeg" for name, kind in zip(raw.ch_names, raw.get_channel_types()) if kind == "eeg"}, verbose=False)
            yield raw
        finally:
            raw.close()


def _head_transform(transform, mne):
    from mne._fiff.constants import FIFF
    if int(transform["from"]) == FIFF.FIFFV_COORD_MRI and int(transform["to"]) == FIFF.FIFFV_COORD_HEAD:
        return transform
    if int(transform["from"]) == FIFF.FIFFV_COORD_HEAD and int(transform["to"]) == FIFF.FIFFV_COORD_MRI:
        return mne.transforms.invert_transform(transform)
    raise AnalysisError("配准变换须连接 MRI 与 head 坐标系。")


def _positions(raw, ctx, np, mne, required=False):
    from mne._fiff.constants import FIFF
    file = ctx.file("electrodes")
    if file:
        suffix = _suffix(file["path"])
        if suffix in {".json", ".csv", ".tsv"}:
            if suffix == ".json":
                payload = json.loads(file["path"].read_text(encoding="utf-8-sig"))
            else:
                with file["path"].open(encoding="utf-8-sig", newline="") as handle:
                    entries = list(csv.DictReader(handle, delimiter="\t" if suffix == ".tsv" else ","))
                if not entries or len(entries) > 512 or any(not {"name", "x", "y", "z", "unit", "coord_frame"}.issubset(item) for item in entries):
                    raise AnalysisError("电极表须包含 name,x,y,z,unit,coord_frame 列。")
                units = {item["unit"].strip() for item in entries}
                frames = {item["coord_frame"].strip() for item in entries}
                if len(units) != 1 or len(frames) != 1:
                    raise AnalysisError("同一电极表的单位和坐标系须一致。")
                payload = {"unit": units.pop(), "coord_frame": frames.pop(), "electrodes": entries}
            unit = payload.get("unit")
            frame = payload.get("coord_frame")
            if unit not in {"m", "mm"} or frame not in {"head", "mri"}:
                raise AnalysisError("电极 JSON 须明确 unit=m/mm 和 coord_frame=head/mri。")
            entries = payload.get("electrodes") or []
            if not isinstance(entries, list) or len(entries) > 512 or len({str(item.get("name", "")) for item in entries}) != len(entries):
                raise AnalysisError("电极名称须有效且不重复，最多 512 个坐标。")
            renames = ctx.p.get("renames") or {}
            positions = {renames.get(str(item["name"]), str(item["name"])): np.array([item["x"], item["y"], item["z"]], float) * (0.001 if unit == "mm" else 1) for item in entries}
            if frame == "mri":
                trans_file = ctx.file("trans")
                if not trans_file:
                    raise AnalysisError("MRI 坐标电极需要对应的 head↔MRI 配准文件。")
                trans = _head_transform(mne.read_trans(str(trans_file["path"])), mne)
                positions = {name: mne.transforms.apply_trans(trans, pos) for name, pos in positions.items()}
            if any(not np.all(np.isfinite(pos)) or np.linalg.norm(pos) > 0.5 for pos in positions.values()):
                raise AnalysisError("电极坐标或单位无效，请检查坐标文件。")
            raw.set_montage(mne.channels.make_dig_montage(ch_pos=positions, coord_frame="head"), on_missing="ignore", verbose=False)
        elif suffix == ".fif":
            raw.set_montage(mne.channels.read_dig_fif(str(file["path"])), on_missing="ignore", verbose=False)
        else:
            raise AnalysisError("电极坐标请使用明确单位和坐标系的 JSON/CSV/TSV 或 MNE digitization FIF。")
    eeg_names = [name for name, kind in zip(raw.ch_names, raw.get_channel_types()) if kind == "eeg"]
    missing = [name for name in eeg_names if not np.all(np.isfinite(raw.info["chs"][raw.ch_names.index(name)]["loc"][:3])) or not np.linalg.norm(raw.info["chs"][raw.ch_names.index(name)]["loc"][:3])]
    non_head = [name for name in eeg_names if name not in missing and int(raw.info["chs"][raw.ch_names.index(name)]["coord_frame"]) != FIFF.FIFFV_COORD_HEAD]
    if non_head and required:
        raise AnalysisError("记录包含未配准到 head 的实测电极，请提供正确的坐标/配准，不能直接重标坐标系。")
    if non_head and missing and not file:
        ctx.warnings.append("部分实测电极不在 head 坐标系，未补入模板或将坐标重新标记；请提供配准。")
        missing = []
    if missing and ctx.modality == "eeg" and not file:
        standard = mne.channels.make_standard_montage("standard_1020")
        # Native standard_1020 coordinates are MRI, not head. Preserve and
        # apply its fiducial transform rather than relabeling the coordinates.
        standard.apply_trans(mne.channels.compute_native_head_t(standard))
        standard_positions = standard.get_positions()["ch_pos"]
        canonical = {name.upper(): name for name in standard_positions}
        aliases = {"T3": "T7", "T4": "T8", "T5": "P7", "T6": "P8"}
        positions = {name: raw.info["chs"][raw.ch_names.index(name)]["loc"][:3].copy() for name in eeg_names
                     if name not in missing and int(raw.info["chs"][raw.ch_names.index(name)]["coord_frame"]) == FIFF.FIFFV_COORD_HEAD}
        filled = 0
        for name in missing:
            token = re.sub(r"^EEG[ _-]*", "", name, flags=re.I)
            token = re.sub(r"[ _-]+(?:REF|LE|RE|A1|A2)$", "", token, flags=re.I).upper()
            token = aliases.get(token, token)
            if token in canonical:
                positions[name] = standard_positions[canonical[token]]
                filled += 1
        if filled:
            raw.set_montage(mne.channels.make_dig_montage(ch_pos=positions, coord_frame="head"), on_missing="ignore", verbose=False)
            ctx.warnings.append(f"为 {filled} 个缺失坐标的通道补入标准 10–20 模板位置；保留已有 head 实测坐标，混合几何不是完整患者测量。")
    electrodes = []
    for name, kind, channel in zip(raw.ch_names, raw.get_channel_types(), raw.info["chs"]):
        pos = np.asarray(channel["loc"][:3])
        if kind not in {"eeg", "seeg", "ecog"} or not np.all(np.isfinite(pos)) or not np.linalg.norm(pos):
            continue
        if int(channel["coord_frame"]) != FIFF.FIFFV_COORD_HEAD:
            if required:
                raise AnalysisError("电极必须位于已验证的 head 坐标系，请提供正确的配准坐标。")
            continue
        electrodes.append({"name": name, "x": float(pos[0]), "y": float(pos[1]), "z": float(pos[2])})
    if required and len(electrodes) != len(eeg_names):
        raise AnalysisError("部分 EEG 通道缺少有效电极位置，请设置正确坐标或标记坏导。")
    return {"electrodes": electrodes, "vertices": [], "faces": [], "coord_frame": "head (meters)"}


def _prepare(original, ctx, np, mne):
    p = ctx.p
    original_types = original.get_channel_types()
    if ctx.operation == "source" and (ctx.modality == "seeg" or ("seeg" in original_types and "eeg" not in original_types)):
        raise AnalysisError("sEEG 不支持标准头皮 EEG 逆解，请使用波形、双极参考或频谱/时频。")
    renames = p.get("renames") or {}
    if not isinstance(renames, dict) or any(old not in original.ch_names or not isinstance(new, str) or not new for old, new in renames.items()):
        raise AnalysisError("通道重命名不合法，请检查原名称和新名称。")
    names = [renames.get(name, name) for name in original.ch_names]
    if len(set(names)) != len(names):
        raise AnalysisError("通道重命名不能生成重复名称。")
    raw = original.copy()
    raw.rename_channels(renames)
    picks = [renames.get(name, name) for name in _names(p.get("picks"))]
    bads = [renames.get(name, name) for name in _names(p.get("bads"))]
    if any(name not in raw.ch_names for name in picks + bads):
        raise AnalysisError("通道选择或坏导名称与文件不一致。")
    raw.info["bads"] = list(dict.fromkeys(raw.info["bads"] + bads))
    if not picks:
        picks = [name for name, kind in zip(raw.ch_names, raw.get_channel_types()) if kind == "eeg" or (ctx.operation != "source" and kind in VOLTAGE_TYPES)]
    picks = [name for name in picks if name not in raw.info["bads"]]
    limit = 128 if ctx.operation == "source" else 32
    if len(picks) > limit:
        picks = picks[:limit]
        ctx.warnings.append(f"本次云分析最多处理 {limit} 个通道。")
    if not picks:
        raise AnalysisError("没有可分析的有效电位通道。")
    raw.pick(picks)
    if any(kind not in VOLTAGE_TYPES for kind in raw.get_channel_types()):
        raise AnalysisError("请选择电位通道，刺激标记和其他物理量不能按微伏显示。")
    sfreq = float(raw.info["sfreq"])
    total = raw.n_times / sfreq
    ctx.start = _number(p.get("start"), 0, 0)
    max_duration = 10 if ctx.operation == "source" else 120
    ctx.duration = _number(p.get("duration"), min(10, total), 0.02, max_duration)
    if ctx.start >= total:
        raise AnalysisError("所选起始时间超出记录。")
    ctx.duration = min(ctx.duration, total - ctx.start)
    ctx.original_sfreq = sfreq
    beginning, ending = ctx.start, ctx.start + ctx.duration
    events = p.get("event_times") or []
    if isinstance(events, str):
        events = [part.strip() for part in events.split(",") if part.strip()]
    if events and ctx.operation == "source":
        if len(events) > 200:
            raise AnalysisError("单次最多分析 200 个明确事件。")
        events = [_number(value, minimum=0, maximum=total) for value in events]
        tmin, tmax = _number(p.get("tmin"), -.2), _number(p.get("tmax"), .5)
        if tmax <= tmin or tmax - tmin > 10:
            raise AnalysisError("事件分析窗口不合法。")
        beginning, last_event_sample = min(events) + tmin, max(events) + tmax
        if beginning < 0 or last_event_sample > total - 1/sfreq:
            raise AnalysisError("事件及其分析窗口超出记录范围。")
        # Epochs tmax includes its final sample; ordinary selected windows are
        # half-open. Keep one additional input sample before the shared crop.
        ending = last_event_sample + 1/sfreq
        ctx.events = events
    else:
        ctx.events = []
    ctx.baseline = None
    if p.get("baseline_start") not in (None, "") or p.get("baseline_end") not in (None, ""):
        b0, b1 = _number(p.get("baseline_start")), _number(p.get("baseline_end"))
        if b0 is None or b1 is None or b0 < 0 or b1 <= b0 or b1 > total:
            raise AnalysisError("噪声基线须有有效的绝对起止时间。")
        ctx.baseline = (b0, b1)
        beginning, ending = min(beginning, b0), max(ending, b1)
    padding = 2.0 if any(p.get(name) not in (None, "", 0) for name in ("l_freq", "h_freq", "notch")) else 0
    beginning, ending = max(0, beginning - padding), min(total, ending + padding)
    if ending - beginning > 300 or (ending - beginning) * sfreq * len(raw.ch_names) > MAX_SAMPLES:
        raise AnalysisError("分析跨度或数据量过大，请缩短窗口/基线间隔或减少通道。")
    first_samp = raw.first_samp
    raw.crop(tmin=beginning, tmax=min(ending - 1/sfreq, raw.times[-1]))
    ctx.offset = (raw.first_samp - first_samp) / sfreq
    raw.load_data()
    if not np.all(np.isfinite(raw.get_data())):
        raise AnalysisError("数据包含非有限值，请先检查记录或坏导。")
    low, high = _number(p.get("l_freq")), _number(p.get("h_freq"))
    if low == 0: low = None
    if high == 0: high = None
    if (low is not None and not 0 < low < sfreq/2) or (high is not None and not 0 < high < sfreq/2) or (low is not None and high is not None and low >= high):
        raise AnalysisError("滤波频率须有效且低于奈奎斯特频率。")
    if low is not None or high is not None:
        raw.filter(low, high, method="iir", iir_params={"order": 4, "ftype": "butter"}, n_jobs=1, verbose=False)
    notch = p.get("notch") or []
    if isinstance(notch, str): notch = notch.split(",")
    if isinstance(notch, (int, float)): notch = [notch]
    if len(notch) > 6: raise AnalysisError("最多设置 6 个陷波频率。")
    for value in notch:
        frequency = _number(value, minimum=.01)
        if frequency >= sfreq/2: raise AnalysisError("陷波频率必须低于奈奎斯特频率。")
        raw.notch_filter([frequency], method="iir", iir_params={"order": 4, "ftype": "butter"}, n_jobs=1, verbose=False)
    reference = p.get("reference", "original")
    if reference == "bipolar":
        if ctx.operation == "source":
            raise AnalysisError("双极导联不能直接套用标准头皮源模型，请使用匹配的单极 EEG 与 forward。")
        pairs = p.get("bipolar_pairs") or []
        if isinstance(pairs, str):
            pairs = [{"anode": a.strip(), "cathode": b.strip()} for a,b in (part.split(":",1) for part in pairs.split(",") if ":" in part)]
        pairs = [{"anode": item[0], "cathode": item[1]} if isinstance(item, (list, tuple)) and len(item) == 2 else item for item in pairs]
        if any(not isinstance(item, dict) or not isinstance(item.get("anode"), str) or not isinstance(item.get("cathode"), str) for item in pairs):
            raise AnalysisError("双极导联须明确每组 anode 和 cathode。")
        if not pairs or len(pairs) > 32: raise AnalysisError("请明确提供 1–32 组双极参考通道。")
        anodes = [renames.get(item["anode"], item["anode"]) for item in pairs]
        cathodes = [renames.get(item["cathode"], item["cathode"]) for item in pairs]
        if any(name not in raw.ch_names for name in anodes+cathodes): raise AnalysisError("双极参考所需通道未选中或不存在。")
        raw = mne.set_bipolar_reference(raw, anodes, cathodes, ch_name=[item.get("name") or a+"-"+b for item,a,b in zip(pairs,anodes,cathodes)], drop_refs=True, copy=False, verbose=False)
    elif reference == "average" and ctx.operation != "source":
        raw.set_eeg_reference(ref_channels="average", projection=False, verbose=False)
    elif reference not in {"original", "none", "average"}:
        raise AnalysisError("请选择原始、平均或明确的双极参考。")
    if ctx.operation == "source":
        target = _number(p.get("source_sfreq"), min(250, sfreq), 50, 500)
        if target < sfreq:
            raw.resample(target, n_jobs=1, verbose=False)
            ctx.warnings.append(f"源分析为控制资源使用，经抗混叠重采样至 {target:g} Hz；导出记录该变化。")
    return raw


def _window(raw, ctx):
    sfreq = raw.info["sfreq"]
    start = max(0, ctx.start - ctx.offset)
    end = min(raw.times[-1], ctx.start + ctx.duration - ctx.offset - 1/sfreq)
    if end <= start:
        raise AnalysisError("所选窗口过短，无法完成分析。")
    return raw.copy().crop(tmin=start, tmax=end)


def _clip_start(raw, clip, ctx):
    return ctx.offset + (clip.first_samp - raw.first_samp) / float(raw.info["sfreq"])


def _inspect(raw, ctx, np, mne):
    geometry = _positions(raw, ctx, np, mne)
    return {"channels": [{"name": name, "type": kind} for name, kind in zip(raw.ch_names, raw.get_channel_types())],
            "sfreq": float(raw.info["sfreq"]), "duration": raw.n_times / float(raw.info["sfreq"]),
            "annotations": [{"onset": float(onset), "duration": float(duration), "description": str(description)}
                            for onset, duration, description in zip(raw.annotations.onset[:2000], raw.annotations.duration[:2000], raw.annotations.description[:2000])],
            "geometry": geometry}


def _waveform(raw, ctx, np):
    clip = _window(raw, ctx)
    data = clip.get_data() * 1e6
    count = data.shape[1]
    if count <= 2000:
        indices = np.arange(count)
    else:
        # A shared irregular time grid includes each channel's extrema. Uniform
        # stride alone can hide a single-sample epileptiform transient.
        bins = max(1, (2000 - 2) // (2 * data.shape[0]))
        boundaries = np.linspace(0, count, bins + 1, dtype=int)
        indices = {0, count - 1}
        for left, right in zip(boundaries[:-1], boundaries[1:]):
            block = data[:, left:right]
            indices.update((left + np.argmin(block, axis=1)).tolist())
            indices.update((left + np.argmax(block, axis=1)).tolist())
        indices = np.array(sorted(indices))
    start = _clip_start(raw, clip, ctx)
    times = clip.times[indices] + start
    return {"times": times.tolist(), "data_uv": data[:, indices].tolist(), "channels": clip.ch_names,
            "sfreq": float(clip.info["sfreq"]), "original_sfreq": ctx.original_sfreq,
            "start": start, "duration": float(clip.n_times / clip.info["sfreq"]),
            "display_sampling": "minmax_irregular" if count > 2000 else "original", "unit": "µV"}


def _freq_range(ctx, sfreq, default_max=80):
    low = _number(ctx.p.get("fmin"), 1, .01)
    high = _number(ctx.p.get("fmax"), min(default_max, sfreq / 2 - .01), .01)
    if high <= low or high >= sfreq / 2:
        raise AnalysisError("频率范围须递增且低于奈奎斯特频率。")
    return low, high


def _psd(raw, ctx, np, plt):
    clip = _window(raw, ctx)
    low, high = _freq_range(ctx, clip.info["sfreq"])
    n_fft = min(clip.n_times, max(32, int(clip.info["sfreq"] * 2)))
    spectrum = clip.compute_psd(method="welch", fmin=low, fmax=high, n_fft=n_fft, n_overlap=n_fft//2,
                                picks="all", n_jobs=1, verbose=False)
    power, frequencies = spectrum.get_data(return_freqs=True)
    power = power * 1e12
    fig, ax = plt.subplots(figsize=(10, 4.8), layout="constrained")
    for name, values in zip(clip.ch_names, power):
        ax.semilogy(frequencies, np.maximum(values, np.finfo(float).tiny), label=name, linewidth=.9, alpha=.8)
    ax.set(xlabel="Frequency (Hz)", ylabel="PSD (µV²/Hz)", title="Welch power spectrum")
    ax.grid(alpha=.2)
    if len(clip.ch_names) <= 16: ax.legend(fontsize=7, ncol=4)
    fig.savefig(ctx.artifact("psd.png", "功率谱", "image"), dpi=140)
    plt.close(fig)
    with ctx.artifact("psd.csv", "功率谱数值（µV²/Hz）").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["frequency_hz"] + clip.ch_names)
        writer.writerows(zip(frequencies, *power))
    return {"summary": "Welch 功率谱；电位单位由原始文件读取，未进行幅值猜测。", "frequencies": frequencies.tolist(),
            "start":_clip_start(raw,clip,ctx), "duration":clip.n_times/clip.info["sfreq"],
            "channels": clip.ch_names, "unit": "µV²/Hz", "method": "welch"}


def _tfr(raw, ctx, np, mne, plt):
    clip = _window(raw, ctx)
    low, high = _freq_range(ctx, clip.info["sfreq"])
    step = _number(ctx.p.get("freq_step"), 2, .1, 50)
    frequencies = np.arange(low, high + step * .1, step)
    if not 1 <= len(frequencies) <= 96:
        raise AnalysisError("单次时频最多 96 个频点，请增大频率步长。")
    method = str(ctx.p.get("tfr_method") or ctx.p.get("method") or "morlet").lower()
    if method not in {"morlet", "multitaper", "stockwell"}:
        raise AnalysisError("请选择 Morlet、multitaper 或 Stockwell 时频方法。")
    decim = max(1, int(math.ceil(clip.n_times / 1000)))
    data = clip.get_data()[None, :, :]
    if data.size * len(frequencies) > 64_000_000:
        raise AnalysisError("时频数据量过大，请缩短窗口或减少通道/频点。")
    cycles = _number(ctx.p.get("n_cycles"), 3, 1, 20)
    if method != "stockwell" and 5 * cycles / (math.pi * low) > clip.n_times / clip.info["sfreq"]:
        raise AnalysisError("窗口短于最低频率的小波，请延长窗口、提高最低频率或减少周期数。")
    ctx.emit(45, "计算时频功率")
    if method == "morlet":
        power = mne.time_frequency.tfr_array_morlet(data, clip.info["sfreq"], frequencies, n_cycles=cycles,
                    zero_mean=True, decim=decim, output="power", n_jobs=1, verbose=False)[0]
    elif method == "multitaper":
        power = mne.time_frequency.tfr_array_multitaper(data, clip.info["sfreq"], frequencies, n_cycles=cycles,
                    time_bandwidth=4, zero_mean=True, decim=decim, output="power", n_jobs=1, verbose=False)[0]
    else:
        n_fft = 2**int(math.ceil(math.log2(clip.n_times)))
        stockwell_bins = int(math.ceil((high-low)*n_fft/clip.info["sfreq"]))+2
        if n_fft > 8192 or n_fft*stockwell_bins > 4_000_000:
            raise AnalysisError("Stockwell 窗口或频段过大，请缩短窗口或缩窄频段以控制云端资源。")
        power, _, frequencies = mne.time_frequency.tfr_array_stockwell(data, clip.info["sfreq"], fmin=low, fmax=high,
                    n_fft=n_fft, decim=decim, width=_number(ctx.p.get("stockwell_width"), 1, .1, 5), n_jobs=1, verbose=False)
        if len(frequencies) > 96:
            indices = np.linspace(0, len(frequencies)-1, 96, dtype=int)
            frequencies, power = frequencies[indices], power[:, indices]
    power = power * 1e12
    times = clip.times[::decim][:power.shape[-1]] + _clip_start(raw, clip, ctx)
    fig, ax = plt.subplots(figsize=(10, 5), layout="constrained")
    values = 10*np.log10(np.maximum(power.mean(axis=0), np.finfo(float).tiny))
    image = ax.pcolormesh(times, frequencies, values, shading="auto", cmap="magma")
    ax.set(xlabel="Recording time (s)", ylabel="Frequency (Hz)", title=f"{method.title()} — channel mean, single continuous observation")
    fig.colorbar(image, ax=ax, label="10 log₁₀(power / µV²)")
    fig.savefig(ctx.artifact("tfr.png", "时频功率", "image"), dpi=140)
    plt.close(fig)
    for index, (channel, channel_power) in enumerate(zip(clip.ch_names, power), start=1):
        fig, ax = plt.subplots(figsize=(10, 5), layout="constrained")
        image = ax.pcolormesh(times, frequencies, 10*np.log10(np.maximum(channel_power, np.finfo(float).tiny)), shading="auto", cmap="magma")
        ax.set(xlabel="Recording time (s)", ylabel="Frequency (Hz)", title=f"{method.title()} — {channel}, single continuous observation")
        fig.colorbar(image, ax=ax, label="10 log₁₀(power / µV²)")
        fig.savefig(ctx.artifact(f"tfr-channel-{index:02d}.png", f"{channel} 时频功率", "image"), dpi=120)
        ctx.artifacts[-1]["channel"] = channel
        plt.close(fig)
    np.savez_compressed(ctx.artifact("tfr.npz", "时频数值"), power_uv2=power, frequencies_hz=frequencies,
                        times_s=times, channels=np.asarray(clip.ch_names, dtype="U"))
    return {"summary": "单个连续选段的时频功率，图为所选通道的功率均值；不是重复试次平均。",
            "frequencies": frequencies.tolist(), "channels": clip.ch_names, "method": method, "unit": "µV²", "observations": 1,
            "start":_clip_start(raw,clip,ctx), "duration":clip.n_times/clip.info["sfreq"]}


def _preprocess(raw, ctx, np, mne):
    clip = _window(raw, ctx)
    if ctx.p.get("ica") is True:
        from mne.preprocessing import ICA
        n_components = min(len(clip.ch_names)-1, int(_number(ctx.p.get("ica_components"), 15, 2, 30)))
        if n_components < 2 or clip.n_times < len(clip.ch_names)*20:
            raise AnalysisError("ICA 需要足够通道和样本，请延长窗口。")
        fit = clip.copy().filter(1, None, method="iir", n_jobs=1, verbose=False)
        ica = ICA(n_components=n_components, method="fastica", random_state=97, max_iter=300)
        ica.fit(fit, picks="all", reject_by_annotation=True, verbose=False)
        exclude = ctx.p.get("ica_exclude") or []
        if not isinstance(exclude, list) or any(not isinstance(i, int) or i < 0 or i >= ica.n_components_ for i in exclude):
            raise AnalysisError("ICA 排除编号须为有效的从零开始的组件编号。")
        ica.exclude = list(dict.fromkeys(exclude))
        ica.save(str(ctx.artifact("ica.fif", "ICA 分解（需人工审核组件）")), overwrite=True, verbose=False)
        ica.apply(clip, verbose=False)
        ctx.warnings.append("ICA 仅去除明确选择的组件；算法不会自动判定并删除病理放电。")
    clip.save(str(ctx.artifact("processed_raw.fif", "处理后的选段（SI 单位）")), overwrite=True, verbose=False)
    return {"summary": "导出所选通道、窗口和预处理后的记录；原文件保留。", "channels": clip.ch_names,
            "sfreq": float(clip.info["sfreq"]), "start": _clip_start(raw,clip,ctx), "duration": clip.n_times/clip.info["sfreq"]}


def run_analysis(spec, output_dir, progress=None):
    ctx = Context(spec, output_dir, progress)
    np, mne, plt = _libs()
    ctx.emit(8, "读取记录")
    with python_warnings.catch_warnings(record=True) as captured:
        # MNE diagnostic warnings may contain private absolute file paths. User
        # warnings below are explicit, scientifically relevant and path-free.
        with _read(ctx, mne) as original:
            if ctx.operation == "inspect":
                result = _inspect(original, ctx, np, mne)
                processed_channels = list(original.ch_names)
                processed_sfreq = float(original.info["sfreq"])
            else:
                raw = _prepare(original, ctx, np, mne)
                ctx.emit(25, "准备选段与通道")
                if ctx.operation == "waveform": result = _waveform(raw, ctx, np)
                elif ctx.operation == "psd": result = _psd(raw, ctx, np, plt)
                elif ctx.operation == "tfr": result = _tfr(raw, ctx, np, mne, plt)
                elif ctx.operation == "preprocess": result = _preprocess(raw, ctx, np, mne)
                else:
                    from .source_algorithms import compute_source
                    result = compute_source(raw, ctx, np, mne, plt)
                processed_channels = list(raw.ch_names)
                processed_sfreq = float(raw.info["sfreq"])
            original_sfreq = float(original.info["sfreq"])
        if any(any(word in str(item.message).lower() for word in ("converg", "iteration limit", "maximum number of iterations")) for item in captured):
            ctx.warnings.append("求解器报告未收敛或达到迭代限制；请检查数据和正则参数，结果需谨慎解释。")
    result.update(status="completed", operation=ctx.operation, warnings=list(dict.fromkeys(ctx.warnings)), artifacts=ctx.artifacts)
    result["provenance"] = {
        "mne_version": mne.__version__, "numpy_version": np.__version__,
        "channels": processed_channels, "sfreq": processed_sfreq, "original_sfreq": original_sfreq,
        "resampling": {"applied": processed_sfreq != original_sfreq, "from_hz": original_sfreq, "to_hz": processed_sfreq},
        "reference": "average EEG projector, consistently applied by MNE inverse routines" if ctx.operation == "source" else ctx.p.get("reference", "original"),
        "window": result.get("analysis_window") or {"start":result.get("start",getattr(ctx,"start",0)),
                                                     "duration":result.get("duration",getattr(ctx,"duration",result.get("duration")))},
        "nave": result.get("nave"), "noise_model": result.get("noise_model"),
        "temporal_reference": result.get("temporal_reference", "recording_absolute"),
        "time_axis": result.get("time_axis", "seconds"), "coord_frame": result.get("coord_frame"),
    }
    # Reject accidental NaN and unserializable objects before publishing result.
    json.dumps(result, allow_nan=False)
    ctx.emit(100, "分析完成")
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        return 2
    destination = Path(argv[1]).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    def write_json(path, value):
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    def progress(percent, message):
        write_json(destination.parent / "progress.json", {"progress": max(0, min(100, int(percent))), "message": message})
    try:
        spec = json.loads(Path(argv[0]).read_text(encoding="utf-8-sig"))
        result = run_analysis(spec, destination.parent, progress)
        write_json(destination, result)
        return 0
    except Exception as exc:
        # Full technical traceback stays private; never expose paths, input
        # data, or dependency exception text through the public job response.
        (destination.parent / "traceback-private.txt").write_text(traceback.format_exc(), encoding="utf-8")
        message = str(exc) if isinstance(exc, AnalysisError) else "分析未完成，请检查文件、参数及所需模型；详细原因已记录在服务器。"
        write_json(destination, {"status": "failed", "error": message})
        progress(100, "分析失败")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
