import Foundation
import simd

/// A rigid camera pose in ARKit conventions: T_world_from_camera (camera-to-world), meters,
/// world +Y up, camera +X right / +Y up / -Z forward.
public struct Pose: Sendable, Equatable {
    public var matrix: simd_double4x4

    public init(_ m: simd_double4x4) { matrix = m }
    public init(_ m: simd_float4x4) {
        matrix = simd_double4x4(columns: (SIMD4<Double>(m.columns.0), SIMD4<Double>(m.columns.1),
                                          SIMD4<Double>(m.columns.2), SIMD4<Double>(m.columns.3)))
    }
    public init?(rowMajor r: [[Double]]) {
        guard r.count == 4, r.allSatisfy({ $0.count == 4 }) else { return nil }
        matrix = simd_double4x4(rows: r.map { SIMD4<Double>($0[0], $0[1], $0[2], $0[3]) })
    }
    public static let identity = Pose(matrix_identity_double4x4)

    public var position: SIMD3<Double> { SIMD3(matrix.columns.3.x, matrix.columns.3.y, matrix.columns.3.z) }
    public var rotation: simd_double3x3 {
        simd_double3x3(columns: (SIMD3(matrix.columns.0.x, matrix.columns.0.y, matrix.columns.0.z),
                                 SIMD3(matrix.columns.1.x, matrix.columns.1.y, matrix.columns.1.z),
                                 SIMD3(matrix.columns.2.x, matrix.columns.2.y, matrix.columns.2.z)))
    }
    /// Unit viewing direction in world coordinates (camera -Z).
    public var forward: SIMD3<Double> { simd_normalize(-SIMD3(matrix.columns.2.x, matrix.columns.2.y, matrix.columns.2.z)) }

    /// Row-major nested arrays, as stored in the archive.
    public var rowMajor: [[Double]] {
        (0..<4).map { r in (0..<4).map { c in matrix[c][r] } }
    }

    public var quaternionXYZW: [Double] {
        let q = simd_quatd(rotation)
        return [q.imag.x, q.imag.y, q.imag.z, q.real]
    }

    public func distance(to o: Pose) -> Double { simd_distance(position, o.position) }

    /// Angle (degrees) of the relative rotation between two poses.
    public func rotationDegrees(to o: Pose) -> Double {
        let rel = simd_mul(rotation.transpose, o.rotation)
        let c = max(-1.0, min(1.0, (rel[0][0] + rel[1][1] + rel[2][2] - 1) / 2))
        return acos(c) * 180 / .pi
    }

    /// Heading of the viewing direction projected on the floor plane, degrees in [0, 360), 0 = world -Z.
    public var headingDegrees: Double {
        let f = forward
        var a = atan2(f.x, -f.z) * 180 / .pi
        if a < 0 { a += 360 }
        return a
    }

    /// Estimated body pivot: `armM` behind the camera along the horizontal viewing direction (the point a person
    /// turns around when they rotate on the spot holding the phone out). Equals `position` for armM = 0 or when
    /// looking straight up/down.
    public func pivot(armM: Double) -> SIMD3<Double> {
        let f = forward
        let h = SIMD2(f.x, f.z)
        let n = simd_length(h)
        guard armM > 0, n > 0.2 else { return position }
        return position - armM * SIMD3(f.x / n, 0, f.z / n)
    }

    /// Pitch of the viewing direction, degrees, + = looking up.
    public var pitchDegrees: Double { asin(max(-1, min(1, forward.y))) * 180 / .pi }

    /// COLMAP extrinsics (world-to-camera, camera +X right, +Y down, +Z forward):
    /// T_cam_from_world = diag(1,-1,-1,1) * inverse(T_world_from_camera). Returns (qw,qx,qy,qz, tx,ty,tz).
    public var colmapExtrinsics: (qw: Double, qx: Double, qy: Double, qz: Double, t: SIMD3<Double>) {
        let flip = simd_double3x3(diagonal: SIMD3(1, -1, -1))
        let rCW = simd_mul(flip, rotation.transpose)
        let t = -simd_mul(rCW, position)
        let q = simd_quatd(rCW)
        let s = q.real < 0 ? -1.0 : 1.0          // COLMAP convention: qw >= 0
        return (q.real * s, q.imag.x * s, q.imag.y * s, q.imag.z * s, t)
    }
}

extension simd_double4x4 {
    init(rows: [SIMD4<Double>]) {
        self.init(columns: (SIMD4(rows[0][0], rows[1][0], rows[2][0], rows[3][0]),
                            SIMD4(rows[0][1], rows[1][1], rows[2][1], rows[3][1]),
                            SIMD4(rows[0][2], rows[1][2], rows[2][2], rows[3][2]),
                            SIMD4(rows[0][3], rows[1][3], rows[2][3], rows[3][3])))
    }
}
