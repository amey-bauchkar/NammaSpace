"""Private experimental native training job using the Studio worker status machinery."""
import shutil

from namma.capture.native_training import prepare
from namma.core.jsonio import load_strict, write_json
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
    required = sum(item["bytes"] for item in integrity["files"])
    if shutil.disk_usage(version).free < required*3 + 2*2**30:
        raise ValueError("insufficient disk space for native evidence and decoded crops")
    if cancel_file and cancel_file.exists():
        raise ValueError("native preparation cancelled")
    progress("Solving native training cameras", .15)
    reference = version / "reference"
    prepare(capture, reference, frame_id, bundle_adjust=True, loop_candidates=True, retrieval_k=5)
    prior_dir = None
    if options.get("native_promptda"):
        from namma.recon.runner import run_step
        from namma import PROJECT_ROOT
        config = options["native_promptda"]
        prior_dir = reference/"estimated_depth"
        prior_dir.mkdir()
        for sensor in sorted((reference/"depth").glob("*.npy")):
            stage = reference/"promptda"/sensor.stem
            run_step("promptda_"+sensor.stem, [config["python"],PROJECT_ROOT/"scripts/depth/promptda_export.py",
                     config["source"],config["checkpoint"],config["approval"],
                     reference/"images"/(sensor.stem+".jpg"),sensor,stage,
                     "--device",config.get("device","cpu"),"--side",config.get("side",504)],version/"logs",timeout_s=3600,cancel_file=cancel_file)
            shutil.copyfile(stage/"estimated_depth_m.npy",prior_dir/sensor.name)
    progress("Training native-resolution private checkpoint", .35)
    checkpoint = train_reference(reference, run_dir, iters=int(options.get("native_iters", 15000)),
                                 cap=int(options.get("native_cap", 300000)), native_crops=True,
                                 depth=True, cancel_file=cancel_file,
                                 training_options=options.get("native_training_options"),estimated_depth=prior_dir)
    report.update(status="checkpoint_ready", checkpoint=str(checkpoint.relative_to(run_dir)),
                  evaluation="pending independent registration", published=False)
    write_json(version / "job.json", report)
    return report
