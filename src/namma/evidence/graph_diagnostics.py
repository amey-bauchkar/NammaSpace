"""Read-only full-scene correspondence graph diagnostics."""
import argparse
from pathlib import Path
import sqlite3

from namma.core.jsonio import write_json


def inspect(database):
    connection = sqlite3.connect(database.resolve().as_uri()+"?mode=ro",uri=True)
    try:
        names = dict(connection.execute("SELECT image_id,name FROM images"))
        keypoints = dict(connection.execute("SELECT image_id,rows FROM keypoints"))
        raw = dict(connection.execute("SELECT pair_id,rows FROM matches"))
        verified = dict(connection.execute("SELECT pair_id,rows FROM two_view_geometries"))
    finally:
        connection.close()
    adjacency = {image:set() for image in names}
    for pair,count in verified.items():
        if count<8:
            continue
        a,b = divmod(pair,2147483647)
        adjacency[a].add(b)
        adjacency[b].add(a)
    remaining = set(names)
    components = []
    while remaining:
        pending,part = [min(remaining)],set()
        while pending:
            image = pending.pop()
            if image not in part:
                part.add(image)
                pending.extend(adjacency[image]-part)
        remaining -= part
        components.append(sorted(part))
    components.sort(key=lambda part:-len(part))
    labels = {image:group for group,part in enumerate(components) for image in part}
    rejected = []
    for pair,count in raw.items():
        a,b = divmod(pair,2147483647)
        if labels[a]!=labels[b]:
            rejected.append({"images":[names[a],names[b]],"raw_matches":count,
                             "verified_inliers":verified.get(pair,0)})
    order = sorted(names,key=lambda image:names[image])
    gaps = [{"images":[names[a],names[b]],"components":[labels[a],labels[b]],
             "raw_matches":raw.get(min(a,b)*2147483647+max(a,b),0),
             "verified_inliers":verified.get(min(a,b)*2147483647+max(a,b),0)}
            for a,b in zip(order,order[1:]) if labels[a]!=labels[b]]
    return {"images":len(names),"component_sizes":[len(part) for part in components],
            "verified_edges":sum(count>=8 for count in verified.values()),
            "candidate_pairs":len(raw),"connected":len(components)==1,
            "isolated":[{"image":names[i],"keypoints":keypoints.get(i,0)} for i in order if not adjacency[i]],
            "components":[[names[i] for i in part] for part in components],
            "temporal_component_boundaries":gaps,
            "cross_component_rejected":sorted(rejected,key=lambda pair:-pair["raw_matches"]),
            "weak_images":[{"image":names[i],"keypoints":keypoints.get(i,0),"degree":len(adjacency[i])}
                           for i in order if keypoints.get(i,0)<64 or len(adjacency[i])<2]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database",type=Path)
    parser.add_argument("output",type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = inspect(args.database)
    write_json(args.output,result)
    print({key:result[key] for key in ("images","component_sizes","verified_edges","candidate_pairs","connected")})
    print("isolated",len(result["isolated"]),"weak",len(result["weak_images"]),
          "temporal_boundaries",len(result["temporal_component_boundaries"]))
