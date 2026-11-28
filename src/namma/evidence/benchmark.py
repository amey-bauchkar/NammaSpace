"""Prepare RGB-only published-calibration benchmarks without claiming independent sensor geometry."""
import shutil
from pathlib import Path

from PIL import Image

from namma.core.jsonio import sha256_file, write_json
from namma.recon.colmap import read_images_txt
from namma.recon.runner import run_step
from namma.recon.tools import find_tools


def prepare(scene, output, split, permission):
    if not permission.get("source") or not permission.get("terms") or permission.get("research_use_approved") is not True:
        raise ValueError("dataset use requires recorded source, terms and permission")
    groups = {role: set(split[role]) for role in ("train","dev","final_test")}
    if any(not group for group in groups.values()) or any(
            groups[a]&groups[b] for a,b in (("train","dev"),("train","final_test"),("dev","final_test"))):
        raise ValueError("benchmark partitions must be nonempty and disjoint")
    scene, output = Path(scene), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    sparse = scene / "sparse/0"
    if not sparse.is_dir():
        sparse = scene / "sparse"
    if not (sparse / "images.txt").is_file():
        converted = output / "input_model"
        converted.mkdir()
        run_step("convert_benchmark", [find_tools(require_openmvs=False).colmap,"model_converter",
                 "--input_path",sparse,"--output_path",converted,"--output_type","TXT"],output/"logs",timeout_s=600)
        sparse = converted
    cameras = {}
    for line in (sparse / "cameras.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        camera, kind, width, height, *params = line.split()
        if kind not in {"PINHOLE","SIMPLE_PINHOLE"}:
            raise ValueError("benchmark requires undistorted pinhole calibration")
        cameras[int(camera)] = (int(width),int(height))
    poses = read_images_txt(sparse / "images.txt")
    wanted = set.union(*groups.values())
    if not wanted <= set(poses):
        raise ValueError("benchmark partition names absent from calibration")
    train_ids = set()
    model, images = output / "model", output / "images"
    model.mkdir()
    images.mkdir()
    shutil.copyfile(sparse/"cameras.txt", model/"cameras.txt")
    lines = [line for line in (sparse/"images.txt").read_text().splitlines() if not line.startswith("#")]
    with (model/"images.txt").open("w",encoding="utf-8") as stream:
        for i in range(0,len(lines),2):
            fields = lines[i].split()
            if len(fields)<10:
                continue
            name = " ".join(fields[9:])
            if name not in groups["train"]:
                continue
            source = (scene/"images"/name).resolve()
            if not source.is_relative_to((scene/"images").resolve()):
                raise ValueError("unsafe calibrated image path")
            with Image.open(source) as photo:
                if photo.size != cameras[int(fields[8])]:
                    raise ValueError("image size and calibration differ")
            target = images/name
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
            train_ids.add(int(fields[0]))
            stream.write(lines[i]+"\n\n")
    points = 0
    with (model/"points3D.txt").open("w",encoding="utf-8") as stream:
        for line in (sparse/"points3D.txt").read_text().splitlines():
            if not line or line.startswith("#"):
                continue
            p = line.split()
            track = set(map(int,p[8::2]))
            if len(track&train_ids)>=2:
                stream.write(" ".join(p[:8])+"\n")
                points += 1
    if points<4:
        raise ValueError("insufficient training-visible benchmark seeds")
    write_json(output/"splits.json",split)
    report = {"protocol":"published_allview_calibration", "independent_geometry":False,
              "measured_lidar":False, "metric_scale_verified":False,
              "depth_supervision":"none", "permissions":permission, "seed_points":points,
              "source_model":{p.name:sha256_file(p) for p in sparse.iterdir() if p.is_file()},
              "source_sparse":str(sparse.resolve()),
              "training_images":{name:sha256_file(images/name) for name in sorted(groups["train"])},
              "evaluation_source":str((scene/"images").resolve())}
    write_json(output/"benchmark.json",report)
    return report
