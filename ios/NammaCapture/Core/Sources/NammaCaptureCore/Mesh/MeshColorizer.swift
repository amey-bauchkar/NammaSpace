import CoreGraphics
import Foundation
import ImageIO
import simd

/// Colours the LiDAR scene mesh from the keyframe photos (per-vertex colour, no texture atlas).
///
/// For every photo: each vertex is projected with the photo's ARKit pose and intrinsics; it takes colour from that photo
/// only if it faces the camera and - when the photo has LiDAR depth - its distance agrees with the measured depth at
/// that pixel (so a wall hidden behind a cupboard does not take the cupboard's colour). Contributions are weighted
/// towards close, head-on views (cos^2 / distance^2). Vertices no photo saw stay grey and are counted.
///
/// Limits: colour detail is the mesh's vertex spacing (one midpoint subdivision halves ARKit's ~2-5 cm triangles), so
/// this is a coloured preview / navigation mesh, not a photo texture; the photoreal view comes from the splat.
public enum MeshColorizer {
    public struct View {
        public var imageURL: URL
        public var pose: Pose
        public var intrinsics: Intrinsics
        /// LiDAR depth of the frame (metres, row-major), its own pinhole model and pose; nil = no occlusion test
        public var depth: (width: Int, height: Int, values: [Float], intrinsics: Intrinsics, pose: Pose)?
        public init(imageURL: URL, pose: Pose, intrinsics: Intrinsics,
                    depth: (width: Int, height: Int, values: [Float], intrinsics: Intrinsics, pose: Pose)?) {
            self.imageURL = imageURL; self.pose = pose; self.intrinsics = intrinsics; self.depth = depth
        }
    }

    public struct Result {
        public var positions: [Float]
        public var indices: [UInt32]
        public var colors: [UInt8]
        public var coloredFraction: Double
        public var viewsUsed: Int
    }

    /// One level of midpoint subdivision (each triangle -> 4); shared edges share their new vertex.
    public static func subdivide(positions: [Float], indices: [UInt32]) -> ([Float], [UInt32]) {
        var pos = positions
        var mid: [UInt64: UInt32] = [:]
        mid.reserveCapacity(indices.count)
        func m(_ a: UInt32, _ b: UInt32) -> UInt32 {
            let key = a < b ? (UInt64(a) << 32 | UInt64(b)) : (UInt64(b) << 32 | UInt64(a))
            if let v = mid[key] { return v }
            let v = UInt32(pos.count / 3)
            for k in 0..<3 { pos.append((positions[Int(a) * 3 + k] + positions[Int(b) * 3 + k]) * 0.5) }
            mid[key] = v
            return v
        }
        var out: [UInt32] = []
        out.reserveCapacity(indices.count * 4)
        var t = 0
        while t + 2 < indices.count {
            let a = indices[t], b = indices[t + 1], c = indices[t + 2]
            let ab = m(a, b), bc = m(b, c), ca = m(c, a)
            out += [a, ab, ca, ab, b, bc, ca, bc, c, ab, bc, ca]
            t += 3
        }
        return (pos, out)
    }

    static func f3(_ m: simd_double3x3) -> simd_float3x3 {
        simd_float3x3(columns: (SIMD3<Float>(m.columns.0), SIMD3<Float>(m.columns.1), SIMD3<Float>(m.columns.2)))
    }

    static func normals(_ p: [Float], _ idx: [UInt32]) -> [SIMD3<Float>] {
        var n = [SIMD3<Float>](repeating: .zero, count: p.count / 3)
        func v(_ i: UInt32) -> SIMD3<Float> { let j = Int(i) * 3; return SIMD3(p[j], p[j + 1], p[j + 2]) }
        var t = 0
        while t + 2 < idx.count {
            let a = idx[t], b = idx[t + 1], c = idx[t + 2]
            let fn = simd_cross(v(b) - v(a), v(c) - v(a))        // area-weighted
            n[Int(a)] += fn; n[Int(b)] += fn; n[Int(c)] += fn
            t += 3
        }
        return n.map { simd_length($0) > 0 ? simd_normalize($0) : $0 }
    }

