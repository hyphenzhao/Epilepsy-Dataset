"""MNE inverse methods with explicit geometry, noise and observation semantics."""

import csv
import math
import os
from pathlib import Path

from .engine import AnalysisError, SOURCE_METHODS, _clip_start, _head_transform, _number, _positions, _window


def _method(value):
    token = str(value or "eloreta").lower().replace("-", "_").replace(" ", "")
    aliases = {"mixed_norm": "mxne", "ir_mixed_norm": "irmxne", "ir_mxne": "irmxne", "tf_mixed_norm": "tf_mxne",
               "ir_tf_mixed_norm": "ir_tf_mxne", "irtf_mxne": "ir_tf_mxne", "gamma": "gamma_map",
               "gammamap": "gamma_map", "ecd": "dipole", "fit_dipole": "dipole", "rapmusic": "rap_music", "trapmusic": "trap_music"}
    token = aliases.get(token, token)
    if token not in SOURCE_METHODS:
        raise AnalysisError("请选择有效的 MNE 源定位方法。")
    return token


def _to_head(points, frame, fwd, np, mne, vectors=False):
    """Transform display coordinates, never alter forward gain/orientation."""
    from mne._fiff.constants import FIFF
    points = np.asarray(points, float)
    if int(frame) == FIFF.FIFFV_COORD_HEAD:
        return points.copy()
    if int(frame) != FIFF.FIFFV_COORD_MRI or fwd.get("mri_head_t") is None:
        raise AnalysisError("Forward 源坐标缺少可验证的 MRI→head 配准，无法正确显示定位。")
    trans = _head_transform(fwd["mri_head_t"], mne)
    return mne.transforms.apply_trans(trans, points, move=not vectors)


