import CoreGraphics
import Foundation
import ImageIO
import simd
import Testing
@testable import NammaCaptureCore

/// Timing of a room-sized bake. Runs only with NAMMACAP_BENCH=1 (swift test -c release --filter BakeBenchmark).
@Suite struct BakeBenchmark {
    @Test func roomSizedBake() throws {
        guard ProcessInfo.processInfo.environment["NAMMACAP_BENCH"] != nil else { return }
        // inside of a 4 x 3 x 2.6 m box, ~4 cm grid -> ~190 k triangles, normals facing inwards
        var pos: [Float] = [], idx: [UInt32] = []
        func wall(_ o: SIMD3<Float>, _ a: SIMD3<Float>, _ b: SIMD3<Float>, _ na: Int, _ nb: Int) {
            let base = UInt32(pos.count / 3)
            for j in 0...nb { for i in 0...na {
                let p = o + a * Float(i) / Float(na) + b * Float(j) / Float(nb); pos += [p.x, p.y, p.z] } }
            for j in 0..<nb { for i in 0..<na {
                let v = base + UInt32(j * (na + 1) + i)
                idx += [v, v + 1, v + UInt32(na + 1), v + 1, v + UInt32(na + 2), v + UInt32(na + 1)] } }
        }
        wall(SIMD3(-2, 0, -1.5), SIMD3(4, 0, 0), SIMD3(0, 0, 3), 100, 75)        // floor
        wall(SIMD3(-2, 2.6, -1.5), SIMD3(0, 0, 3), SIMD3(4, 0, 0), 75, 100)      // ceiling
        wall(SIMD3(-2, 0, -1.5), SIMD3(0, 2.6, 0), SIMD3(4, 0, 0), 65, 100)      // walls
        wall(SIMD3(-2, 0, 1.5), SIMD3(4, 0, 0), SIMD3(0, 2.6, 0), 100, 65)
        wall(SIMD3(-2, 0, -1.5), SIMD3(0, 0, 3), SIMD3(0, 2.6, 0), 75, 65)
        wall(SIMD3(2, 0, -1.5), SIMD3(0, 2.6, 0), SIMD3(0, 0, 3), 65, 75)
        let dir = Fixtures.tempDir("bench")
        let w = 4032, h = 3024
        var px = [UInt8](repeating: 0, count: w * h * 4)
        for y in 0..<h { for x in 0..<w { let o = (y * w + x) * 4; px[o] = UInt8(x % 256); px[o + 1] = UInt8(y % 256); px[o + 2] = UInt8((x ^ y) % 256) } }
        let ctx = CGContext(data: &px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                            space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
        let img = dir.appendingPathComponent("p.jpg")
        let dst = CGImageDestinationCreateWithURL(img as CFURL, "public.jpeg" as CFString, 1, nil)!
        CGImageDestinationAddImage(dst, ctx.makeImage()!, nil); CGImageDestinationFinalize(dst)
        let k = Intrinsics(fx: 3000, fy: 3000, cx: 2016, cy: 1512, width: w, height: h)
        var views: [TextureBaker.View] = []
        for i in 0..<40 {                                                       // camera at 1.4 m turning around
            let yaw = Double(i) * 2 * .pi / 40, pitch = (i % 3 == 0 ? -0.6 : (i % 3 == 1 ? 0.0 : 0.5))
            var m = simd_double4x4(simd_quatd(angle: yaw, axis: SIMD3(0, 1, 0)) * simd_quatd(angle: pitch, axis: SIMD3(1, 0, 0)))
            m.columns.3 = SIMD4(0.3 * cos(yaw), 1.4, 0.3 * sin(yaw), 1)
            views.append(.init(base: .init(imageURL: img, pose: Pose(m), intrinsics: k, depth: nil), sharpness: 100))
        }
        let t0 = Date()
        let r = try TextureBaker.bake(positions: pos, indices: idx, views: views)
        let s = Date().timeIntervalSince(t0)
        print(String(format: "BENCH triangles %d views %d textured %d atlases %d texel %.2f mm glb %.1f MB time %.1f s",
                     r.trianglesTotal, views.count, r.trianglesTextured, r.atlases, r.texelSizeM * 1000, Double(r.glb.count) / 1e6, s))
    }
}
