"""Build a private native depth checkpoint from an imported Studio project."""
import sys
import uuid
from pathlib import Path

from namma.evidence.bundle import verify_checkpoint
from namma.recon.runner import run_step


def train_native(project, run, coordinate_frame, progress, cancel_file, *, steps=15000):
    source = Path(project) / "capture" / "nammacap"
    private = Path(project) / "runs" / run / "evidence" / ("native-" + uuid.uuid4().hex)
    private.mkdir(parents=True)
    reference = private / "reference"
    progress("Checking native depth and adjusting training cameras", .02)
    run_step("native_reference", [sys.executable, "-m", "namma.capture.native_training", source, reference,
             "--coordinate-frame", coordinate_frame, "--bundle-adjust"], private / "logs",
             timeout_s=4*3600, cancel_file=cancel_file)
    progress("Training private Gaussian checkpoint with measured depth", .5)
    run_step("native_training", [sys.executable, "-m", "namma.evidence.train_reference", reference, private,
             "--iters", steps, "--measured-depth"], private / "logs", timeout_s=5*3600, cancel_file=cancel_file)
    version = private / "evidence/v1"
    verify_checkpoint(version)
    return {"kind": "private_native_checkpoint", "experiment": private.name, "version": "v1"}