def _model(raw, ctx, np, mne, method):
    p = ctx.p
    bem_file, src_file, trans_file = ctx.file("bem"), ctx.file("src"), ctx.file("trans")
    fwd_file = ctx.file("forward")
    bem = trans = None
    template = False
    demo = False
    if bem_file:
        bem = mne.read_bem_solution(str(bem_file["path"]), verbose=False)
        if len(bem["surfs"]) < 3:
            raise AnalysisError("EEG 需要至少三层导电头模型；单层 MEG BEM 不适用。")
    if trans_file:
        trans = _head_transform(mne.read_trans(str(trans_file["path"]), verbose=False), mne)
    head_model = str(p.get("head_model") or "uploaded")
    if head_model == "sphere":
        if p.get("demo_only") is not True:
            raise AnalysisError("球模型仅用于明确选择的演示/探索，不能作为患者解剖模型。")
        demo = True
        bem = mne.make_sphere_model(r0=(0, 0, 0), head_radius=.09, verbose=False)
        trans = None
        ctx.warnings.append("使用多层球形演示头模型；坐标与结果不代表患者解剖定位。")
    if fwd_file:
        fwd = mne.read_forward_solution(str(fwd_file["path"]), verbose=False)
        if fwd["nsource"] > 20_000:
            raise AnalysisError("上传 Forward 源点超过云端限额，请提供较粗分辨率的源空间。")
        if method == "dipole" and bem is None:
            raise AnalysisError("连续 ECD 拟合还需要该 Forward 对应的三层 BEM 与配准文件。")
        if method == "dipole" and not demo and trans is None:
            if fwd.get("mri_head_t") is None:
                raise AnalysisError("ECD 的 BEM 需要与记录匹配的 head↔MRI 配准。")
            trans = _head_transform(fwd["mri_head_t"], mne)
    else:
        if head_model in {"fsaverage", "template"}:
            subjects_dir = Path(os.environ.get("EEG_SUBJECTS_DIR") or "")
            template_bem = subjects_dir / "fsaverage" / "bem" / "fsaverage-ico3-bem-sol.fif"
            template_src = subjects_dir / "fsaverage" / "bem" / "fsaverage-ico4-src.fif"
            if not template_bem.is_file() or not template_src.is_file():
                raise AnalysisError("fsaverage 低分辨率模板尚未缓存，请先由管理员准备模板；分析不会联网下载。")
            bem = mne.read_bem_solution(str(template_bem), verbose=False)
            src = mne.read_source_spaces(str(template_src), verbose=False)
            trans = "fsaverage"
            template = True
            ctx.warnings.append("使用 fsaverage ico4 源空间 / ico3 三层 BEM 模板，是粗分辨率模板近似，并非患者个体 MRI。")
        elif src_file and bem is not None and trans is not None:
            src = mne.read_source_spaces(str(src_file["path"]), verbose=False)
        elif demo:
            spacing = _number(p.get("sphere_spacing"), 30, 20, 50)
            src = mne.setup_volume_source_space(pos=spacing, sphere=(0,0,0,.075), exclude=10, mindist=10,
                                                add_interpolator=False, n_jobs=1, verbose=False)
        else:
            raise AnalysisError("请上传匹配的 Forward，或提供 BEM、源空间和配准；也可明确选用已缓存的 fsaverage 模板。")
        if sum(space["nuse"] for space in src) > 20_000:
            raise AnalysisError("源空间过大，请提供较粗分辨率模型。")
        ctx.emit(35, "构建导电头模型与 Forward")
        fwd = mne.make_forward_solution(raw.info, trans=trans, src=src, bem=bem, meg=False, eeg=True,
                                       mindist=5, n_jobs=1, verbose=False)
    available = [name for name in raw.ch_names if name in fwd["sol"]["row_names"]]
    if len(available) < 8:
        raise AnalysisError("源定位需要至少 8 个与 Forward 匹配的有效 EEG 通道。")
    missing = [name for name in raw.ch_names if name not in available]
    if missing:
        raise AnalysisError("所选 EEG 通道与 Forward 不匹配，请修正通道名称、选择匹配通道或标记坏导。")
    fwd = mne.pick_channels_forward(fwd, include=available, ordered=True, copy=True, verbose=False)
    if fwd_file:
        from mne._fiff.constants import FIFF
        forward_channels = {channel["ch_name"]: channel for channel in fwd["info"]["chs"]}
        for channel in raw.info["chs"]:
            expected = forward_channels.get(channel["ch_name"])
            if expected is None:
                continue
            actual_pos, expected_pos = channel["loc"][:3], expected["loc"][:3]
            if (int(expected["coord_frame"]) == FIFF.FIFFV_COORD_HEAD and np.all(np.isfinite(expected_pos))
                    and np.linalg.norm(expected_pos) > 0 and np.linalg.norm(actual_pos-expected_pos) > .005):
                raise AnalysisError("记录电极位置与上传 Forward 不一致（超过 5 mm），请使用匹配的坐标和模型。")
    # Verify every relevant coordinate frame before algorithms or visualization.
    _to_head(fwd["source_rr"], fwd["coord_frame"], fwd, np, mne)
    for space in fwd["src"]:
        _to_head(space["rr"][space["vertno"]], space["coord_frame"], fwd, np, mne)
    return fwd, bem, trans, template, demo


def _evoked(raw, ctx, np, mne):
    if ctx.events:
        events = np.asarray([[raw.first_samp + round((t-ctx.offset)*raw.info["sfreq"]), 0, 1] for t in ctx.events], dtype=int)
        if len(np.unique(events[:,0])) != len(events):
            raise AnalysisError("事件时间重复或间隔小于一个采样点，请修正事件列表。")
        epochs = mne.Epochs(raw, events, event_id=1, tmin=_number(ctx.p.get("tmin"), -.2),
                            tmax=_number(ctx.p.get("tmax"), .5), baseline=None, preload=True,
                            reject_by_annotation=True, event_repeated="error", verbose=False)
        if len(epochs) < 1:
            raise AnalysisError("事件窗口均被坏段或边界排除，请检查事件和 BAD 注释。")
        ctx.warnings.append(f"使用 {len(epochs)} 个保留的明确事件试次平均；未将采样点冒充试次数。")
        return epochs.average(), epochs, len(epochs), "event_relative"
    clip = _window(raw, ctx)
    evoked = mne.EvokedArray(clip.get_data(), clip.info.copy(), tmin=_clip_start(raw, clip, ctx), nave=1, verbose=False)
    ctx.warnings.append("没有明确事件：本次是一个连续选段的探索性定位，nave=1；不是重复试次平均。")
    return evoked, None, 1, "recording_absolute"


