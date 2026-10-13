"""Actual MNE algorithms on tiny synthetic data; no patient or network input."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from .engine import AnalysisError, SOURCE_METHODS, run_analysis


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("MPLCONFIGDIR", "/tmp/eeg-matplotlib-tests")
        import numpy as np
        import mne
        cls.np, cls.mne = np, mne
        mne.set_log_level("ERROR")
        cls.directory = tempfile.TemporaryDirectory(prefix="eeg-science-test-")
        cls.root = Path(cls.directory.name)
        cls.channels = ["Fp1", "Fp2", "F3", "F4", "C3", "C4", "P3", "P4"]
        info = mne.create_info(cls.channels, 100, "eeg")
        montage = mne.channels.make_standard_montage("standard_1020")
        montage.apply_trans(mne.channels.compute_native_head_t(montage))
        info.set_montage(montage)
        rr = np.array([[.015,.015,.045],[-.02,.01,.05],[.02,-.02,.04]])
        nn = rr / np.linalg.norm(rr,axis=1)[:,None]
        src = mne.setup_volume_source_space(pos={"rr":rr,"nn":nn},sphere=(0,0,0,.075),add_interpolator=False,verbose=False)
        sphere = mne.make_sphere_model(r0=(0,0,0),head_radius=.09,verbose=False)
        cls.forward = mne.make_forward_solution(info,trans=None,src=src,bem=sphere,eeg=True,meg=False,n_jobs=1,verbose=False)
        rng = np.random.RandomState(97)
        times = np.arange(600)/100
        currents = np.zeros((9,600))
        currents[2] = 2e-8*np.sin(2*np.pi*10*times) * (times >= 2)
        data = cls.forward["sol"]["data"] @ currents + rng.normal(scale=.15e-6,size=(8,600))
        raw = mne.io.RawArray(data,info,verbose=False)
        raw.save(str(cls.root/"synthetic_raw.fif"),overwrite=True,verbose=False)
        mne.write_forward_solution(str(cls.root/"synthetic-fwd.fif"),cls.forward,overwrite=True,verbose=False)
        cls.files = [{"path":str(cls.root/"synthetic_raw.fif"),"name":"synthetic_raw.fif","role":"raw"},
                     {"path":str(cls.root/"synthetic-fwd.fif"),"name":"synthetic-fwd.fif","role":"forward"}]
        cls.raw = raw

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def analyze(self, operation, parameters=None, modality="eeg", files=None):
        output = self.root / (self.id().split(".")[-1] + "-" + operation + "-" + str(len(list(self.root.iterdir()))))
        result = run_analysis({"operation":operation,"modality":modality,"files":files or self.files,
                               "parameters":parameters or {}},output)
        json.dumps(result,allow_nan=False)
        for artifact in result["artifacts"]:
            self.assertEqual(Path(artifact["name"]).name,artifact["name"])
            self.assertTrue((output/artifact["name"]).is_file())
        return result, output

    def test_inspection_and_waveform_keep_si_units_names_and_bads(self):
        result,_ = self.analyze("inspect")
        self.assertEqual(result["sfreq"],100)
        self.assertEqual(len(result["geometry"]["electrodes"]),8)
        wave,_ = self.analyze("waveform",{"start":2,"duration":1,"renames":{"Fp1":"left"},"picks":["Fp1","Fp2"],"bads":["Fp2"]})
        self.assertEqual(wave["channels"],["left"])
        self.np.testing.assert_allclose(wave["data_uv"][0],self.raw.get_data(picks=[0],start=200,stop=300)[0]*1e6,rtol=1e-6)

    def test_display_preserves_single_sample_spike(self):
        raw = self.mne.io.RawArray(self.np.zeros((1,5000)),self.mne.create_info(["C3"],1000,"eeg"),verbose=False)
        raw._data[0,2345] = 100e-6
        path = self.root/"spike_raw.fif"; raw.save(str(path),overwrite=True,verbose=False)
        wave,_ = self.analyze("waveform",{"duration":5},files=[{"path":str(path),"name":path.name,"role":"raw"}])
        self.assertLessEqual(len(wave["times"]),2000)
        self.assertAlmostEqual(max(wave["data_uv"][0]),100,places=4)
        self.assertTrue(all(a<b for a,b in zip(wave["times"],wave["times"][1:])))

    def test_psd_and_all_tfr_methods_locate_10hz(self):
        result,output = self.analyze("psd",{"start":2,"duration":3,"picks":["C3"],"fmin":5,"fmax":20})
        numeric = self.np.genfromtxt(output/"psd.csv",delimiter=",",skip_header=1)
        self.assertAlmostEqual(numeric[numeric[:,1].argmax(),0],10,delta=.6)
        for method in ("morlet","multitaper","stockwell"):
            with self.subTest(method=method):
                result,output = self.analyze("tfr",{"start":2,"duration":3,"picks":["C3"],"fmin":5,"fmax":20,"freq_step":1,"tfr_method":method,"n_cycles":7})
                with self.np.load(output/"tfr.npz",allow_pickle=False) as stored:
                    band=stored["power_uv2"].mean(axis=(0,2)); frequencies=stored["frequencies_hz"]
                    self.assertAlmostEqual(frequencies[band.argmax()],10,delta=2)
                self.assertEqual(result["observations"],1)
                self.assertTrue(any(item.get("channel")=="C3" for item in result["artifacts"]))

    def test_bipolar_and_seeg_no_fake_source(self):
        wave,_=self.analyze("waveform",{"start":2,"duration":1,"picks":["Fp1","Fp2"],"reference":"bipolar","bipolar_pairs":[["Fp1","Fp2"]]},"seeg")
        expected=(self.raw.get_data(picks=[0],start=200,stop=300)-self.raw.get_data(picks=[1],start=200,stop=300))*1e6
        self.np.testing.assert_allclose(wave["data_uv"],expected,rtol=1e-6,atol=1e-6)
        with self.assertRaisesRegex(AnalysisError,"sEEG"):
            self.analyze("source",{"start":2,"duration":.4},"seeg")

    def test_all_14_real_inverse_algorithms(self):
        for method in SOURCE_METHODS:
            with self.subTest(method=method):
                result,output = self.analyze("source",{"method":method,"start":2,"duration":.8,"baseline_start":0,"baseline_end":1.5,
                         "head_model":"sphere","demo_only":True,"alpha":20,"gamma_alpha":.5,"max_iter":50,
                         "fmin":8,"fmax":12,"dics_window":.5,"n_dipoles":1})
                self.assertEqual(result["method"],method)
                self.assertEqual(result["coord_frame"],"head (meters)")
                self.assertEqual(result["nave"],1)
                self.assertTrue(result["peaks"])
                self.assertLessEqual(len(result["geometry"]["sources"]),5000)
                self.assertTrue(all(self.np.isfinite(peak["value"]) for peak in result["peaks"]))
                self.assertIn("average EEG projector",result["provenance"]["reference"])
                with self.np.load(output/"source.npz",allow_pickle=False) as saved:
                    self.assertEqual(str(saved["coord_frame"]),"head (meters)")
                    self.assertEqual(str(saved["temporal_reference"]),"recording_absolute")
                    self.assertEqual(str(saved["time_axis"]),result["time_axis"])
                if method == "dics":
                    self.assertEqual(result["time_axis"],"frequency")
                    self.assertTrue(all(peak["time"] is None and 8 <= peak["frequency"] <=12 for peak in result["peaks"]))
                elif method=="eloreta": self.assertEqual(result["unit"],"A m")
                if method == "dipole":
                    for peak in result["peaks"]:
                        self.assertTrue(self.np.any(self.np.isclose(peak["time"],self.np.arange(200,280,2)/100)))

    def test_event_average_retains_real_trial_count_and_rejects_bad(self):
        raw=self.raw.copy();raw.set_annotations(self.mne.Annotations([3.8],[.5],["BAD synthetic"]))
        path=self.root/"event_raw.fif";raw.save(str(path),overwrite=True,verbose=False)
        files=[{"path":str(path),"name":path.name,"role":"raw"},self.files[1]]
        result,_=self.analyze("source",{"method":"mne","event_times":[2.5,3,4],"tmin":-.1,"tmax":.2,"baseline_start":0,"baseline_end":1.5},files=files)
        self.assertEqual(result["nave"],2)
        self.assertEqual(result["temporal_reference"],"event_relative")

    def test_last_event_endpoint_and_nonzero_first_sample_retained(self):
        raw=self.mne.io.RawArray(self.raw.get_data(),self.raw.info.copy(),first_samp=100,verbose=False)
        path=self.root/"offset_raw.fif";raw.save(str(path),overwrite=True,verbose=False)
        result,_=self.analyze("source",{"method":"mne","event_times":[3],"tmin":-.2,"tmax":.5,"baseline_start":0,"baseline_end":1},
                              files=[{"path":str(path),"name":path.name,"role":"raw"},self.files[1]])
        self.assertEqual(result["nave"],1)
        self.assertEqual(result["temporal_reference"],"event_relative")

    def test_source_requires_explicit_noise_and_no_runtime_download(self):
        with self.assertRaisesRegex(AnalysisError,"噪声"):
            self.analyze("source",{"method":"mne","start":2,"duration":.5})
        from unittest.mock import patch
        with patch.dict(os.environ,{"EEG_SUBJECTS_DIR":str(self.root/"missing")}), patch("mne.datasets.fetch_fsaverage") as download:
            with self.assertRaisesRegex(AnalysisError,"缓存"):
                self.analyze("source",{"method":"mne","head_model":"fsaverage"},files=[self.files[0]])
            download.assert_not_called()

    def test_mri_forward_display_coordinates_are_transformed(self):
        from .source_algorithms import _to_head
        transform=self.mne.transforms.Transform("mri","head",self.np.array([[1,0,0,.02],[0,1,0,-.01],[0,0,1,.03],[0,0,0,1]]))
        points=self.np.array([[.01,.02,.03]])
        output=_to_head(points,5,{"mri_head_t":transform},self.np,self.mne)
        self.np.testing.assert_allclose(output,[[.03,.01,.06]])
        self.np.testing.assert_allclose(_to_head(points,5,{"mri_head_t":transform},self.np,self.mne,vectors=True),points)

    def test_uploaded_mri_frame_forward_roundtrip_has_head_peaks(self):
        from mne._fiff.constants import FIFF
        fwd=self.forward.copy()
        transform=self.mne.transforms.Transform("mri","head",self.np.array([[1,0,0,.01],[0,1,0,-.005],[0,0,1,.015],[0,0,0,1]]))
        inverse=self.mne.transforms.invert_transform(transform)
        fwd["mri_head_t"]=transform;fwd["coord_frame"]=FIFF.FIFFV_COORD_MRI
        # The FIF writer stores the transform from the parent-MRI info block,
        # not solely from fwd['mri_head_t']; both metadata copies must agree.
        with fwd["info"]._unlock():
            fwd["info"]["mri_head_t"]=transform
        fwd["source_rr"]=self.mne.transforms.apply_trans(inverse,fwd["source_rr"])
        for space in fwd["src"]:
            space["rr"]=self.mne.transforms.apply_trans(inverse,space["rr"])
            space["coord_frame"]=FIFF.FIFFV_COORD_MRI
        path=self.root/"mri-coord-fwd.fif";self.mne.write_forward_solution(str(path),fwd,overwrite=True,verbose=False)
        result,_=self.analyze("source",{"method":"mne","start":2,"duration":.5,"baseline_start":0,"baseline_end":1.5},
                              files=[self.files[0],{"path":str(path),"name":path.name,"role":"forward"}])
        actual=self.np.array([[peak[k] for k in ("x","y","z")] for peak in result["peaks"]])
        for point in actual:
            self.assertLess(self.np.linalg.norm(self.forward["source_rr"]-point,axis=1).min(),1e-6)

    def test_partial_montage_preserves_measured_positions(self):
        from .engine import Context,_positions
        raw=self.raw.copy()
        original=raw.info["chs"][0]["loc"][:3].copy()
        raw.info["chs"][1]["loc"][:3]=self.np.nan
        context=Context({"operation":"inspect","files":self.files},self.root/"mixed",None)
        _positions(raw,context,self.np,self.mne)
        self.np.testing.assert_allclose(raw.info["chs"][0]["loc"][:3],original)
        self.assertTrue(any("1 个缺失" in warning for warning in context.warnings))

    def test_forward_geometry_mismatch_rejected(self):
        raw=self.raw.copy();raw.info["chs"][0]["loc"][0]+=.02
        path=self.root/"mismatch_raw.fif";raw.save(str(path),overwrite=True,verbose=False)
        with self.assertRaisesRegex(AnalysisError,"Forward 不一致"):
            self.analyze("source",{"method":"mne","start":2,"duration":.5,"baseline_start":0,"baseline_end":1.5},
                         files=[{"path":str(path),"name":path.name,"role":"raw"},self.files[1]])

    def test_cached_true_fsaverage_without_network(self):
        subjects=Path(os.environ.get("EEG_SUBJECTS_DIR") or "")
        if not (subjects/"fsaverage"/"bem"/"fsaverage-ico3-bem-sol.fif").is_file():
            self.skipTest("public fsaverage cache not installed")
        from unittest.mock import patch
        with patch("mne.datasets.fetch_fsaverage") as download:
            result,_=self.analyze("source",{"method":"mne","head_model":"fsaverage","start":2,"duration":.3,"baseline_start":0,"baseline_end":1.5},files=[self.files[0]])
            download.assert_not_called()
        self.assertTrue(result["template"])
        self.assertTrue(any(item["name"]=="source-mri.png" for item in result["artifacts"]))

    def test_non_ras_mri_planes_and_registered_point_reoriented_together(self):
        import nibabel as nib
        import matplotlib.pyplot as plt
        from unittest.mock import patch
        from .engine import Context
        from .source_algorithms import _mri
        np=self.np
        data=np.arange(4*5*6,dtype=np.float32).reshape(4,5,6)
        affine=np.array([[-2,0,0,10],[0,0,4,-20],[0,-3,0,30],[0,0,0,1]],float)
        image=nib.MGHImage(data,affine)
        self.assertEqual(nib.aff2axcodes(image.affine),("L","I","A"))
        path=self.root/"orientation.mgz";nib.save(image,str(path))
        image=nib.load(str(path))
        original_voxel=np.array([1,2,3],float)
        peak=nib.affines.apply_affine(image.header.get_vox2ras_tkr(),original_voxel)/1000
        expected_image=nib.as_closest_canonical(image)
        expected_voxel=nib.affines.apply_affine(np.linalg.inv(expected_image.affine),nib.affines.apply_affine(image.affine,original_voxel))
        self.assertEqual(nib.aff2axcodes(expected_image.affine),("R","A","S"))
        files=[self.files[0],{"path":str(path),"name":path.name,"role":"mri"}]
        context=Context({"operation":"source","files":files,"parameters":{"mri_registered":True}},self.root/"orientation-output",None)
        captures=[];subplots=plt.subplots
        def capture(*args,**kwargs):
            result=subplots(*args,**kwargs);captures.append(result);return result
        with patch.object(plt,"subplots",side_effect=capture):
            _mri(context,{"mri_head_t":self.mne.transforms.Transform("mri","head")},False,peak,np,plt,self.mne)
        axes=captures[0][1]
        expected_data=np.asarray(expected_image.dataobj)
        i,j,k=np.round(expected_voxel).astype(int)
        for ax,label,section,cross in zip(axes,("Sagittal","Coronal","Axial"),
                (expected_data[i,:,:].T,expected_data[:,j,:].T,expected_data[:,:,k].T),
                ((expected_voxel[1],expected_voxel[2]),(expected_voxel[0],expected_voxel[2]),(expected_voxel[0],expected_voxel[1]))):
            self.assertEqual(ax.get_title(),label+" — registered")
            np.testing.assert_array_equal(ax.images[0].get_array(),section)
            np.testing.assert_allclose(ax.lines[0].get_xdata(),[cross[0],cross[0]])
            np.testing.assert_allclose(ax.lines[1].get_ydata(),[cross[1],cross[1]])
        self.assertTrue((context.output/"source-mri.png").is_file())

    def test_compressed_fif_and_csv_coordinates(self):
        path=self.root/"compressed_raw.fif.gz";self.raw.save(str(path),overwrite=True,verbose=False)
        coordinate=self.root/"electrodes.csv"
        coordinate.write_text("name,x,y,z,unit,coord_frame\n"+"\n".join(f"{name},{pos[0]*1000},{pos[1]*1000},{pos[2]*1000},mm,head" for name,pos in zip(self.raw.ch_names,self.raw._get_channel_positions())),encoding="utf-8")
        result,_=self.analyze("inspect",files=[{"path":str(path),"name":path.name,"role":"raw"},{"path":str(coordinate),"name":coordinate.name,"role":"electrodes"}])
        self.assertEqual(len(result["geometry"]["electrodes"]),8)

    def test_preprocess_ica_explicit_exclusion_preserves_original_file(self):
        result,output=self.analyze("preprocess",{"start":2,"duration":3,"picks":["Fp1","Fp2","F3","F4"],
                                                 "ica":True,"ica_components":2,"ica_exclude":[]})
        exported=self.mne.io.read_raw_fif(str(output/"processed_raw.fif"),preload=True,verbose=False)
        self.np.testing.assert_allclose(exported.get_data(),self.raw.get_data(picks=[0,1,2,3],start=200,stop=500),rtol=1e-5,atol=1e-12)
        self.assertTrue((output/"ica.fif").is_file())
        self.assertTrue(any("人工" in artifact["label"] for artifact in result["artifacts"]))

    def test_cli_failure_is_private_and_progress_is_atomic(self):
        folder=self.root/"cli";folder.mkdir()
        input_path=folder/"input.json";input_path.write_text(json.dumps({"operation":"invalid"}),encoding="utf-8")
        output_path=folder/"result.json"
        process=subprocess.run([sys.executable,"-m","eeg.engine",str(input_path),str(output_path)],capture_output=True,text=True)
        self.assertNotEqual(process.returncode,0)
        result=json.loads(output_path.read_text())
        self.assertEqual(result["status"],"failed")
        self.assertNotIn(str(folder),result["error"])
        self.assertTrue((folder/"traceback-private.txt").exists())
        self.assertEqual(json.loads((folder/"progress.json").read_text())["progress"],100)


if __name__ == "__main__": unittest.main()
