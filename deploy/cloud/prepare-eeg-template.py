"""Run once during installation, never inside a patient analysis job.

Downloads only MNE's checksummed public fsaverage assets. Prepared files use
ico3 BEM / ico4 sources to fit a small CPU server's memory budget.
"""
import argparse
import os
from pathlib import Path

os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')
import mne

parser = argparse.ArgumentParser()
parser.add_argument('subjects_dir', type=Path)
parser.add_argument('--download', action='store_true')
args = parser.parse_args()
subjects_dir = args.subjects_dir.resolve()
if args.download:
    mne.datasets.fetch_fsaverage(subjects_dir=subjects_dir)
subject = subjects_dir / 'fsaverage'
bem_dir = subject / 'bem'
source_path = bem_dir / 'fsaverage-ico4-src.fif'
bem_path = bem_dir / 'fsaverage-ico3-bem-sol.fif'
if not source_path.exists():
    src = mne.setup_source_space('fsaverage', spacing='ico4', subjects_dir=subjects_dir, add_dist=False, n_jobs=1)
    mne.write_source_spaces(source_path, src, overwrite=True)
if not bem_path.exists():
    model = mne.make_bem_model('fsaverage', ico=3, conductivity=(.3,.006,.3), subjects_dir=subjects_dir)
    solution = mne.make_bem_solution(model)
    mne.write_bem_solution(bem_path, solution, overwrite=True)
print('Prepared standard template:', source_path, bem_path)
