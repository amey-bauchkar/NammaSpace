import CoreGraphics
import Foundation
import ImageIO
import simd
@testable import NammaCaptureCore

/// Builds synthetic sessions through the same SessionStore / SessionWriter the app uses (no camera involved).
enum Fixtures {
    static func tempDir(_ name: String = "nc") -> URL {
        let u = FileManager.default.temporaryDirectory.appendingPathComponent("\(name)-\(UUID().uuidString)")
        try! FileManager.default.createDirectory(at: u, withIntermediateDirectories: true)
        return u
    }

    static func capabilities(depth: Bool) -> Capabilities {
        Capabilities(worldTracking: true, sceneDepth: depth, smoothedSceneDepth: depth, sceneReconstructionMesh: depth,
                     highResolutionFrameCapture: true, configurableCaptureDevice: true,
                     videoFormat: VideoFormatInfo(width: 1920, height: 1440, framesPerSecond: 60,
                                                  recommendedForHighResolutionCapture: true, hdrSupported: false))
    }

    static func settings() -> CaptureSettings {
        CaptureSettings(keyframe: KeyframeThresholds(), quality: QualityThresholds(), jpegQuality: 0.92,
                        trajectorySampleHz: 5,
                        cameraControls: [CameraControlResult(control: "exposure_lock", outcome: "unavailable",
                                                             detail: "synthetic fixture", at: ISOTime.string())])
    }

    /// Pose walking along +X looking down -Z, yawed a little each step.
    static func pose(_ i: Int) -> Pose {
        let yaw = Double(i) * 0.15
        let r = simd_double3x3(simd_quatd(angle: yaw, axis: SIMD3(0, 1, 0)))
        var m = matrix_identity_double4x4
        m.columns.0 = SIMD4(r.columns.0, 0); m.columns.1 = SIMD4(r.columns.1, 0); m.columns.2 = SIMD4(r.columns.2, 0)
        m.columns.3 = SIMD4(Double(i) * 0.3, 1.4, 0, 1)
        return Pose(m)
    }