def _noise(raw, ctx, mne):
    cov_file = ctx.file("covariance")
    if cov_file:
        cov = mne.read_cov(str(cov_file["path"]), verbose=False)
        if any(name not in cov["names"] for name in raw.ch_names):
            raise AnalysisError("噪声协方差通道与记录不匹配，请上传匹配矩阵。")
        return mne.pick_channels_cov(cov, include=raw.ch_names, ordered=True, copy=True), "uploaded"
    if ctx.baseline:
        start, end = ctx.baseline
        if end-start < .2:
            raise AnalysisError("噪声基线至少需要 0.2 秒，建议使用更长且无异常的基线。")
        cov = mne.compute_raw_covariance(raw, tmin=start-ctx.offset, tmax=end-ctx.offset-1/raw.info["sfreq"],
                                         picks="eeg", reject_by_annotation=True, method="empirical", verbose=False)
        return cov, "baseline"
    if ctx.p.get("allow_ad_hoc_noise") is not True:
        raise AnalysisError("请提供噪声协方差或明确的记录基线；只有明确同意探索性噪声近似才能省略。")
    ctx.warnings.append("使用固定 2 µV 的 ad-hoc EEG 噪声近似；不是该患者实测噪声，归一化幅值仅供探索。")
    return mne.make_ad_hoc_cov(raw.info, std={"eeg": 2e-6}, verbose=False), "ad_hoc"


def _dics_epochs(raw, ctx, np, mne, baseline=False):
    if baseline:
        if not ctx.baseline: return None
        start, end = ctx.baseline
    else:
        start, end = ctx.start, ctx.start+ctx.duration
    duration = _number(ctx.p.get("dics_window"), min(1, end-start), .2, 5)
    clip = raw.copy().crop(tmin=start-ctx.offset, tmax=min(raw.times[-1], end-ctx.offset-1/raw.info["sfreq"]))
    epochs = mne.make_fixed_length_epochs(clip, duration=duration, overlap=0, preload=True,
                                          reject_by_annotation=True, verbose=False)
    if len(epochs) < 1:
        raise AnalysisError("DICS 没有足够的有效固定窗口，请延长选段或检查 BAD 注释。")
    return epochs


