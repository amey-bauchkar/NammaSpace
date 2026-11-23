import CoreGraphics
import Foundation
import ImageIO
import simd

/// Bakes a photo texture for the LiDAR mesh from the full-resolution keyframes (no vertex-colour blur):
///
/// 1. Layout: every triangle is laid flat (its true shape, no stretching) into a cell of a texture atlas, at one texel
///    size for the whole mesh, chosen as the finest that fits `maxAtlases` atlases of `atlasSide` px. Cells have a
///    `padding` border filled with the triangle's edge colour, so mip-mapping does not bleed neighbours in.
/// 2. View choice: per triangle, the keyframe that sees all three corners in frame, passes the LiDAR occlusion test,
///    and scores best on cos^2(view angle) / distance^2 x photo sharpness (the app's measured Laplacian variance).
///    One view per triangle keeps texels inside a triangle consistent.
/// 3. Colour matching: each keyframe gets an RGB gain fitted to a multi-view average colour of the vertices it
///    textures (clamped to 0.7-1.4), reducing seams between triangles textured from differently exposed photos.
/// 4. Rasterise: each texel's 3D point is projected into the chosen photo at full resolution and sampled bilinearly.
///
/// Triangles no keyframe saw stay mid-grey and are counted. Output: un-shared vertices with UVs, one primitive per atlas.
public enum TextureBaker {
    public struct Options: Sendable {
        public var atlasSide = 4096
        public var maxAtlases = 3
        public var padding = 2
        public var jpegQuality = 0.9
        public var minTexelM: Float = 0.0008
        public init() {}
    }

    public struct View {
        public var base: MeshColorizer.View
        /// sharpness of the photo (QualityMetrics.sharpness); <= 0 = unknown
        public var sharpness: Double
        public init(base: MeshColorizer.View, sharpness: Double) { self.base = base; self.sharpness = sharpness }
    }

    public struct Result {
        public var glb: Data
        public var texelSizeM: Double
        public var atlases: Int
        public var trianglesTotal: Int
        public var trianglesTextured: Int
        public var viewsUsed: Int
        public var gains: [SIMD3<Float>]
    }

    public enum Error: Swift.Error, CustomStringConvertible {
        case empty, tooLarge
        public var description: String {
            switch self {
            case .empty: return "the mesh has no triangles"
            case .tooLarge: return "the mesh is too large to texture within the atlas budget"
            }
        }
    }

    struct Layout { var atlas: Int32; var a: SIMD2<Float>; var b: SIMD2<Float>; var c: SIMD2<Float> }

    // MARK: entry

