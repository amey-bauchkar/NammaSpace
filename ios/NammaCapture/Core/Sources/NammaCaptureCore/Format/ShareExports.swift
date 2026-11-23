import CoreGraphics
import Foundation
import ImageIO

/// The three Polycam-style exports, built from a session folder (streamed into ZIPs; at most one photo in memory):
///
/// 1. Raw data (.zip) - Polycam's raw "Images" layout, which Namma Space's LiDAR route reads
///    (src/namma/capture/polycam_raw.py):
///      keyframes/cameras/<id>.json            pose t_00..t_23 (row-major 3x4 camera->world, ARKit axes), fx fy cx cy
///                                             width height of images/<id>.jpg, iso / exposure_time when reported,
///                                             tracking_segment, exif.LensModel, plus "namma" (our per-frame record)
///      keyframes/images/<id>.jpg              full-resolution photo as stored by the app
///      keyframes/depth/<id>.Clean.png         LiDAR depth, 16-bit, millimetres, 0 = no measurement (native size)
///      keyframes/confidence/<id>.png          ARKit confidence as 0 / 54 / 255 (low / medium / high)
///      keyframes/corrected_cameras/<id>.json  same pose; intrinsics for corrected_images/
///      keyframes/corrected_images/<id>.jpg    the photo at 2016 px (ARKit images are already undistorted, so
///                                             "corrected" here is only smaller; nothing was re-posed or sharpened)
///      keyframes/corrected_depth/<id>.Clean.png  depth at 512x384 (nearest-neighbour from the native grid)
///      mesh_info.json                         alignmentTransform = identity (mesh and cameras share ARKit's world)
///      namma_capture.json                     provenance and the notes above
///    <id> = the frame's ARKit timestamp in microseconds. Frames without depth (non-LiDAR) have cameras + images only.
///    No .glb inside (Namma Space would take a zip with one .glb as the mesh).
/// 2. Images (.zip) - images/<frame>.jpg, the full-resolution photos.
/// 3. glTF (.zip) - <name>.glb (LiDAR mesh with photo vertex colours) + mesh_info.json.
public enum ShareExports {
    public enum Kind: String, CaseIterable, Sendable { case raw, images, gltf }

    public enum Error: Swift.Error, CustomStringConvertible {
        case noFrames, noMesh, io(String)
        public var description: String {
            switch self {
            case .noFrames: return "This scan has no photos to export."
            case .noMesh: return "This scan has no LiDAR mesh (it needs a LiDAR iPhone and a finished scan)."
            case .io(let s): return s
            }
        }
    }

    static let coloredMeshPath = "mesh/arkit_scene_mesh_colored.glb"
    static let texturedMeshPath = "mesh/arkit_scene_mesh_textured.glb"
    static let correctedLongSide = 2016

    // MARK: inputs

    static func frames(_ dir: URL) throws -> [FrameRecord] {
        let r = try JSONLines.read(FrameRecord.self, from: dir.appendingPathComponent(NammaCapFormat.framesPath))
        let fm = FileManager.default
        return r.items.filter { fm.fileExists(atPath: dir.appendingPathComponent($0.image.path).path) }
            .sorted { $0.index < $1.index }
    }

    static func depthValues(_ dir: URL, _ d: DepthRef) -> [Float]? {
        guard let data = try? Data(contentsOf: dir.appendingPathComponent(d.path)), data.count == d.width * d.height * 4 else { return nil }
        return data.withUnsafeBytes { raw in (0..<(d.width * d.height)).map { Float(bitPattern: UInt32(littleEndian: raw.loadUnaligned(fromByteOffset: $0 * 4, as: UInt32.self))) } }
    }

    // MARK: coloured mesh