def _solve(raw, ctx, np, mne, method, fwd, bem, trans, evoked, epochs, cov):
    p = ctx.p
    loose = .2 if all(space["type"] == "surf" for space in fwd["src"]) else 1.0
    if method in {"mne", "dspm", "sloreta", "eloreta"}:
        inverse = mne.minimum_norm.make_inverse_operator(evoked.info, fwd, cov, loose=loose, depth=.8, rank=None, verbose=False)
        snr = _number(p.get("snr"), 3, .1, 100)
        params = {"max_iter": 30, "eps": 1e-6} if method == "eloreta" else None
        names = {"mne":"MNE", "dspm":"dSPM", "sloreta":"sLORETA", "eloreta":"eLORETA"}
        return mne.minimum_norm.apply_inverse(evoked, inverse, lambda2=1/snr**2, method=names[method],
                                               method_params=params, verbose=False), None, None
    if method == "lcmv":
        if epochs is not None:
            data_cov = mne.compute_covariance(epochs, method="empirical", rank=None, verbose=False)
        else:
            clip = _window(raw, ctx)
            data_cov = mne.compute_raw_covariance(clip, picks="eeg", method="empirical", reject_by_annotation=True, verbose=False)
        filters = mne.beamformer.make_lcmv(evoked.info, fwd, data_cov, reg=_number(p.get("reg"), .05, .001, 1),
                noise_cov=cov, pick_ori="max-power", weight_norm="unit-noise-gain-invariant", rank=None, verbose=False)
        return mne.beamformer.apply_lcmv(evoked, filters, verbose=False), None, None
    if method == "dics":
        data_epochs = epochs if epochs is not None else _dics_epochs(raw, ctx, np, mne)
        noise_epochs = _dics_epochs(raw, ctx, np, mne, baseline=True)
        low = _number(p.get("fmin"), 8, .01)
        high = _number(p.get("fmax"), 12, .01)
        if high <= low or high >= raw.info["sfreq"]/2:
            raise AnalysisError("DICS 频段须递增并低于奈奎斯特频率。")
        # Same n_fft establishes identical frequency bins for data/noise CSD.
        length = min(data_epochs.get_data(copy=False).shape[-1], noise_epochs.get_data(copy=False).shape[-1] if noise_epochs is not None else 10**9)
        csd = mne.time_frequency.csd_fourier(data_epochs, fmin=low, fmax=high, n_fft=length, n_jobs=1, verbose=False).mean()
        if noise_epochs is None:
            if p.get("allow_ad_hoc_noise") is not True:
                raise AnalysisError("DICS 需要同频段的噪声基线；时间域协方差不能代替噪声 CSD。")
            noise_csd = None
            ctx.warnings.append("DICS 未提供频域噪声基线：结果为未按实测频域噪声归一化的探索性源功率。")
        else:
            noise_csd = mne.time_frequency.csd_fourier(noise_epochs, fmin=low, fmax=high, n_fft=length, n_jobs=1, verbose=False).mean()
        def csd_rank(value):
            matrix = value.get_data(index=0).real
            covariance = mne.Covariance(matrix, value.ch_names, [], value.projs, nfree=1)
            return mne.compute_rank(covariance, info=evoked.info, rank=None, verbose=False)["eeg"]
        data_rank = csd_rank(csd)
        noise_rank = csd_rank(noise_csd) if noise_csd is not None else data_rank
        rank = {"eeg": min(data_rank, noise_rank)}
        if rank["eeg"] < 2:
            raise AnalysisError("DICS 频段有效秩过低，请增加固定窗口数量或拓宽频段。")
        if data_rank != noise_rank:
            ctx.warnings.append(f"DICS 数据/噪声频谱秩不同，采用共同保守秩 {rank['eeg']}；短选段结果需谨慎解释。")
        ctx.warnings.append(f"DICS 使用 {len(data_epochs)} 个有效 {'事件试次' if epochs is not None else '非重叠固定选段（不是刺激试次）'}，频段 {low:g}–{high:g} Hz。")
        filters = mne.beamformer.make_dics(evoked.info, fwd, csd, noise_csd=noise_csd, reg=_number(p.get("reg"), .05, .001, 1),
                pick_ori="max-power", real_filter=True, rank=rank, weight_norm="unit-noise-gain", verbose=False)
        stc, frequencies = mne.beamformer.apply_dics_csd(csd, filters, verbose=False)
        return stc, None, np.asarray(frequencies, float)
    if method in {"rap_music", "trap_music"}:
        number = int(_number(p.get("n_dipoles"), 1, 1, min(3, len(raw.ch_names)-1)))
        fn = mne.beamformer.rap_music if method == "rap_music" else mne.beamformer.trap_music
        ctx.warnings.append("MUSIC 的 GOF 是整组偶极子共同拟合的解释率，不是各偶极子的独立置信度。")
        return None, fn(evoked, fwd, cov, n_dipoles=number, verbose=False), None
    if method == "dipole":
        if bem is None:
            raise AnalysisError("ECD 需要对应的导电头模型和配准。")
        # Each sample is independently fitted; bound cloud CPU without claiming
        # sample averaging increases the number of trials.
        stride = max(1, math.ceil(evoked.data.shape[-1]/60))
        info = evoked.info.copy()
        with info._unlock(): info["sfreq"] /= stride
        selected = mne.EvokedArray(evoked.data[:,::stride], info, tmin=evoked.times[0], nave=evoked.nave, verbose=False)
        # EvokedArray rounds tmin to the reduced sampling grid. Preserve exact
        # original selected-sample times rather than shifting absolute epochs.
        selected._set_times(evoked.times[::stride].copy())
        ctx.warnings.append("ECD 独立拟合最多 60 个选段时点；不是将邻域平均伪造为重复试次。")
        dipole, _ = mne.fit_dipole(selected, cov, bem, trans=trans, n_jobs=1, verbose=False)
        return None, [dipole], None
    maxit = int(_number(p.get("max_iter"), 200, 10, 1000))
    common = {"loose":loose, "depth":.8, "maxit":maxit, "rank":None, "verbose":False}
    if method == "gamma_map":
        alpha = _number(p.get("gamma_alpha", p.get("alpha")), .5, .0001, 100)
        return mne.inverse_sparse.gamma_map(evoked, fwd, cov, alpha=alpha, **common), None, None
    alpha = _number(p.get("alpha"), 50, .001, 99.9)
    iterations = int(_number(p.get("iterations"), 3, 2, 10)) if method in {"irmxne", "ir_tf_mxne"} else 1
    if method in {"mxne", "irmxne"}:
        return mne.inverse_sparse.mixed_norm(evoked, fwd, cov, alpha=alpha, n_mxne_iter=iterations, **common), None, None
    wsize = int(_number(p.get("wsize"), 64, 8, 256))
    tstep = int(_number(p.get("tstep"), 4, 2, 64))
    if wsize%4 or tstep%2 or wsize%tstep or tstep >= wsize/2:
        raise AnalysisError("TF-MxNE 的 wsize 须为 4 的倍数，tstep 为偶数且整除 wsize，并小于 wsize/2。")
    ratio = _number(p.get("l1_ratio"), .5, 0, 1)
    return mne.inverse_sparse.tf_mixed_norm(evoked, fwd, cov, alpha=alpha, l1_ratio=ratio, wsize=wsize, tstep=tstep,
                                            n_tfmxne_iter=iterations, **common), None, None


