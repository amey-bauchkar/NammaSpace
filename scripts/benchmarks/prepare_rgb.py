"""Import locally permitted Deep Blending or Mip-NeRF 360 COLMAP data."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"src"))
from namma.evidence.benchmark import prepare
from namma.core.jsonio import load_strict

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("scene","output","splits","permission"):
        parser.add_argument(key,type=Path)
    args = parser.parse_args()
    print(prepare(args.scene,args.output,load_strict(args.splits),load_strict(args.permission)))