    public static func bake(positions: [Float], indices: [UInt32], views: [View], options o: Options = Options(),
                            progress: ((Double) -> Void)? = nil) throws -> Result {
        let nT = indices.count / 3
        guard nT > 0 else { throw Error.empty }
        func P(_ i: UInt32) -> SIMD3<Float> { let j = Int(i) * 3; return SIMD3(positions[j], positions[j + 1], positions[j + 2]) }

        // --- triangle geometry and flat 2D shapes (metres)
        var tA = [SIMD3<Float>](), tB = [SIMD3<Float>](), tC = [SIMD3<Float>](), tN = [SIMD3<Float>]()
        var flat = [(SIMD2<Float>, SIMD2<Float>, SIMD2<Float>)]()
        tA.reserveCapacity(nT); tB.reserveCapacity(nT); tC.reserveCapacity(nT); tN.reserveCapacity(nT); flat.reserveCapacity(nT)
        for t in 0..<nT {
            let A = P(indices[t * 3]), B = P(indices[t * 3 + 1]), C = P(indices[t * 3 + 2])
            tA.append(A); tB.append(B); tC.append(C)
            let n = simd_cross(B - A, C - A)
            tN.append(simd_length(n) > 1e-12 ? simd_normalize(n) : SIMD3(0, 1, 0))
            let L = simd_length(B - A)
            if L < 1e-6 { flat.append((.zero, .zero, .zero)); continue }
            let x = (B - A) / L
            let cx = simd_dot(C - A, x)
            let cy = simd_length((C - A) - cx * x)
            flat.append((SIMD2(0, 0), SIMD2(L, 0), SIMD2(cx, cy)))
        }

        // spatial order (Morton code of the centroid): each atlas then holds one compact region of the room, so the
        // triangles of a primitive stay connected (importers that check connectivity per primitive - e.g. Namma Space's
        // fragment removal - must not see a random scatter of single triangles)
        var lo = SIMD3<Float>(repeating: .greatestFiniteMagnitude), hi = -lo
        for t in 0..<nT { let c = (tA[t] + tB[t] + tC[t]) / 3; lo = simd_min(lo, c); hi = simd_max(hi, c) }
        let ext = simd_max(hi - lo, SIMD3(repeating: 1e-6))
        func spread(_ v: UInt32) -> UInt32 {
            var x = v & 0x3FF
            x = (x | (x << 16)) & 0x030000FF; x = (x | (x << 8)) & 0x0300F00F
            x = (x | (x << 4)) & 0x030C30C3; x = (x | (x << 2)) & 0x09249249
            return x
        }
        let order: [Int] = (0..<nT).map { t -> (UInt32, Int) in
            let q = ((tA[t] + tB[t] + tC[t]) / 3 - lo) / ext * 1023
            return (spread(UInt32(q.x)) | spread(UInt32(q.y)) << 1 | spread(UInt32(q.z)) << 2, t)
        }.sorted { $0.0 < $1.0 }.map(\.1)

        // --- 1. pack at the finest texel size that fits
        var s = max(o.minTexelM, sqrt(flat.reduce(Float(0)) { acc, f in
            let w = max(f.1.x, f.2.x) - min(0, f.2.x), h = f.2.y
            return acc + w * h
        } * 1.6 / Float(o.maxAtlases * o.atlasSide * o.atlasSide)))
        var layout: [Layout] = []
        var usedHeight: [Int] = []
        for _ in 0..<40 {
            if let r = pack(flat, order: order, texel: s, side: o.atlasSide, pad: o.padding, maxAtlases: o.maxAtlases) {
                layout = r.0; usedHeight = r.1; break
            }
            s *= 1.08
        }
        guard !layout.isEmpty else { throw Error.tooLarge }
        progress?(0.05)

        // --- 2. best view per triangle
        let sharp = views.map(\.sharpness).filter { $0 > 0 }.sorted()
        let medSharp = sharp.isEmpty ? 1 : sharp[sharp.count / 2]
        // keep the K best candidate photos per triangle, then smooth the choice so neighbours share a photo
        let K = 4
        var candV = [Int32](repeating: -1, count: nT * K)
        var candS = [Float](repeating: 0, count: nT * K)
        for (vi, v) in views.enumerated() {
            let cam = Cam(v.base, width: v.base.intrinsics.width, height: v.base.intrinsics.height)
            let sw = Float(v.sharpness > 0 ? min(2, max(0.3, (v.sharpness / medSharp).squareRoot())) : 1)
            for t in 0..<nT {
                let cen = (tA[t] + tB[t] + tC[t]) / 3
                guard let (z, _, _) = cam.project(cen), cam.inFrame(tA[t], margin: 2), cam.inFrame(tB[t], margin: 2),
                      cam.inFrame(tC[t], margin: 2) else { continue }
                let cosA = abs(simd_dot(tN[t], simd_normalize(cam.C - cen)))
                if cosA < 0.2 { continue }
                if !cam.visible(cen) { continue }
                let score = cosA * cosA / (z * z) * sw
                // insert into this triangle's top-K (sorted, best first)
                let b = t * K
                if score <= candS[b + K - 1] { continue }
                var j = K - 1
                while j > 0 && candS[b + j - 1] < score { candS[b + j] = candS[b + j - 1]; candV[b + j] = candV[b + j - 1]; j -= 1 }
                candS[b + j] = score; candV[b + j] = Int32(vi)
            }
            progress?(0.05 + 0.22 * Double(vi + 1) / Double(max(1, views.count)))
        }
        let best = smoothLabels(candV: candV, candS: candS, K: K, adjacency: adjacency(positions: positions, indices: indices))
        progress?(0.30)

        // --- 3. colour gains against a multi-view vertex average (small decodes)
        let ref = MeshColorizer.colorize(positions: positions, indices: indices, views: views.map(\.base), subdivisions: 0)
        var byView = [[Int]](repeating: [], count: views.count)
        for t in 0..<nT where best[t] >= 0 { byView[Int(best[t])].append(t) }
        var gains = [SIMD3<Float>](repeating: SIMD3(1, 1, 1), count: views.count)
        for (vi, tris) in byView.enumerated() where !tris.isEmpty {
            guard let small = MeshColorizer.decode(views[vi].base.imageURL, maxSide: 1008) else { continue }
            let cam = Cam(views[vi].base, width: small.w, height: small.h)
            var sr = SIMD3<Float>(repeating: 0), ss = SIMD3<Float>(repeating: 0)
            for t in tris {
                for k in 0..<3 {
                    let vid = Int(indices[t * 3 + k])
                    guard ref.colors.count > vid * 4 else { continue }
                    let r = SIMD3<Float>(Float(ref.colors[vid * 4]), Float(ref.colors[vid * 4 + 1]), Float(ref.colors[vid * 4 + 2]))
                    if r == SIMD3(128, 128, 128) { continue }                    // vertex nobody saw
                    guard let (_, u, vv) = cam.project(P(indices[t * 3 + k])) else { continue }
                    sr += r; ss += sample(small.px, small.w, small.h, stride: 4, u, vv)
                }
            }
            if ss.x > 0 && ss.y > 0 && ss.z > 0 {
                gains[vi] = simd_clamp(sr / ss, SIMD3(repeating: 0.7), SIMD3(repeating: 1.4))
            }
            progress?(0.30 + 0.10 * Double(vi + 1) / Double(views.count))
        }

        // --- 4. rasterise into the atlases at full photo resolution
        var atlases = usedHeight.map { [UInt8](repeating: 128, count: o.atlasSide * $0 * 3) }
        var textured = 0, used = 0
        for (vi, tris) in byView.enumerated() where !tris.isEmpty {
            guard let img = decodeFull(views[vi].base.imageURL) else { continue }
            used += 1
            let cam = Cam(views[vi].base, width: img.w, height: img.h)
            let g = gains[vi]
            img.px.withUnsafeBufferPointer { px in
                for t in tris {
                    let l = layout[t]
                    let k = Int(l.atlas), H = usedHeight[k]
                    fill(&atlases[k], side: o.atlasSide, height: H, l: l, pad: o.padding) { bary in
                        let p = bary.x * tA[t] + bary.y * tB[t] + bary.z * tC[t]
                        guard let (_, u, v) = cam.project(p) else { return nil }
                        return simd_clamp(sample(px, img.w, img.h, stride: 4, u, v) * g, SIMD3(repeating: 0), SIMD3(repeating: 255))
                    }
                    textured += 1
                }
            }
            progress?(0.40 + 0.50 * Double(vi + 1) / Double(views.count))
        }

        // --- 5. encode
        var jpegs: [Data] = []
        for (k, a) in atlases.enumerated() { jpegs.append(try jpeg(a, width: o.atlasSide, height: usedHeight[k], quality: o.jpegQuality)) }
        var pos = [Float](), uv = [Float]()
        pos.reserveCapacity(nT * 9); uv.reserveCapacity(nT * 6)
        var prims = [[UInt32]](repeating: [], count: atlases.count)
        for t in 0..<nT {
            let l = layout[t], k = Int(l.atlas)
            for (p, q) in [(tA[t], l.a), (tB[t], l.b), (tC[t], l.c)] {
                pos += [p.x, p.y, p.z]
                uv += [q.x / Float(o.atlasSide), q.y / Float(usedHeight[k])]
            }
            prims[k] += [UInt32(t * 3), UInt32(t * 3 + 1), UInt32(t * 3 + 2)]
        }
        let glb = try GLBWriter.texturedData(positions: pos, uvs: uv, primitives: prims, atlases: jpegs)
        progress?(1)
        return Result(glb: glb, texelSizeM: Double(s), atlases: atlases.count, trianglesTotal: nT,
                      trianglesTextured: textured, viewsUsed: used, gains: gains)
    }