    /// The coloured LiDAR mesh (built once per scan, cached next to the plain mesh). Returns nil without a mesh.
    @discardableResult
    public static func coloredMesh(sessionDirectory dir: URL, progress: ((Double) -> Void)? = nil) throws -> (url: URL, coloredFraction: Double)? {
        let plain = dir.appendingPathComponent(NammaCapFormat.meshPath)
        let out = dir.appendingPathComponent(coloredMeshPath)
        guard FileManager.default.fileExists(atPath: plain.path) else { return nil }
        let infoURL = out.appendingPathExtension("json")
        if FileManager.default.fileExists(atPath: out.path),
           let info = try? JSONSerialization.jsonObject(with: Data(contentsOf: infoURL)) as? [String: Any],
           let f = info["colored_fraction"] as? Double { return (out, f) }
        let (p, i) = try GLBWriter.read(Data(contentsOf: plain))
        let views = try frames(dir).map { f -> MeshColorizer.View in
            var depth: (width: Int, height: Int, values: [Float], intrinsics: Intrinsics, pose: Pose)? = nil
            if let d = f.depth, let v = depthValues(dir, d), let dp = Pose(rowMajor: d.tWorldFromCamera) {
                depth = (d.width, d.height, v, d.intrinsics, dp)
            }
            return MeshColorizer.View(imageURL: dir.appendingPathComponent(f.image.path),
                                      pose: Pose(rowMajor: f.camera.tWorldFromCamera) ?? .identity,
                                      intrinsics: f.camera.intrinsics, depth: depth)
        }
        let r = MeshColorizer.colorize(positions: p, indices: i, views: views, progress: progress)
        let glb = try GLBWriter.data(positions: r.positions, indices: r.indices, colors: r.colors)
        try SessionStore.writeAtomically(glb, to: out)
        try SessionStore.writeAtomically(try JSONSerialization.data(withJSONObject: [
            "colored_fraction": r.coloredFraction, "views_used": r.viewsUsed, "vertices": r.positions.count / 3,
            "triangles": r.indices.count / 3, "method": "per-vertex colour from keyframes, LiDAR-depth occlusion test, one midpoint subdivision",
        ], options: [.sortedKeys]), to: infoURL)
        return (out, r.coloredFraction)
    }

    /// The photo-textured LiDAR mesh (TextureBaker), built once per scan and cached. nil without a mesh.
    @discardableResult
    public static func texturedMesh(sessionDirectory dir: URL, options: TextureBaker.Options = TextureBaker.Options(),
                                    progress: ((Double) -> Void)? = nil) throws -> (url: URL, info: [String: Any])? {
        let plain = dir.appendingPathComponent(NammaCapFormat.meshPath)
        let out = dir.appendingPathComponent(texturedMeshPath)
        let infoURL = out.appendingPathExtension("json")
        guard FileManager.default.fileExists(atPath: plain.path) else { return nil }
        if FileManager.default.fileExists(atPath: out.path),
           let info = try? JSONSerialization.jsonObject(with: Data(contentsOf: infoURL)) as? [String: Any] { return (out, info) }
        let (p, i) = try GLBWriter.read(Data(contentsOf: plain))
        let views = try frames(dir).map { f -> TextureBaker.View in
            var depth: (width: Int, height: Int, values: [Float], intrinsics: Intrinsics, pose: Pose)? = nil
            if let d = f.depth, let v = depthValues(dir, d), let dp = Pose(rowMajor: d.tWorldFromCamera) {
                depth = (d.width, d.height, v, d.intrinsics, dp)
            }
            return TextureBaker.View(base: MeshColorizer.View(imageURL: dir.appendingPathComponent(f.image.path),
                                                              pose: Pose(rowMajor: f.camera.tWorldFromCamera) ?? .identity,
                                                              intrinsics: f.camera.intrinsics, depth: depth),
                                     sharpness: f.quality.metrics.sharpness)
        }
        let r = try TextureBaker.bake(positions: p, indices: i, views: views, options: options, progress: progress)
        try SessionStore.writeAtomically(r.glb, to: out)
        let info: [String: Any] = ["texel_size_m": r.texelSizeM, "atlases": r.atlases, "atlas_side": options.atlasSide,
                                   "triangles": r.trianglesTotal, "triangles_textured": r.trianglesTextured,
                                   "views_used": r.viewsUsed, "bytes": r.glb.count,
                                   "method": "photo texture atlas from full-resolution keyframes; best view per triangle (angle, distance, sharpness, LiDAR occlusion); per-photo colour gains"]
        try SessionStore.writeAtomically(try JSONSerialization.data(withJSONObject: info, options: [.sortedKeys]), to: infoURL)
        return (out, info)
    }

    // MARK: exports

    public static func export(_ kind: Kind, sessionDirectory dir: URL, outputDirectory: URL? = nil,
                              progress: ((Double) -> Void)? = nil) throws -> URL {
        let doc = try NammaJSON.decoder().decode(SessionDocument.self, from: Data(contentsOf: dir.appendingPathComponent("session.json")))
        let outDir = outputDirectory ?? dir.appendingPathComponent("exports")
        try FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
        let base = Exporter.fileBase(doc.name, id: doc.id)
        let suffix = ["raw": "raw-data", "images": "images", "gltf": "gltf"][kind.rawValue]!
        let final = outDir.appendingPathComponent("\(base)-\(suffix).zip")
        let part = outDir.appendingPathComponent("\(base)-\(suffix).zip.part")
        try? FileManager.default.removeItem(at: part)
        do {
            let zw = try ZipWriter(url: part)
            switch kind {
            case .images: try writeImages(zw, dir, progress)
            case .gltf: try writeGLTF(zw, dir, base: base, progress)
            case .raw: try writeRaw(zw, dir, doc: doc, progress)
            }
            try zw.finish()
            // re-open to prove the archive is complete before it gets its final name
            _ = try ZipReader(url: part)
        } catch {
            try? FileManager.default.removeItem(at: part)
            throw error
        }
        if FileManager.default.fileExists(atPath: final.path) { try FileManager.default.removeItem(at: final) }
        try FileManager.default.moveItem(at: part, to: final)
        progress?(1)
        return final
    }