def _source_positions(stc, fwd, np, mne):
    parts = []
    for vertices, space in zip(stc.vertices, fwd["src"]):
        parts.append(_to_head(space["rr"][vertices], space["coord_frame"], fwd, np, mne))
    return np.concatenate(parts) if parts else np.empty((0,3))


def _geometry(fwd, bem, electrodes, positions, values, np, mne):
    geometry = {"vertices":[], "faces":[], "electrodes":electrodes, "sources":[], "coord_frame":"head (meters)"}
    # Render an actual BEM boundary if available; never mix inflated MRI/MNI
    # anatomy with head-frame source points.
    if isinstance(bem, dict) and bem.get("surfs"):
        surface = bem["surfs"][0]
        if len(surface["rr"]) <= 2500:
            mesh_fwd = dict(fwd)
            vertices = _to_head(surface["rr"], surface["coord_frame"], mesh_fwd, np, mne)
            geometry.update(vertices=vertices.tolist(), faces=surface["tris"].tolist(), surface="BEM boundary")
    budget = max(1, 5000-len(geometry["vertices"])-len(electrodes))
    if len(positions):
        order = np.argsort(np.abs(values))[-budget:]
        geometry["sources"] = [{"x":float(pos[0]), "y":float(pos[1]), "z":float(pos[2]), "value":float(value)} for pos,value in zip(positions[order], values[order])]
    return geometry