    /// A small real JPEG (decodable), colour varying with i.
    static func jpeg(_ i: Int, width: Int = 64, height: Int = 48, rgb: (UInt8, UInt8, UInt8)? = nil) -> Data {
        let c = rgb ?? (UInt8(40 + (i * 37) % 200), UInt8(90), UInt8(200 - (i * 23) % 150))
        var px = [UInt8](repeating: 0, count: width * height * 4)
        for k in 0..<(width * height) { px[k * 4] = c.0; px[k * 4 + 1] = c.1; px[k * 4 + 2] = c.2; px[k * 4 + 3] = 255 }
        let ctx = CGContext(data: &px, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
                            space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
        let out = NSMutableData()
        let dst = CGImageDestinationCreateWithData(out, "public.jpeg" as CFString, 1, nil)!
        CGImageDestinationAddImage(dst, ctx.makeImage()!, [kCGImageDestinationLossyCompressionQuality: 0.95] as CFDictionary)
        CGImageDestinationFinalize(dst)
        return out as Data
    }

    struct Built { let store: SessionStore; let dir: URL; let id: String }

    /// Writes `frames` keyframes (+ optional depth), trajectory, events, a room change and an annotation.
    static func session(frames: Int = 6, depth: Bool = true, finish: Bool = true, name: String = "Test Flat") async throws -> Built {
        let root = tempDir("sessions")
        let store = try SessionStore(root: root)
        let id = UUID().uuidString.lowercased()
        var doc = SessionDocument(id: id, name: name, startedAt: ISOTime.string(),
                                  device: DeviceInfo(modelIdentifier: "iPhone15,4", systemName: "iOS", systemVersion: "18.0"),
                                  capabilities: capabilities(depth: depth), settings: settings(),
                                  depthSource: depth ? "arkit_scene_depth" : nil,
                                  depthUnavailableReason: depth ? nil : "device has no LiDAR (ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) == false)",
                                  appVersion: "0.1.0", appBuild: "1")
        doc.coordinateFrames = [CoordinateFrame(id: 0, startedAt: doc.startedAt, startedAtTimestampS: 0, reason: "session_start")]
        doc.rooms = [Room(id: "room_1", label: "Living room", createdAt: doc.startedAt),
                     Room(id: "room_2", label: "Kitchen", createdAt: doc.startedAt)]
        let dir = try store.create(doc)
        let w = SessionWriter(directory: dir)
        let k = Intrinsics(fx: 3000, fy: 3000, cx: 2016, cy: 1512, width: 4032, height: 3024)
        for i in 0..<frames {
            let p = pose(i)
            let fid = String(format: "f%06d", i + 1)
            var dref: DepthRef? = nil
            var dd: Data? = nil, cd: Data? = nil
            if depth {
                dref = DepthRef(path: "depth/\(fid).depth.f32", confidencePath: "depth/\(fid).conf.u8", width: 256, height: 192,
                                source: "arkit_scene_depth", intrinsics: k.scaled(toWidth: 256, height: 192),
                                timestampS: Double(i) * 0.8, tWorldFromCamera: p.rowMajor, pairing: "same_frame",
                                validSampleFraction: 0.97)
                dd = Data(repeating: 0, count: 256 * 192 * 4)
                cd = Data(repeating: 2, count: 256 * 192)
            }
            let rec = FrameRecord(
                frameId: fid, index: i + 1, timestampS: Double(i) * 0.8, wallclock: ISOTime.string(), coordinateFrameId: 0,
                roomId: i < frames / 2 ? "room_1" : "room_2",
                image: ImageRef(path: "images/\(fid).jpg", width: 4032, height: 3024, source: "high_resolution_capture",
                                interfaceOrientation: "portrait"),
                camera: CameraRecord(tWorldFromCamera: p.rowMajor, intrinsics: k, eulerAngles: [0, 0.15 * Double(i), 0]),
                tracking: TrackingRecord(state: "normal", reason: nil, worldMapping: "mapped"),
                exposure: ExposureRecord(exposureDurationS: 1.0 / 60, iso: 200, brightnessValue: nil),
                quality: QualityRecord(metrics: QualityMetrics(sharpness: 80, meanLuma: 120, darkFraction: 0.01, brightFraction: 0.01),
                                       warnings: [], overridden: false),
                selection: SelectionRecord(trigger: "auto", reason: i == 0 ? "first_frame" : "translation",
                                           translationSinceLastM: i == 0 ? nil : 0.3, rotationSinceLastDeg: i == 0 ? nil : 8.6),
                depth: dref, flags: [])
            await w.beginWrite()
            try await w.writeKeyframe(rec, image: jpeg(i), depth: dd, confidence: cd)
            try await w.writeTrajectory(TrajectorySample(timestampS: Double(i) * 0.8, coordinateFrameId: 0,
                                                         position: [p.position.x, p.position.y, p.position.z],
                                                         quaternionXYZW: p.quaternionXYZW, tracking: "normal", trackingReason: nil))
        }
        try await w.writeRejection(RejectionRecord(candidateIndex: 3, timestampS: 1.1, coordinateFrameId: 0, trigger: "auto",
                                                   reasons: ["blurry"], metrics: QualityMetrics(sharpness: 5, meanLuma: 100, darkFraction: 0, brightFraction: 0)))
        try await w.writeEvent(ScanEvent(seq: 1, type: EventType.sessionStart, timestampS: 0, wallclock: ISOTime.string(),
                                         coordinateFrameId: 0, roomId: "room_1", position: nil, frameId: nil))
        try await w.writeEvent(ScanEvent(seq: 2, type: EventType.doorway, timestampS: 2.0, wallclock: ISOTime.string(),
                                         coordinateFrameId: 0, roomId: "room_2", position: [0.9, 1.4, 0], frameId: nil,
                                         data: ["from_room": "room_1", "to_room": "room_2", "doorway_id": "door_1"]))
        try await w.writeAnnotations(AnnotationsDocument(annotations: [
            SurfaceAnnotation(id: "ann_1", kind: "mirror", frameId: "f000002", imagePointNormalized: [0.5, 0.4], coordinateFrameId: 0,
                              tWorldFromCamera: pose(1).rowMajor, worldPoint: nil, worldPointMethod: nil, roomId: "room_1",
                              note: "", createdAt: ISOTime.string(), updatedAt: ISOTime.string())]))
        if depth {      // LiDAR sessions carry a scene mesh: a 1 m floor quad
            let glb = try GLBWriter.data(positions: [0, 0, 0, 1, 0, 0, 1, 0, -1, 0, 0, -1], indices: [0, 1, 2, 0, 2, 3])
            try FileManager.default.createDirectory(at: dir.appendingPathComponent("mesh"), withIntermediateDirectories: true)
            try glb.write(to: dir.appendingPathComponent(NammaCapFormat.meshPath))
        }
        var cov = CoverageModel()
        cov.setFloor(y: 0)
        cov.addCameraView(pose: pose(0))
        try await w.writeCoverage(ScanCoverage(rooms: [cov.summary(roomId: "room_1")]))
        await w.close()
        if finish {
            doc.state = "finished"; doc.endedAt = ISOTime.string()
            try store.save(doc)
        }
        return Built(store: store, dir: dir, id: id)
    }
}
