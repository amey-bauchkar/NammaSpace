import CoreGraphics
import Foundation
import ImageIO
import simd
import Testing
@testable import NammaCaptureCore

@Suite struct MeshColorizerTests {
    /// Camera at the origin looking down -Z; a quad 2 m away facing it; the photo is solid red.
    func scene(depth: Float?) throws -> MeshColorizer.Result {
        let dir = Fixtures.tempDir("color")
        let img = dir.appendingPathComponent("red.jpg")
        try Fixtures.jpeg(0, width: 400, height: 300, rgb: (220, 20, 20)).write(to: img)
        let k = Intrinsics(fx: 300, fy: 300, cx: 200, cy: 150, width: 400, height: 300)
        let pos: [Float] = [-0.8, -0.8, -2, 0.8, -0.8, -2, 0.8, 0.8, -2, -0.8, 0.8, -2]
        let idx: [UInt32] = [0, 1, 2, 0, 2, 3]                          // counter-clockwise seen from the camera
        let d = depth.map { (width: 40, height: 30, values: [Float](repeating: $0, count: 1200),
                             intrinsics: k.scaled(toWidth: 40, height: 30), pose: Pose.identity) }
        return MeshColorizer.colorize(positions: pos, indices: idx,
                                      views: [.init(imageURL: img, pose: .identity, intrinsics: k, depth: d)])
    }

    @Test func visibleSurfaceTakesThePhotoColour() throws {
        let r = try scene(depth: 2.0)
        #expect(r.coloredFraction == 1)
        #expect(r.indices.count == 6 * 4)                                // one subdivision
        for i in 0..<(r.colors.count / 4) {
            #expect(r.colors[i * 4] > 180 && r.colors[i * 4 + 1] < 60 && r.colors[i * 4 + 2] < 60)
        }
    }

    @Test func surfaceHiddenBehindMeasuredDepthStaysUncoloured() throws {
        let r = try scene(depth: 1.0)                                    // LiDAR saw something 1 m away: the quad is hidden
        #expect(r.coloredFraction == 0)
        #expect(r.colors[0] == 128)
    }

    @Test func colouredGLBRoundTrips() throws {
        let r = try scene(depth: nil)
        let glb = try GLBWriter.data(positions: r.positions, indices: r.indices, colors: r.colors)
        let back = try GLBWriter.read(glb)
        #expect(back.positions == r.positions && back.indices == r.indices)
        let jl = Int(glb.subdata(in: 12..<16).withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) })
        let json = String(decoding: glb.subdata(in: 20..<(20 + jl)), as: UTF8.self)
        #expect(json.contains("COLOR_0") && json.contains("KHR_materials_unlit"))
    }
}

@Suite struct ShareExportTests {
    @Test func rawImagesAndGLTFExports() async throws {
        let s = try await Fixtures.session(frames: 5, depth: true)
        let env = ProcessInfo.processInfo.environment
        for kind in ShareExports.Kind.allCases {
            let url = try ShareExports.export(kind, sessionDirectory: s.dir)
            let z = try ZipReader(url: url)
            let names = Set(z.entries.map(\.path))
            switch kind {
            case .images:
                #expect(names.filter { $0.hasPrefix("images/") }.count == 5)
            case .gltf:
                #expect(names.filter { $0.hasSuffix(".glb") }.count == 1 && names.contains("mesh_info.json"))
            case .raw:
                for folder in ["cameras", "images", "depth", "confidence", "corrected_cameras", "corrected_images", "corrected_depth"] {
                    #expect(names.filter { $0.hasPrefix("keyframes/\(folder)/") }.count == 5, "\(folder)")
                }
                #expect(!names.contains { $0.hasSuffix(".glb") })           // a .glb would make Namma Space treat it as a mesh
                #expect(names.contains("mesh_info.json") && names.contains("namma_capture.json"))
            }
            let key = ["raw": "NAMMACAP_RAW_OUT", "images": "NAMMACAP_IMAGES_OUT", "gltf": "NAMMACAP_GLTF_OUT"][kind.rawValue]!
            if let out = env[key] {
                try? FileManager.default.removeItem(atPath: out)
                try FileManager.default.copyItem(at: url, to: URL(fileURLWithPath: out))
            }
        }
    }

    @Test func pngsRoundTripThroughImageIO() throws {
        let v: [UInt16] = [0, 1, 1500, 65535]
        let png = try ShareExports.png16(v, width: 2, height: 2)
        let src = CGImageSourceCreateWithData(png as CFData, nil)!
        let cg = CGImageSourceCreateImageAtIndex(src, 0, nil)!
        #expect(cg.bitsPerComponent == 16 && cg.width == 2)
    }
}
