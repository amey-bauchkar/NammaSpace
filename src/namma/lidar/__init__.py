"""LiDAR capture support (Record3D exports first): inventory / validation, point clouds, experimental meshing.

    namma.lidar.inventory   read-only inventory + capability report of an export (implemented)
    namma.lidar.ply         PLY header inspection, memory-mapped vertex reading (implemented)
    namma.lidar.pointcloud  voxel fusion, outlier filtering, frame-sequence checks (implemented)
    namma.lidar.mesh        Poisson surface -> vertex-coloured GLB (EXPERIMENTAL, synthetic tests only)

The photo pipeline (namma.studio.pipeline) is unchanged and remains the fallback. See docs/RECORD3D_LIDAR.md.
"""