    static func writeImages(_ zw: ZipWriter, _ dir: URL, _ progress: ((Double) -> Void)?) throws {
        let fs = try frames(dir)
        guard !fs.isEmpty else { throw Error.noFrames }
        for (k, f) in fs.enumerated() {
            try zw.addFile(path: "images/\(f.frameId).jpg", from: dir.appendingPathComponent(f.image.path))
            progress?(Double(k + 1) / Double(fs.count))
        }
    }

    static func meshInfo() throws -> Data {
        // identity, column-major (as Polycam writes it): mesh and cameras are both in ARKit's world frame
        try JSONSerialization.data(withJSONObject: ["alignmentTransform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                                                    "generator": "Namma Capture"], options: [.sortedKeys])
    }

    /// Photo-textured mesh; if texturing fails (e.g. no photo saw the mesh) the vertex-coloured mesh is used and the
    /// reason is written into the zip (mesh_info.json "texture").
    static func writeGLTF(_ zw: ZipWriter, _ dir: URL, base: String, _ progress: ((Double) -> Void)?) throws {
        var note: [String: Any] = [:]
        var file: URL
        do {
            guard let t = try texturedMesh(sessionDirectory: dir, progress: { progress?($0 * 0.95) }) else { throw Error.noMesh }
            file = t.url; note = t.info
        } catch Error.noMesh {
            throw Error.noMesh
        } catch {
            guard let c = try coloredMesh(sessionDirectory: dir, progress: { progress?($0 * 0.95) }) else { throw Error.noMesh }
            file = c.url
            note = ["fallback": "vertex colours", "reason": "\(error)", "colored_fraction": c.coloredFraction]
        }
        try zw.addFile(path: "\(base).glb", from: file)
        var info = try JSONSerialization.jsonObject(with: meshInfo()) as! [String: Any]
        info["texture"] = note
        try zw.addData(path: "mesh_info.json", data: try JSONSerialization.data(withJSONObject: info, options: [.sortedKeys]))
    }

    static func cameraJSON(_ f: FrameRecord, intrinsics k: Intrinsics, model: String, namma: Bool) throws -> Data {
        let T = f.camera.tWorldFromCamera
        var d: [String: Any] = ["fx": k.fx, "fy": k.fy, "cx": k.cx, "cy": k.cy, "width": k.width, "height": k.height,
                                "timestamp": f.timestampS, "tracking_segment": f.coordinateFrameId,
                                "exif": ["LensModel": "\(model) back camera"]]
        for r in 0..<3 { for c in 0..<4 { d["t_\(r)\(c)"] = T[r][c] } }
        if let iso = f.exposure.iso { d["iso"] = iso }
        if let e = f.exposure.exposureDurationS { d["exposure_time"] = e }
        if namma {
            d["namma"] = try JSONSerialization.jsonObject(with: NammaJSON.encoder().encode(f))
        }
        return try JSONSerialization.data(withJSONObject: d, options: [.sortedKeys])
    }

    static func writeRaw(_ zw: ZipWriter, _ dir: URL, doc: SessionDocument, _ progress: ((Double) -> Void)?) throws {
        let fs = try frames(dir)
        guard !fs.isEmpty else { throw Error.noFrames }
        let model = doc.device.modelIdentifier
        var withDepth = 0
        for (k, f) in fs.enumerated() {
            let id = String(Int64((f.timestampS * 1_000_000).rounded()))
            let img = dir.appendingPathComponent(f.image.path)
            try zw.addData(path: "keyframes/cameras/\(id).json", data: cameraJSON(f, intrinsics: f.camera.intrinsics, model: model, namma: true))
            try zw.addFile(path: "keyframes/images/\(id).jpg", from: img)
            // corrected set (geometry steps): smaller photo, scaled intrinsics, same pose
            if let small = jpegDownscaled(img, longSide: correctedLongSide) {
                let ck = f.camera.intrinsics.scaled(toWidth: small.w, height: small.h)
                try zw.addData(path: "keyframes/corrected_cameras/\(id).json", data: cameraJSON(f, intrinsics: ck, model: model, namma: false))
                try zw.addData(path: "keyframes/corrected_images/\(id).jpg", data: small.data)
            }
            if let d = f.depth, let v = depthValues(dir, d) {
                let mm = v.map { $0.isFinite && $0 > 0 ? UInt16(clamping: Int(($0 * 1000).rounded())) : 0 }
                try zw.addData(path: "keyframes/depth/\(id).Clean.png", data: png16(mm, width: d.width, height: d.height))
                let up = upsampleNearest(mm, width: d.width, height: d.height, to: 512, 384)
                try zw.addData(path: "keyframes/corrected_depth/\(id).Clean.png", data: png16(up, width: 512, height: 384))
                var conf = [UInt8](repeating: 255, count: d.width * d.height)
                if let cp = d.confidencePath, let c = try? Data(contentsOf: dir.appendingPathComponent(cp)), c.count == conf.count {
                    conf = c.map { $0 == 0 ? 0 : ($0 == 1 ? 54 : 255) }
                }
                try zw.addData(path: "keyframes/confidence/\(id).png", data: png8(conf, width: d.width, height: d.height))
                withDepth += 1
            }
            progress?(Double(k + 1) / Double(fs.count))
        }
        try zw.addData(path: "mesh_info.json", data: try meshInfo())
        try zw.addData(path: "namma_capture.json", data: try JSONSerialization.data(withJSONObject: [
            "generator": "Namma Capture", "session_id": doc.id, "name": doc.name, "device": model,
            "frames": fs.count, "frames_with_depth": withDepth,
            "layout": "Polycam raw Images export (keyframes/...) as read by Namma Space polycam_raw.py",
            "notes": ["poses are ARKit's own (not refined); 'corrected_*' = the same pose, the photo at 2016 px, depth upsampled to 512x384 by nearest neighbour",
                      "ARKit images are already undistorted; nothing was sharpened",
                      "keyframe id = ARKit timestamp in microseconds",
                      "the coloured LiDAR mesh is in the separate glTF export; the full record is in the .nammacap export"],
        ], options: [.sortedKeys, .prettyPrinted]))
    }

    // MARK: image helpers

    static func jpegDownscaled(_ url: URL, longSide: Int) -> (data: Data, w: Int, h: Int)? {
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
              let cg = CGImageSourceCreateThumbnailAtIndex(src, 0, [kCGImageSourceCreateThumbnailFromImageAlways: true,
                                                                   kCGImageSourceThumbnailMaxPixelSize: longSide,
                                                                   kCGImageSourceCreateThumbnailWithTransform: false] as CFDictionary)
        else { return nil }
        let out = NSMutableData()
        guard let dst = CGImageDestinationCreateWithData(out, "public.jpeg" as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(dst, cg, [kCGImageDestinationLossyCompressionQuality: 0.92] as CFDictionary)
        guard CGImageDestinationFinalize(dst) else { return nil }
        return (out as Data, cg.width, cg.height)
    }

    static func upsampleNearest(_ v: [UInt16], width w: Int, height h: Int, to W: Int, _ H: Int) -> [UInt16] {
        var out = [UInt16](repeating: 0, count: W * H)
        for y in 0..<H { let sy = min(h - 1, y * h / H); for x in 0..<W { out[y * W + x] = v[sy * w + min(w - 1, x * w / W)] } }
        return out
    }

    /// 16-bit greyscale PNG (values as given).
    static func png16(_ v: [UInt16], width: Int, height: Int) throws -> Data {
        var be = v.map { $0.bigEndian }
        let data = Data(bytes: &be, count: be.count * 2)
        guard let prov = CGDataProvider(data: data as CFData),
              let cg = CGImage(width: width, height: height, bitsPerComponent: 16, bitsPerPixel: 16, bytesPerRow: width * 2,
                               space: CGColorSpaceCreateDeviceGray(),
                               bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue | CGImageByteOrderInfo.order16Big.rawValue),
                               provider: prov, decode: nil, shouldInterpolate: false, intent: .defaultIntent)
        else { throw Error.io("could not make a 16-bit PNG") }
        return try encodePNG(cg)
    }

    static func png8(_ v: [UInt8], width: Int, height: Int) throws -> Data {
        guard let prov = CGDataProvider(data: Data(v) as CFData),
              let cg = CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 8, bytesPerRow: width,
                               space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
                               provider: prov, decode: nil, shouldInterpolate: false, intent: .defaultIntent)
        else { throw Error.io("could not make an 8-bit PNG") }
        return try encodePNG(cg)
    }

    static func encodePNG(_ cg: CGImage) throws -> Data {
        let out = NSMutableData()
        guard let dst = CGImageDestinationCreateWithData(out, "public.png" as CFString, 1, nil) else { throw Error.io("PNG encoder unavailable") }
        CGImageDestinationAddImage(dst, cg, nil)
        guard CGImageDestinationFinalize(dst) else { throw Error.io("PNG encoding failed") }
        return out as Data
    }
}