    // MARK: view-label smoothing (fewer texture seams)

    /// Triangles sharing an edge (vertices welded at 1 mm, so seams in the input do not break adjacency).
    static func adjacency(positions: [Float], indices: [UInt32]) -> [[Int32]] {
        let nT = indices.count / 3
        var weld: [SIMD3<Int32>: Int32] = [:]
        var wid = [Int32](repeating: 0, count: positions.count / 3)
        for v in 0..<(positions.count / 3) {
            let k = SIMD3<Int32>(Int32((positions[v * 3] * 1000).rounded()), Int32((positions[v * 3 + 1] * 1000).rounded()),
                                 Int32((positions[v * 3 + 2] * 1000).rounded()))
            if let w = weld[k] { wid[v] = w } else { let w = Int32(weld.count); weld[k] = w; wid[v] = w }
        }
        var edges: [UInt64: Int32] = [:]
        edges.reserveCapacity(nT * 3)
        var adj = [[Int32]](repeating: [], count: nT)
        for t in 0..<nT {
            for e in 0..<3 {
                let a = UInt64(UInt32(bitPattern: wid[Int(indices[t * 3 + e])])), b = UInt64(UInt32(bitPattern: wid[Int(indices[t * 3 + (e + 1) % 3])]))
                let key = a < b ? a << 32 | b : b << 32 | a
                if let o = edges[key] { adj[t].append(o); adj[Int(o)].append(Int32(t)) } else { edges[key] = Int32(t) }
            }
        }
        return adj
    }