    /// RGBA8 pixels of a photo decoded at most `maxSide` px (ImageIO thumbnail: never the full 12 MP in memory).
    static func decode(_ url: URL, maxSide: Int) -> (w: Int, h: Int, px: [UInt8])? {
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
              let cg = CGImageSourceCreateThumbnailAtIndex(src, 0, [kCGImageSourceCreateThumbnailFromImageAlways: true,
                                                                   kCGImageSourceThumbnailMaxPixelSize: maxSide,
                                                                   kCGImageSourceCreateThumbnailWithTransform: false] as CFDictionary)
        else { return nil }
        let w = cg.width, h = cg.height
        var px = [UInt8](repeating: 0, count: w * h * 4)
        guard let cs = CGColorSpace(name: CGColorSpace.sRGB),
              let ctx = CGContext(data: &px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: cs,
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
        return (w, h, px)
    }

    public static func colorize(positions: [Float], indices: [UInt32], views: [View], subdivisions: Int = 1,
                                maxTrianglesToSubdivide: Int = 600_000, decodeMaxSide: Int = 1008,
                                progress: ((Double) -> Void)? = nil) -> Result {
        var pos = positions, idx = indices
        for _ in 0..<subdivisions where idx.count / 3 <= maxTrianglesToSubdivide {
            (pos, idx) = subdivide(positions: pos, indices: idx)
        }
        let n = pos.count / 3
        let nrm = normals(pos, idx)
        var sum = [SIMD3<Float>](repeating: .zero, count: n)
        var wsum = [Float](repeating: 0, count: n)
        var used = 0
        for (vi, view) in views.enumerated() {
            defer { progress?(Double(vi + 1) / Double(max(1, views.count))) }
            guard let img = decode(view.imageURL, maxSide: decodeMaxSide) else { continue }
            used += 1
            let k = view.intrinsics.scaled(toWidth: img.w, height: img.h)
            let R = view.pose.rotation, C = view.pose.position
            let Rt = f3(R.transpose)
            let Cf = SIMD3<Float>(C)
            // depth camera (usually the same frame)
            var dR = Rt, dC = Cf, dk = k, dw = 0, dh = 0
            var dv: [Float] = []
            if let d = view.depth {
                dR = f3(d.pose.rotation.transpose); dC = SIMD3<Float>(d.pose.position)
                dk = d.intrinsics; dw = d.width; dh = d.height; dv = d.values
            }
            let fx = Float(k.fx), fy = Float(k.fy), cx = Float(k.cx), cy = Float(k.cy)
            let dfx = Float(dk.fx), dfy = Float(dk.fy), dcx = Float(dk.cx), dcy = Float(dk.cy)
            img.px.withUnsafeBufferPointer { px in
                for i in 0..<n {
                    let p = SIMD3<Float>(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2])
                    let pc = Rt * (p - Cf)
                    let z = -pc.z                                   // ARKit camera looks along -Z
                    if z < 0.1 || z > 8 { continue }
                    let u = fx * pc.x / z + cx, v = cy - fy * pc.y / z
                    if u < 0 || v < 0 || u >= Float(img.w - 1) || v >= Float(img.h - 1) { continue }
                    let toCam = simd_normalize(Cf - p)
                    let cosA = simd_dot(nrm[i], toCam)
                    if cosA < 0.15 { continue }
                    if !dv.isEmpty {
                        let q = dR * (p - dC)
                        let zd = -q.z
                        if zd > 0.1 {
                            let du = Int(dfx * q.x / zd + dcx), dvv = Int(dcy - dfy * q.y / zd)
                            if du >= 0, dvv >= 0, du < dw, dvv < dh {
                                let meas = dv[dvv * dw + du]
                                if meas.isFinite && meas > 0 && abs(zd - meas) > max(0.04, 0.03 * meas) { continue }
                            }
                        }
                    }
                    let o = (Int(v) * img.w + Int(u)) * 4
                    let w = cosA * cosA / (z * z)
                    sum[i] += w * SIMD3<Float>(Float(px[o]), Float(px[o + 1]), Float(px[o + 2]))
                    wsum[i] += w
                }
            }
        }
        var colors = [UInt8](repeating: 255, count: n * 4)
        var colored = 0
        for i in 0..<n {
            if wsum[i] > 0 {
                let c = sum[i] / wsum[i]
                colors[i * 4] = UInt8(max(0, min(255, c.x.rounded())))
                colors[i * 4 + 1] = UInt8(max(0, min(255, c.y.rounded())))
                colors[i * 4 + 2] = UInt8(max(0, min(255, c.z.rounded())))
                colored += 1
            } else {
                colors[i * 4] = 128; colors[i * 4 + 1] = 128; colors[i * 4 + 2] = 128
            }
        }
        return Result(positions: pos, indices: idx, colors: colors, coloredFraction: Double(colored) / Double(max(1, n)),
                      viewsUsed: used)
    }
}