def _mri(ctx, fwd, template, peak, np, plt, mne):
    file = ctx.file("mri")
    if template:
        path = Path(os.environ.get("EEG_SUBJECTS_DIR", "")) / "fsaverage" / "mri" / "T1.mgz"
    elif file:
        path = file["path"]
    else:
        return
    if not path.is_file(): return
    import nibabel as nib
    image = nib.load(str(path))
    if len(image.shape) != 3 or math.prod(image.shape) > 67_108_864:
        raise AnalysisError("MRI 须为体积不超过 256³ 的三维结构像。")
    voxel = np.asarray(image.shape, float)/2
    # An arbitrary uploaded MGZ does not become registered merely because its
    # format exposes surface-RAS. Require an explicit assertion of the matching
    # anatomy/transform; the cached template anatomy is known to match.
    registered = (template or ctx.p.get("mri_registered") is True) and hasattr(image.header, "get_vox2ras_tkr") and fwd.get("mri_head_t") is not None
    if registered:
        head_mri = mne.transforms.invert_transform(_head_transform(fwd["mri_head_t"], mne))
        surface_ras_mm = mne.transforms.apply_trans(head_mri, peak)*1000
        proposed = nib.affines.apply_affine(np.linalg.inv(image.header.get_vox2ras_tkr()), surface_ras_mm)
        if np.all(proposed >= 0) and np.all(proposed < np.array(image.shape)):
            voxel = proposed
        else:
            registered = False
    if not registered:
        ctx.warnings.append("MRI 仅显示真实结构像中央切片：缺少可验证的源坐标到该图像的配准，未叠加定位点。")
    # The native fsaverage MGZ array is LIA, not RAS. First resolve the source
    # point with its original surface-RAS header, then reorient both the voxel
    # array and that point together. Axis 0/1/2 now mean R/A/S, respectively.
    original_world = nib.affines.apply_affine(image.affine, voxel)
    image = nib.as_closest_canonical(image)
    voxel = nib.affines.apply_affine(np.linalg.inv(image.affine), original_world)
    data = np.asarray(image.dataobj, dtype=np.float32)
    fig, axes = plt.subplots(1,3, figsize=(10,3.7), layout="constrained")
    indices = np.clip(np.round(voxel).astype(int), 0, np.array(image.shape)-1)
    slices = [data[indices[0],:,:].T, data[:,indices[1],:].T, data[:,:,indices[2]].T]
    cross = [(voxel[1],voxel[2]),(voxel[0],voxel[2]),(voxel[0],voxel[1])]
    vmin, vmax = np.percentile(data, (1,99))
    for ax, section, point, label in zip(axes,slices,cross,("Sagittal","Coronal","Axial")):
        ax.imshow(section, origin="lower", cmap="gray", vmin=vmin, vmax=vmax)
        if registered: ax.axvline(point[0],color="#ec6d45",lw=.8); ax.axhline(point[1],color="#ec6d45",lw=.8)
        ax.set_title(label + (" — registered" if registered else " — no source overlay")); ax.axis("off")
    fig.savefig(ctx.artifact("source-mri.png", "结构 MRI 切片" + ("与源点配准" if registered else "（未配准）"), "image"), dpi=140)
    plt.close(fig)