    /// Iterated conditional modes over the candidate photos: data cost = 1 - score / best score of the triangle,
    /// plus `lambda` for every neighbour textured from a different photo. Returns the chosen photo per triangle (-1 = none).
    static func smoothLabels(candV: [Int32], candS: [Float], K: Int, adjacency adj: [[Int32]], lambda: Float = 0.35,
                             iterations: Int = 8) -> [Int32] {
        let nT = adj.count
        var label = (0..<nT).map { candV[$0 * K] }
        for _ in 0..<iterations {
            var changed = 0
            for t in 0..<nT {
                let b = t * K, top = candS[b]
                if top <= 0 { continue }
                var bestCost = Float.greatestFiniteMagnitude, bestV = label[t]
                for k in 0..<K {
                    let v = candV[b + k]
                    if v < 0 { break }
                    var cost = 1 - candS[b + k] / top
                    for n in adj[t] where label[Int(n)] >= 0 && label[Int(n)] != v { cost += lambda }
                    if cost < bestCost { bestCost = cost; bestV = v }
                }
                if bestV != label[t] { label[t] = bestV; changed += 1 }
            }
            if changed == 0 { break }
        }
        return label
    }

    // MARK: packing: spatial chunks, shelves (tallest first) inside each atlas

    static func pack(_ flat: [(SIMD2<Float>, SIMD2<Float>, SIMD2<Float>)], order: [Int], texel s: Float, side: Int, pad: Int,
                     maxAtlases: Int) -> ([Layout], [Int])? {
        let n = flat.count
        var cellW = [Int](repeating: 0, count: n), cellH = [Int](repeating: 0, count: n), minX = [Float](repeating: 0, count: n)
        for i in 0..<n {
            let f = flat[i]
            let x0 = min(0, f.2.x), x1 = max(f.1.x, f.2.x)
            minX[i] = x0
            cellW[i] = Int(((x1 - x0) / s).rounded(.up)) + 2 * pad + 1
            cellH[i] = Int((f.2.y / s).rounded(.up)) + 2 * pad + 1
            if cellW[i] > side || cellH[i] > side { return nil }
        }
        var out = [Layout](repeating: Layout(atlas: 0, a: .zero, b: .zero, c: .zero), count: n)
        var heights: [Int] = []
        var start = 0
        while start < n {
            let atlas = heights.count
            if atlas >= maxAtlases { return nil }
            // the next run of triangles in spatial order that should fit; shrink until it really does
            var fill = 0.80
            var placed: (end: Int, height: Int)? = nil
            while placed == nil {
                var end = start, acc = 0.0
                let cap = fill * Double(side * side)
                while end < n && (end == start || acc + Double(cellW[order[end]] * cellH[order[end]]) <= cap) {
                    acc += Double(cellW[order[end]] * cellH[order[end]]); end += 1
                }
                let chunk = order[start..<end].sorted { cellH[$0] > cellH[$1] }
                var x = 0, y = 0, shelf = 0, ok = true, top = 0
                var tmp: [(Int, Layout)] = []
                tmp.reserveCapacity(chunk.count)
                for i in chunk {
                    if x + cellW[i] > side { x = 0; y += shelf; shelf = 0 }
                    if y + cellH[i] > side { ok = false; break }
                    let o = SIMD2<Float>(Float(x + pad), Float(y + pad))
                    let f = flat[i]
                    func m(_ q: SIMD2<Float>) -> SIMD2<Float> { o + SIMD2((q.x - minX[i]) / s, q.y / s) + SIMD2(0.5, 0.5) }
                    tmp.append((i, Layout(atlas: Int32(atlas), a: m(f.0), b: m(f.1), c: m(f.2))))
                    x += cellW[i]; shelf = max(shelf, cellH[i]); top = max(top, y + shelf)
                }
                if ok {
                    for (i, l) in tmp { out[i] = l }
                    placed = (end, top)
                } else if end - start <= 1 {
                    return nil
                } else {
                    fill *= 0.9
                }
            }
            heights.append(max(4, (placed!.height + 3) / 4 * 4))
            start = placed!.end
        }
        return (out, heights)
    }

