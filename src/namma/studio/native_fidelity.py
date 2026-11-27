"""Private experimental native training job using the Studio worker status machinery."""
import shutil
import zipfile

from namma.capture.native_training import prepare
from namma.core.jsonio import load_strict, sha256_file, write_json
from namma.evidence.bundle import allocate_version
from namma.evidence.train_reference import train_reference
from namma.studio.splat_claude import available, SplatError


def run(project, run_id, options, *, cancel_file=None, progress=None):
    ok, why = available()
    if not ok:
        raise SplatError(why)
    frame_id = options.get("native_coordinate_frame")
    if not isinstance(frame_id, int):
        raise ValueError("select native_coordinate_frame explicitly")
    capture = project / "capture/nammacap"
    integrity = load_strict(capture / "integrity.json")
    run_dir = project / "runs" / run_id
    version = allocate_version(run_dir)
    report = {"status": "preparing", "version": version.name}
    write_json(version / "job.json", report)
    progress = progress or (lambda *_: None)
    members = integrity["files"]
    required = sum(item["bytes"] for item in members)
    if shutil.disk_usage(version).free < required*3 + 2*2**30:
        raise ValueError("insufficient disk space for native evidence and decoded crops")
    archive = version / "native.nammacap"
    progress("Verifying native capture", .05)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_STORED) as output:
        output.write(capture / "integrity.json", "integrity.json")
        for member in members:
            rel = member["path"]
            path = (capture / rel).resolve()
            if not path.is_relative_to(capture.resolve()) or not path.is_file():
                raise ValueError("invalid native integrity member")
            if sha256_file(path) != member["sha256"] or path.stat().st_size != member["bytes"]:
                raise ValueError("native capture integrity mismatch")
            output.write(path, rel)
    if cancel_file and cancel_file.exists():
        raise ValueError("native preparation cancelled")
    progress("Solving native training cameras", .15)
    reference = version / "reference"
    prepare(archive, reference, frame_id, bundle_adjust=True, loop_candidates=True, retrieval_k=5)
    progress("Training native-resolution private checkpoint", .35)
    checkpoint = train_reference(reference, run_dir, iters=int(options.get("native_iters", 15000)),
                                 cap=int(options.get("native_cap", 300000)), native_crops=True,
                                 depth=True, cancel_file=cancel_file,
                                 training_options=options.get("native_training_options"))
    report.update(status="checkpoint_ready", checkpoint=str(checkpoint.relative_to(run_dir)),
                  evaluation="pending independent registration", published=False)
    write_json(version / "job.json", report)
    return report