def compute_source(raw, ctx, np, mne, plt):
    from mne._fiff.constants import FIFF
    method = _method(ctx.p.get("method"))
    if ctx.modality == "seeg" or any(kind == "seeg" for kind in raw.get_channel_types()):
        raise AnalysisError("纯 sEEG 不支持这些头皮 EEG 逆解。请使用波形、双极参考、频谱/时频；不能将深部接点伪装成头皮电极。")
    if any(kind != "eeg" for kind in raw.get_channel_types()):
        raise AnalysisError("头皮源定位只能选择 EEG 通道，请排除 EOG/ECG/EMG 和其他类型。")
    if raw.info["custom_ref_applied"]:
        raise AnalysisError("记录已标记自定义参考，需提供参考处理一致的 Forward/原始单极 EEG；不能直接套用平均参考逆解。")
    positions = _positions(raw, ctx, np, mne, required=True)
    raw.set_eeg_reference(ref_channels="average", projection=True, verbose=False)
    fwd, bem, trans, template, demo = _model(raw, ctx, np, mne, method)
    evoked, epochs, nave, temporal_reference = _evoked(raw, ctx, np, mne)
    cov, noise = _noise(raw, ctx, mne)
    ctx.emit(55, "计算源定位逆解")
    stc, dipoles, frequencies = _solve(raw, ctx, np, mne, method, fwd, bem, trans, evoked, epochs, cov)
    unit = "dimensionless" if method in {"dspm", "sloreta"} else "a.u." if method in {"lcmv", "dics"} else "A m"
    peaks = []
    if stc is not None:
        coordinates = _source_positions(stc, fwd, np, mne)
        data = np.asarray(stc.data)
        if data.size == 0 or not np.all(np.isfinite(data)):
            raise AnalysisError("逆解没有得到有限的活动源，请检查信号、噪声基线与正则参数。")
        scores = np.max(np.abs(data),axis=1)
        for index in np.argsort(scores)[-10:][::-1]:
            time_index = int(np.argmax(np.abs(data[index])))
            point = coordinates[index]
            peaks.append({"x":float(point[0]), "y":float(point[1]), "z":float(point[2]),
                          "time":None if frequencies is not None else float(stc.times[time_index]),
                          "frequency":float(frequencies[time_index]) if frequencies is not None else None,
                          "value":float(data[index,time_index]), "unit":unit})
        times = stc.times if frequencies is None else np.empty(0)
    else:
        coords, amplitudes, all_times = [], [], []
        for dipole in dipoles:
            frame = FIFF.FIFFV_COORD_HEAD if method == "dipole" else fwd["coord_frame"]
            transformed = _to_head(dipole.pos, frame, fwd, np, mne)
            orientation = _to_head(dipole.ori, frame, fwd, np, mne, vectors=True)
            for index in np.argsort(np.abs(dipole.amplitude))[-10:][::-1]:
                point = transformed[index]
                peaks.append({"x":float(point[0]), "y":float(point[1]), "z":float(point[2]), "time":float(dipole.times[index]),
                              "frequency":None, "value":float(dipole.amplitude[index]), "unit":unit,
                              "gof":float(dipole.gof[index]), "gof_scope":"group" if method != "dipole" else "single_dipole",
                              "orientation":orientation[index].tolist()})
            coords.extend(transformed); amplitudes.extend(dipole.amplitude); all_times.extend(dipole.times)
        coordinates, scores, times = np.asarray(coords), np.abs(amplitudes), np.asarray(all_times)
        data = np.asarray(amplitudes)[None,:]
        peaks = sorted(peaks,key=lambda item:abs(item["value"]),reverse=True)[:10]
    geometry = _geometry(fwd, bem, positions["electrodes"], coordinates, scores, np, mne)
    np.savez_compressed(ctx.artifact("source.npz", "源定位数值（head 米坐标）"), data=data, positions_head_m=coordinates,
                        times_s=times, frequencies_hz=frequencies if frequencies is not None else np.empty(0),
                        method=np.asarray(method), unit=np.asarray(unit), nave=np.asarray(nave),
                        temporal_reference=np.asarray(temporal_reference), time_axis=np.asarray("frequency" if frequencies is not None else "seconds"),
                        coord_frame=np.asarray("head (meters)"))
    with ctx.artifact("source-peaks.csv", "活动峰值（head 米坐标）").open("w",encoding="utf-8-sig",newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["x","y","z","time","frequency","value","unit","gof","gof_scope"], extrasaction="ignore")
        writer.writeheader(); writer.writerows(peaks)
    fig, axes = plt.subplots(1,3,figsize=(11,4),layout="constrained")
    for ax,(a,b),label in zip(axes,((0,1),(0,2),(1,2)),("Axial head","Coronal head","Sagittal head")):
        points = np.array([[item["x"],item["y"],item["z"]] for item in geometry["sources"]])
        if len(points): ax.scatter(points[:,a]*1000,points[:,b]*1000,c=[abs(item["value"]) for item in geometry["sources"]],cmap="inferno",s=12)
        sensors = np.array([[item["x"],item["y"],item["z"]] for item in positions["electrodes"]])
        if len(sensors): ax.scatter(sensors[:,a]*1000,sensors[:,b]*1000,c="#3879ac",s=8,alpha=.7)
        ax.set(title=label,xlabel="mm",ylabel="mm",aspect="equal"); ax.grid(alpha=.2)
    fig.suptitle(f"{method} — {unit}; head coordinates; nave={nave}")
    fig.savefig(ctx.artifact("source.png", "源定位投影（head 坐标）", "image"),dpi=140); plt.close(fig)
    if peaks: _mri(ctx,fwd,template,np.array([peaks[0][key] for key in ("x","y","z")]),np,plt,mne)
    return {"method":method,"summary":f"{method} 源定位，保留试次数 {nave}，噪声来源 {noise}。",
            "peaks":peaks,"geometry":geometry,"coord_frame":"head (meters)","unit":unit,"nave":nave,
            "time_axis":"frequency" if frequencies is not None else "seconds", "temporal_reference":temporal_reference,
            "noise_model":noise,"template":template,"demo_only":demo,
            "analysis_window": {"event_times":ctx.events,"tmin":float(evoked.times[0]),"tmax":float(evoked.times[-1])} if ctx.events else
                               {"start":float(evoked.times[0]),"duration":evoked.data.shape[-1]/float(evoked.info["sfreq"])}}