    // MARK: rasterising one triangle (texels inside, plus a padding ring with clamped barycentrics)

    static func fill(_ atlas: inout [UInt8], side: Int, height: Int, l: Layout, pad: Int,
                     color: (SIMD3<Float>) -> SIMD3<Float>?) {
        let a = l.a, b = l.b, c = l.c
        let d = (b.y - c.y) * (a.x - c.x) + (c.x - b.x) * (a.y - c.y)
        if abs(d) < 1e-9 { return }
        let x0 = max(0, Int(min(a.x, b.x, c.x)) - pad), x1 = min(side - 1, Int(max(a.x, b.x, c.x)) + pad)
        let y0 = max(0, Int(min(a.y, b.y, c.y)) - pad), y1 = min(height - 1, Int(max(a.y, b.y, c.y)) + pad)
        if x1 < x0 || y1 < y0 { return }
        for y in y0...y1 {
            for x in x0...x1 {
                let px = Float(x) + 0.5, py = Float(y) + 0.5
                var w1 = ((b.y - c.y) * (px - c.x) + (c.x - b.x) * (py - c.y)) / d
                var w2 = ((c.y - a.y) * (px - c.x) + (a.x - c.x) * (py - c.y)) / d
                var w3 = 1 - w1 - w2
                if w1 < 0 || w2 < 0 || w3 < 0 {                         // padding: nearest point on the triangle
                    w1 = max(0, w1); w2 = max(0, w2); w3 = max(0, w3)
                    let sum = w1 + w2 + w3
                    if sum <= 0 { continue }
                    w1 /= sum; w2 /= sum; w3 /= sum
                }
                guard let col = color(SIMD3(w1, w2, w3)) else { continue }
                let o = (y * side + x) * 3
                atlas[o] = UInt8(col.x); atlas[o + 1] = UInt8(col.y); atlas[o + 2] = UInt8(col.z)
            }
        }
    }

    // MARK: camera model (ARKit: camera looks along -Z, image v grows downward)

    struct Cam {
        let Rt: simd_float3x3, C: SIMD3<Float>, fx: Float, fy: Float, cx: Float, cy: Float, w: Int, h: Int
        let dR: simd_float3x3, dC: SIMD3<Float>, dfx: Float, dfy: Float, dcx: Float, dcy: Float, dw: Int, dh: Int, dv: [Float]

        init(_ v: MeshColorizer.View, width: Int, height: Int) {
            let k = v.intrinsics.scaled(toWidth: width, height: height)
            Rt = MeshColorizer.f3(v.pose.rotation.transpose); C = SIMD3<Float>(v.pose.position)
            fx = Float(k.fx); fy = Float(k.fy); cx = Float(k.cx); cy = Float(k.cy); w = width; h = height
            if let d = v.depth {
                dR = MeshColorizer.f3(d.pose.rotation.transpose); dC = SIMD3<Float>(d.pose.position)
                dfx = Float(d.intrinsics.fx); dfy = Float(d.intrinsics.fy); dcx = Float(d.intrinsics.cx); dcy = Float(d.intrinsics.cy)
                dw = d.width; dh = d.height; dv = d.values
            } else {
                dR = Rt; dC = C; dfx = 0; dfy = 0; dcx = 0; dcy = 0; dw = 0; dh = 0; dv = []
            }
        }

