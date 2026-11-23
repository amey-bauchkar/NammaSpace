import CoreGraphics
import Foundation
import ImageIO
import simd
import Testing
@testable import NammaCaptureCore

@Suite struct TextureBakerTests {
    /// 400x300 photo: left half `left`, right half `right`.
    static func photo(_ dir: URL, _ name: String, left: (UInt8, UInt8, UInt8), right: (UInt8, UInt8, UInt8)) throws -> URL {
        let w = 400, h = 300
        var px = [UInt8](repeating: 255, count: w * h * 4)
        for y in 0..<h { for x in 0..<w {
            let c = x < w / 2 ? left : right
            let o = (y * w + x) * 4; px[o] = c.0; px[o + 1] = c.1; px[o + 2] = c.2
        } }
        let ctx = CGContext(data: &px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                            space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
        let u = dir.appendingPathComponent(name)
        let dst = CGImageDestinationCreateWithURL(u as CFURL, "public.jpeg" as CFString, 1, nil)!
        CGImageDestinationAddImage(dst, ctx.makeImage()!, [kCGImageDestinationLossyCompressionQuality: 1.0] as CFDictionary)
        CGImageDestinationFinalize(dst)
        return u
    }

    static let k = Intrinsics(fx: 300, fy: 300, cx: 200, cy: 150, width: 400, height: 300)
    static let quad: [Float] = [-0.8, -0.8, -2, 0.8, -0.8, -2, 0.8, 0.8, -2, -0.8, 0.8, -2]
    static let tris: [UInt32] = [0, 1, 2, 0, 2, 3]

    static func view(_ u: URL, depth: Float?, sharp: Double = 100) -> TextureBaker.View {
        let d = depth.map { (width: 40, height: 30, values: [Float](repeating: $0, count: 1200), intrinsics: k.scaled(toWidth: 40, height: 30), pose: Pose.identity) }
        return .init(base: .init(imageURL: u, pose: .identity, intrinsics: k, depth: d), sharpness: sharp)
    }

    /// Minimal reader for the baked GLB: positions, uvs, first atlas as RGBA.
    static func parse(_ glb: Data) throws -> (pos: [Float], uv: [Float], atlas: (w: Int, h: Int, px: [UInt8])) {
        let jl = Int(glb.subdata(in: 12..<16).withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) })
        let doc = try JSONSerialization.jsonObject(with: glb.subdata(in: 20..<(20 + jl))) as! [String: Any]
        let bin = glb.subdata(in: (20 + jl + 8)..<glb.count)
        let views = doc["bufferViews"] as! [[String: Any]], acc = doc["accessors"] as! [[String: Any]]
        func floats(_ a: Int) -> [Float] {
            let v = views[acc[a]["bufferView"] as! Int]; let o = v["byteOffset"] as! Int, n = v["byteLength"] as! Int / 4
            return (0..<n).map { i in bin.subdata(in: (o + i * 4)..<(o + i * 4 + 4)).withUnsafeBytes { $0.loadUnaligned(as: Float.self) } }
        }
        let img = (doc["images"] as! [[String: Any]])[0]
        let bv = views[img["bufferView"] as! Int]
        let jpg = bin.subdata(in: (bv["byteOffset"] as! Int)..<((bv["byteOffset"] as! Int) + (bv["byteLength"] as! Int)))
        let cg = CGImageSourceCreateImageAtIndex(CGImageSourceCreateWithData(jpg as CFData, nil)!, 0, nil)!
        var px = [UInt8](repeating: 0, count: cg.width * cg.height * 4)
        let ctx = CGContext(data: &px, width: cg.width, height: cg.height, bitsPerComponent: 8, bytesPerRow: cg.width * 4,
                            space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: cg.width, height: cg.height))
        return (floats(0), floats(1), (cg.width, cg.height, px))
    }

    /// Colour of the texture near each corner of each triangle (80 % corner, 20 % centroid), with the corner's x.
    static func cornerColours(_ r: TextureBaker.Result) throws -> [(x: Float, rgb: SIMD3<Float>)] {
        let g = try parse(r.glb)
        var out: [(Float, SIMD3<Float>)] = []
        for t in 0..<(g.pos.count / 9) {
            let cu = (0..<3).reduce(SIMD2<Float>.zero) { $0 + SIMD2(g.uv[(t * 3 + $1) * 2], g.uv[(t * 3 + $1) * 2 + 1]) } / 3
            for k in 0..<3 {
                let i = t * 3 + k
                let uv = 0.8 * SIMD2(g.uv[i * 2], g.uv[i * 2 + 1]) + 0.2 * cu
                let x = min(g.atlas.w - 1, Int(uv.x * Float(g.atlas.w))), y = min(g.atlas.h - 1, Int(uv.y * Float(g.atlas.h)))
                let o = (y * g.atlas.w + x) * 4
                out.append((g.pos[i * 3], SIMD3(Float(g.atlas.px[o]), Float(g.atlas.px[o + 1]), Float(g.atlas.px[o + 2]))))
            }
        }
        return out
    }

    @Test func bakedTextureMatchesThePhoto() throws {
        let dir = Fixtures.tempDir("bake")
        let u = try Self.photo(dir, "a.jpg", left: (230, 20, 20), right: (20, 20, 230))
        let r = try TextureBaker.bake(positions: Self.quad, indices: Self.tris, views: [Self.view(u, depth: 2.0)])
        #expect(r.trianglesTextured == 2 && r.viewsUsed == 1 && r.atlases == 1)
        #expect(r.texelSizeM > 0.00079 && r.texelSizeM < 0.01)
        for c in try Self.cornerColours(r) {
            if c.x < 0 { #expect(c.rgb.x > 150 && c.rgb.z < 100, "left corner should be red: \(c.rgb)") }
            else { #expect(c.rgb.z > 150 && c.rgb.x < 100, "right corner should be blue: \(c.rgb)") }
        }
    }

    @Test func occludedSurfaceIsNotTextured() throws {
        let dir = Fixtures.tempDir("bake")
        let u = try Self.photo(dir, "a.jpg", left: (230, 20, 20), right: (230, 20, 20))
        let r = try TextureBaker.bake(positions: Self.quad, indices: Self.tris, views: [Self.view(u, depth: 1.0)])
        #expect(r.trianglesTextured == 0)
        for c in try Self.cornerColours(r) { #expect(abs(c.rgb.x - 128) < 12) }
    }

    @Test func sharperPhotoWins() throws {
        let dir = Fixtures.tempDir("bake")
        let blurry = try Self.photo(dir, "b.jpg", left: (230, 20, 20), right: (230, 20, 20))
        let sharp = try Self.photo(dir, "s.jpg", left: (20, 200, 20), right: (20, 200, 20))
        let r = try TextureBaker.bake(positions: Self.quad, indices: Self.tris,
                                      views: [Self.view(blurry, depth: nil, sharp: 20), Self.view(sharp, depth: nil, sharp: 900)])
        // the colour gain pulls towards the multi-view average (both photos show the same surface in reality), so check
        // which photo dominates rather than absolute values
        for c in try Self.cornerColours(r) { #expect(c.rgb.y > c.rgb.x + 40, "\(c.rgb)") }
    }

    @Test func smoothingRemovesNeedlessPhotoSwitches() {
        // a strip of 20 triangles: photos 0 and 1 alternate as "best" by a hair; one triangle strongly prefers photo 2
        let n = 20, K = 4
        var candV = [Int32](repeating: -1, count: n * K), candS = [Float](repeating: 0, count: n * K)
        for t in 0..<n {
            let a: Int32 = t % 2 == 0 ? 0 : 1, b: Int32 = 1 - a
            candV[t * K] = a; candS[t * K] = 1.0; candV[t * K + 1] = b; candS[t * K + 1] = 0.97
            if t == 10 { candV[t * K] = 2; candS[t * K] = 1.0; candV[t * K + 1] = 0; candS[t * K + 1] = 0.2; candV[t * K + 2] = -1 }
        }
        let adj: [[Int32]] = (0..<n).map { t in [t - 1, t + 1].filter { $0 >= 0 && $0 < n }.map(Int32.init) }
        let before = (1..<n).filter { t in candV[t * K] != candV[(t - 1) * K] }.count
        let l = TextureBaker.smoothLabels(candV: candV, candS: candS, K: K, adjacency: adj)
        let after = (1..<n).filter { l[$0] != l[$0 - 1] }.count
        #expect(before == 19)
        #expect(after <= 3, "\(l)")
        #expect(l[10] == 2)                                     // a clearly better photo is not overridden
    }
}
