"""Run the publisher's paired example through the locally pinned PromptDA exporter."""
from pathlib import Path
import sys
import numpy as np
from PIL import Image

from promptda_export import export

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    source = root/"tools/promptda/source"
    weights = root/"tools/promptda/weights/model.ckpt"
    sensor = root/"tools/promptda/smoke_sensor.npy"
    with Image.open(source/"assets/example_images/arkit_depth.png") as image:
        depth = np.asarray(image).astype(np.float32)/1000
    np.save(sensor,depth)
    export(source,weights,root/"tools/promptda/approval.json",
           source/"assets/example_images/image.jpg",sensor,Path(sys.argv[1]),side=252)