        /// -> (depth, u, v) in pixels, or nil behind the camera / out of range.
        @inline(__always) func project(_ p: SIMD3<Float>) -> (Float, Float, Float)? {
            let q = Rt * (p - C)
            let z = -q.z
            if z < 0.1 || z > 8 { return nil }
            return (z, fx * q.x / z + cx, cy - fy * q.y / z)
        }

        func inFrame(_ p: SIMD3<Float>, margin: Float) -> Bool {
            guard let (_, u, v) = project(p) else { return false }
            return u >= margin && v >= margin && u < Float(w) - margin && v < Float(h) - margin
        }

        /// LiDAR occlusion test (true when there is no usable depth at that pixel).
        func visible(_ p: SIMD3<Float>) -> Bool {
            if dv.isEmpty { return true }
            let q = dR * (p - dC)
            let z = -q.z
            if z < 0.1 { return false }
            let u = Int(dfx * q.x / z + dcx), v = Int(dcy - dfy * q.y / z)
            guard u >= 0, v >= 0, u < dw, v < dh else { return true }
            let m = dv[v * dw + u]
            return !(m.isFinite && m > 0) || abs(z - m) <= max(0.05, 0.03 * m)
        }
    }

    // MARK: images

    @inline(__always) static func sample(_ px: UnsafeBufferPointer<UInt8>, _ w: Int, _ h: Int, stride: Int,
                                         _ u: Float, _ v: Float) -> SIMD3<Float> {
        let x = min(max(u - 0.5, 0), Float(w - 1)), y = min(max(v - 0.5, 0), Float(h - 1))
        let x0 = Int(x), y0 = Int(y), x1 = min(x0 + 1, w - 1), y1 = min(y0 + 1, h - 1)
        let fx = x - Float(x0), fy = y - Float(y0)
        func c(_ xx: Int, _ yy: Int) -> SIMD3<Float> {
            let o = (yy * w + xx) * stride
            return SIMD3(Float(px[o]), Float(px[o + 1]), Float(px[o + 2]))
        }
        return (c(x0, y0) * (1 - fx) + c(x1, y0) * fx) * (1 - fy) + (c(x0, y1) * (1 - fx) + c(x1, y1) * fx) * fy
    }

    static func sample(_ px: [UInt8], _ w: Int, _ h: Int, stride: Int, _ u: Float, _ v: Float) -> SIMD3<Float> {
        px.withUnsafeBufferPointer { sample($0, w, h, stride: stride, u, v) }
    }

    /// Full-resolution RGBA8 decode of one photo.
    static func decodeFull(_ url: URL) -> (w: Int, h: Int, px: [UInt8])? {
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
              let cg = CGImageSourceCreateImageAtIndex(src, 0, [kCGImageSourceShouldCache: false] as CFDictionary) else { return nil }
        let w = cg.width, h = cg.height
        var px = [UInt8](repeating: 0, count: w * h * 4)
        guard let cs = CGColorSpace(name: CGColorSpace.sRGB),
              let ctx = CGContext(data: &px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: cs,
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
        return (w, h, px)
    }

    static func jpeg(_ rgb: [UInt8], width: Int, height: Int, quality: Double) throws -> Data {
        guard let prov = CGDataProvider(data: Data(rgb) as CFData), let cs = CGColorSpace(name: CGColorSpace.sRGB),
              let cg = CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 24, bytesPerRow: width * 3,
                               space: cs, bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
                               provider: prov, decode: nil, shouldInterpolate: false, intent: .defaultIntent) else {
            throw GLBWriter.Error.invalid("atlas image")
        }
        let out = NSMutableData()
        guard let dst = CGImageDestinationCreateWithData(out, "public.jpeg" as CFString, 1, nil) else { throw GLBWriter.Error.invalid("JPEG encoder") }
        CGImageDestinationAddImage(dst, cg, [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
        guard CGImageDestinationFinalize(dst) else { throw GLBWriter.Error.invalid("JPEG encoding failed") }
        return out as Data
    }
}
