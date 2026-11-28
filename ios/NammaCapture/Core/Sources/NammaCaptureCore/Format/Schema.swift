import Foundation

/// Namma Capture archive format (`.nammacap`), schema 1.0. The normative description is docs/NAMMACAP_FORMAT.md;
/// these types are the writer's view of it. Every optional field is encoded explicitly as `null` when absent
/// (see `NullEncoding`), so a reader can tell "not available" from "forgotten".
public enum NammaCapFormat {
    public static let formatName = "nammacap"
    public static let schemaVersion = SchemaVersion(major: 1, minor: 0)
    public static let fileExtension = "nammacap"

    // Fixed archive member names.
    public static let manifestPath = "manifest.json"
    public static let integrityPath = "integrity.json"
    public static let framesPath = "frames.jsonl"
    public static let rejectionsPath = "rejections.jsonl"
    public static let trajectoryPath = "trajectory.jsonl"
    public static let eventsPath = "events.jsonl"
    public static let annotationsPath = "annotations.json"
    public static let coveragePath = "coverage.json"
    public static let imagesDir = "images"
    public static let depthDir = "depth"
    public static let meshPath = "mesh/arkit_scene_mesh.glb"
}

public struct SchemaVersion: Codable, Comparable, Hashable, CustomStringConvertible, Sendable {
    public var major: Int
    public var minor: Int
    public init(major: Int, minor: Int) { self.major = major; self.minor = minor }

    public init?(string: String) {
        let parts = string.split(separator: ".")
        guard parts.count == 2, let a = Int(parts[0]), let b = Int(parts[1]), a >= 0, b >= 0 else { return nil }
        major = a; minor = b
    }
    public var description: String { "\(major).\(minor)" }
    public static func < (l: SchemaVersion, r: SchemaVersion) -> Bool { (l.major, l.minor) < (r.major, r.minor) }

    public init(from decoder: Decoder) throws {
        let s = try decoder.singleValueContainer().decode(String.self)
        guard let v = SchemaVersion(string: s) else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "bad version \(s)"))
        }
        self = v
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(description)
    }
}

// MARK: - Manifest

public struct Manifest: Codable, Sendable {
    public var format = NammaCapFormat.formatName
    public var schemaVersion = NammaCapFormat.schemaVersion
    /// Oldest reader version that can read this archive correctly. Readers whose major differs must refuse.
    public var minReaderVersion = SchemaVersion(major: 1, minor: 0)
    public var createdBy: CreatedBy
    public var session: SessionInfo
    public var device: DeviceInfo
    public var capabilities: Capabilities
    public var conventions = Conventions()
    public var depth: DepthInfo
    public var captureSettings: CaptureSettings
    public var scale: ScaleInfo
    public var coordinateFrames: [CoordinateFrame]
    public var rooms: [Room]
    public var counts: Counts
    public var files = FileIndex()
    public var warnings: [String]
    public var recovery: RecoveryInfo?

    public init(createdBy: CreatedBy, session: SessionInfo, device: DeviceInfo, capabilities: Capabilities,
                depth: DepthInfo, captureSettings: CaptureSettings, scale: ScaleInfo,
                coordinateFrames: [CoordinateFrame], rooms: [Room], counts: Counts, warnings: [String],
                recovery: RecoveryInfo?) {
        self.createdBy = createdBy; self.session = session; self.device = device; self.capabilities = capabilities
        self.depth = depth; self.captureSettings = captureSettings; self.scale = scale
        self.coordinateFrames = coordinateFrames; self.rooms = rooms; self.counts = counts
        self.warnings = warnings; self.recovery = recovery
    }

    enum CodingKeys: String, CodingKey {
        case format, schemaVersion = "schema_version", minReaderVersion = "min_reader_version",
             createdBy = "created_by", session, device, capabilities, conventions, depth,
             captureSettings = "capture_settings", scale, coordinateFrames = "coordinate_frames", rooms, counts,
             files, warnings, recovery
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(format, forKey: .format); try c.encode(schemaVersion, forKey: .schemaVersion)
        try c.encode(minReaderVersion, forKey: .minReaderVersion); try c.encode(createdBy, forKey: .createdBy)
        try c.encode(session, forKey: .session); try c.encode(device, forKey: .device)
        try c.encode(capabilities, forKey: .capabilities); try c.encode(conventions, forKey: .conventions)
        try c.encode(depth, forKey: .depth); try c.encode(captureSettings, forKey: .captureSettings)
        try c.encode(scale, forKey: .scale); try c.encode(coordinateFrames, forKey: .coordinateFrames)
        try c.encode(rooms, forKey: .rooms); try c.encode(counts, forKey: .counts); try c.encode(files, forKey: .files)
        try c.encode(warnings, forKey: .warnings); try c.encodeNullable(recovery, forKey: .recovery)
    }
}

public struct CreatedBy: Codable, Sendable {
    public var app: String
    public var appVersion: String
    public var build: String
    public init(app: String = "Namma Capture", appVersion: String, build: String) {
        self.app = app; self.appVersion = appVersion; self.build = build
    }
    enum CodingKeys: String, CodingKey { case app, appVersion = "app_version", build }
}

public struct SessionInfo: Codable, Sendable {
    public var id: String
    public var name: String
    public var startedAt: String
    public var endedAt: String?
    /// "complete" for a normally finished scan, "recovered" for one finished after an interruption.
    public var status: String
    public init(id: String, name: String, startedAt: String, endedAt: String?, status: String) {
        self.id = id; self.name = name; self.startedAt = startedAt; self.endedAt = endedAt; self.status = status
    }
    enum CodingKeys: String, CodingKey { case id, name, startedAt = "started_at", endedAt = "ended_at", status }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id); try c.encode(name, forKey: .name); try c.encode(startedAt, forKey: .startedAt)
        try c.encodeNullable(endedAt, forKey: .endedAt); try c.encode(status, forKey: .status)
    }
}

public struct DeviceInfo: Codable, Sendable {
    public var modelIdentifier: String
    public var systemName: String
    public var systemVersion: String
    public init(modelIdentifier: String, systemName: String, systemVersion: String) {
        self.modelIdentifier = modelIdentifier; self.systemName = systemName; self.systemVersion = systemVersion
    }
    enum CodingKeys: String, CodingKey {
        case modelIdentifier = "model_identifier", systemName = "system_name", systemVersion = "system_version"
    }
}

/// What the device reported at session start. `false` means "the API said no", never "we did not check".
public struct Capabilities: Codable, Sendable {
    public var worldTracking: Bool
    public var sceneDepth: Bool
    public var smoothedSceneDepth: Bool
    public var sceneReconstructionMesh: Bool
    public var highResolutionFrameCapture: Bool
    public var configurableCaptureDevice: Bool
    public var videoFormat: VideoFormatInfo?
    public init(worldTracking: Bool, sceneDepth: Bool, smoothedSceneDepth: Bool, sceneReconstructionMesh: Bool,
                highResolutionFrameCapture: Bool, configurableCaptureDevice: Bool, videoFormat: VideoFormatInfo?) {
        self.worldTracking = worldTracking; self.sceneDepth = sceneDepth; self.smoothedSceneDepth = smoothedSceneDepth
        self.sceneReconstructionMesh = sceneReconstructionMesh; self.highResolutionFrameCapture = highResolutionFrameCapture
        self.configurableCaptureDevice = configurableCaptureDevice; self.videoFormat = videoFormat
    }
    enum CodingKeys: String, CodingKey {
        case worldTracking = "world_tracking", sceneDepth = "scene_depth", smoothedSceneDepth = "smoothed_scene_depth",
             sceneReconstructionMesh = "scene_reconstruction_mesh",
             highResolutionFrameCapture = "high_resolution_frame_capture",
             configurableCaptureDevice = "configurable_capture_device", videoFormat = "video_format"
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(worldTracking, forKey: .worldTracking); try c.encode(sceneDepth, forKey: .sceneDepth)
        try c.encode(smoothedSceneDepth, forKey: .smoothedSceneDepth)
        try c.encode(sceneReconstructionMesh, forKey: .sceneReconstructionMesh)
        try c.encode(highResolutionFrameCapture, forKey: .highResolutionFrameCapture)
        try c.encode(configurableCaptureDevice, forKey: .configurableCaptureDevice)
        try c.encodeNullable(videoFormat, forKey: .videoFormat)
    }
}

public struct VideoFormatInfo: Codable, Sendable {
    public var width: Int
    public var height: Int
    public var framesPerSecond: Int
    public var recommendedForHighResolutionCapture: Bool
    public var hdrSupported: Bool
    public init(width: Int, height: Int, framesPerSecond: Int, recommendedForHighResolutionCapture: Bool, hdrSupported: Bool) {
        self.width = width; self.height = height; self.framesPerSecond = framesPerSecond
        self.recommendedForHighResolutionCapture = recommendedForHighResolutionCapture; self.hdrSupported = hdrSupported
    }
    enum CodingKeys: String, CodingKey {
        case width, height, framesPerSecond = "frames_per_second",
             recommendedForHighResolutionCapture = "recommended_for_high_resolution_capture", hdrSupported = "hdr_supported"
    }
}

/// Fixed for schema 1.x. Documented in NAMMACAP_FORMAT.md §Conventions.
public struct Conventions: Codable, Sendable {
    public var world = "arkit_world: right-handed, +Y up (opposite gravity), origin and heading set at session start"
    public var units = "meters, seconds"
    public var pose = "T_world_from_camera (camera-to-world), 4x4, row-major nested arrays, rows[3] = [0,0,0,1]"
    public var cameraAxes = "arkit_camera: +X right, +Y up, -Z viewing direction, relative to the stored (sensor landscape) image"
    public var imageOrientation = "sensor_landscape: stored pixels are not rotated for the UI; see image.interface_orientation"
    public var intrinsics = "pixels of the stored image; fx, fy, cx, cy; no distortion model (ARKit images are undistorted)"
    public var timestamps = "timestamp_s = ARFrame.timestamp (device uptime clock, s); wallclock = ISO-8601 UTC"
    public var colmap = "T_cam_from_world_colmap = diag(1,-1,-1,1) * inverse(T_world_from_camera)"
    public init() {}
    enum CodingKeys: String, CodingKey {
        case world, units, pose, cameraAxes = "camera_axes", imageOrientation = "image_orientation", intrinsics,
             timestamps, colmap
    }
}

public struct DepthInfo: Codable, Sendable {
    /// true only when at least one frame carries measured LiDAR depth.
    public var available: Bool
    /// "arkit_scene_depth" | "arkit_smoothed_scene_depth" | null. Never an estimate.
    public var source: String?
    public var unavailableReason: String?
    public var depthEncoding = "float32 little-endian, row-major, meters along the camera's viewing axis; 0 or non-finite = no measurement"
    public var confidenceEncoding = "uint8 row-major, ARConfidenceLevel: 0 low, 1 medium, 2 high"
    public init(available: Bool, source: String?, unavailableReason: String?) {
        self.available = available; self.source = source; self.unavailableReason = unavailableReason
    }
    enum CodingKeys: String, CodingKey {
        case available, source, unavailableReason = "unavailable_reason", depthEncoding = "depth_encoding",
             confidenceEncoding = "confidence_encoding"
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(available, forKey: .available); try c.encodeNullable(source, forKey: .source)
        try c.encodeNullable(unavailableReason, forKey: .unavailableReason)
        try c.encode(depthEncoding, forKey: .depthEncoding); try c.encode(confidenceEncoding, forKey: .confidenceEncoding)
    }
}

public struct CaptureSettings: Codable, Sendable {
    public var keyframe: KeyframeThresholds
    public var quality: QualityThresholds
    public var imageEncoding: String
    public var jpegQuality: Double
    public var trajectorySampleHz: Double
    /// One entry per requested camera control, with what actually happened.
    public var cameraControls: [CameraControlResult]
    public init(keyframe: KeyframeThresholds, quality: QualityThresholds, imageEncoding: String = "jpeg",
                jpegQuality: Double, trajectorySampleHz: Double, cameraControls: [CameraControlResult]) {
        self.keyframe = keyframe; self.quality = quality; self.imageEncoding = imageEncoding; self.jpegQuality = jpegQuality
        self.trajectorySampleHz = trajectorySampleHz; self.cameraControls = cameraControls
    }
    enum CodingKeys: String, CodingKey {
        case keyframe, quality, imageEncoding = "image_encoding", jpegQuality = "jpeg_quality",
             trajectorySampleHz = "trajectory_sample_hz", cameraControls = "camera_controls"
    }
}

public struct CameraControlResult: Codable, Sendable, Equatable {
    /// e.g. "exposure_lock", "white_balance_lock", "focus_lock", "video_hdr"
    public var control: String
    /// "locked" | "enabled" | "disabled" | "unavailable" | "failed" | "not_requested"
    public var outcome: String
    public var detail: String?
    public var at: String
    public init(control: String, outcome: String, detail: String?, at: String) {
        self.control = control; self.outcome = outcome; self.detail = detail; self.at = at
    }
    enum CodingKeys: String, CodingKey { case control, outcome, detail, at }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(control, forKey: .control); try c.encode(outcome, forKey: .outcome)
        try c.encodeNullable(detail, forKey: .detail); try c.encode(at, forKey: .at)
    }
}

public struct ScaleInfo: Codable, Sendable {
    /// Always "arkit_visual_inertial" for schema 1.0: poses are in ARKit's metric world.
    public var provenance = "arkit_visual_inertial"
    /// "device_tracked_unverified" | "user_validated" | "user_validation_failed"
    public var status: String
    public var validations: [ScaleValidation]
    public var note = "ARKit reports meters from visual-inertial tracking. That is not a survey-grade measurement; see validations."
    public init(status: String, validations: [ScaleValidation]) { self.status = status; self.validations = validations }
    enum CodingKeys: String, CodingKey { case provenance, status, validations, note }
}

public struct ScaleValidation: Codable, Sendable, Equatable {
    public var id: String
    public var at: String
    public var coordinateFrameId: Int
    public var pointA: [Double]
    public var pointB: [Double]
    public var measuredMeters: Double
    public var trueMeters: Double
    /// measured / true
    public var ratio: Double
    public var method: String
    public init(id: String, at: String, coordinateFrameId: Int, pointA: [Double], pointB: [Double],
                measuredMeters: Double, trueMeters: Double, method: String) {
        self.id = id; self.at = at; self.coordinateFrameId = coordinateFrameId; self.pointA = pointA; self.pointB = pointB
        self.measuredMeters = measuredMeters; self.trueMeters = trueMeters
        self.ratio = trueMeters > 0 ? measuredMeters / trueMeters : 0; self.method = method
    }
    enum CodingKeys: String, CodingKey {
        case id, at, coordinateFrameId = "coordinate_frame_id", pointA = "point_a", pointB = "point_b",
             measuredMeters = "measured_m", trueMeters = "true_m", ratio, method
    }
}

/// A span of capture that shares one ARKit world. A new one starts whenever tracking is reset; frames from
/// different coordinate frames must not be combined without registration.
public struct CoordinateFrame: Codable, Sendable, Equatable {
    public var id: Int
    public var startedAt: String
    public var startedAtTimestampS: Double?
    public var reason: String
    public init(id: Int, startedAt: String, startedAtTimestampS: Double?, reason: String) {
        self.id = id; self.startedAt = startedAt; self.startedAtTimestampS = startedAtTimestampS; self.reason = reason
    }
    enum CodingKeys: String, CodingKey { case id, startedAt = "started_at", startedAtTimestampS = "started_at_timestamp_s", reason }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id); try c.encode(startedAt, forKey: .startedAt)
        try c.encodeNullable(startedAtTimestampS, forKey: .startedAtTimestampS); try c.encode(reason, forKey: .reason)
    }
}

public struct Room: Codable, Sendable, Equatable, Identifiable {
    public var id: String
    public var label: String
    public var createdAt: String
    public init(id: String, label: String, createdAt: String) { self.id = id; self.label = label; self.createdAt = createdAt }
    enum CodingKeys: String, CodingKey { case id, label, createdAt = "created_at" }
}

public struct Counts: Codable, Sendable, Equatable {
    public var framesAccepted = 0
    public var framesRejected = 0
    public var framesWithDepth = 0
    public var framesHighResolution = 0
    public var trajectorySamples = 0
    public var events = 0
    public var annotations = 0
    public init() {}
    enum CodingKeys: String, CodingKey {
        case framesAccepted = "frames_accepted", framesRejected = "frames_rejected", framesWithDepth = "frames_with_depth",
             framesHighResolution = "frames_high_resolution", trajectorySamples = "trajectory_samples", events, annotations
    }
}

public struct FileIndex: Codable, Sendable {
    public var frames = NammaCapFormat.framesPath
    public var rejections = NammaCapFormat.rejectionsPath
    public var trajectory = NammaCapFormat.trajectoryPath
    public var events = NammaCapFormat.eventsPath
    public var annotations = NammaCapFormat.annotationsPath
    public var coverage = NammaCapFormat.coveragePath
    public var integrity = NammaCapFormat.integrityPath
    /// Optional LiDAR scene-reconstruction mesh (glTF binary, ARKit world coordinates); null when not captured.
    public var mesh: String?
    public init() {}
    enum CodingKeys: String, CodingKey { case frames, rejections, trajectory, events, annotations, coverage, integrity, mesh }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        frames = try c.decode(String.self, forKey: .frames); rejections = try c.decode(String.self, forKey: .rejections)
        trajectory = try c.decode(String.self, forKey: .trajectory); events = try c.decode(String.self, forKey: .events)
        annotations = try c.decode(String.self, forKey: .annotations); coverage = try c.decode(String.self, forKey: .coverage)
        integrity = try c.decode(String.self, forKey: .integrity); mesh = try c.decodeIfPresent(String.self, forKey: .mesh)
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(frames, forKey: .frames); try c.encode(rejections, forKey: .rejections)
        try c.encode(trajectory, forKey: .trajectory); try c.encode(events, forKey: .events)
        try c.encode(annotations, forKey: .annotations); try c.encode(coverage, forKey: .coverage)
        try c.encode(integrity, forKey: .integrity); try c.encodeNullable(mesh, forKey: .mesh)
    }
}

public struct RecoveryInfo: Codable, Sendable, Equatable {
    public var interrupted: Bool
    public var droppedPartialLines: Int
    public var framesWithoutImage: [String]
    public var notes: [String]
    public init(interrupted: Bool, droppedPartialLines: Int, framesWithoutImage: [String], notes: [String]) {
        self.interrupted = interrupted; self.droppedPartialLines = droppedPartialLines
        self.framesWithoutImage = framesWithoutImage; self.notes = notes
    }
    enum CodingKeys: String, CodingKey {
        case interrupted, droppedPartialLines = "dropped_partial_lines", framesWithoutImage = "frames_without_image", notes
    }
}

// MARK: - Per-frame records

public struct FrameRecord: Codable, Sendable, Equatable {
    public var frameId: String
    public var index: Int
    public var timestampS: Double
    public var wallclock: String
    public var coordinateFrameId: Int
    public var roomId: String?
    public var image: ImageRef
    public var camera: CameraRecord
    public var tracking: TrackingRecord
    public var exposure: ExposureRecord
    public var quality: QualityRecord
    public var selection: SelectionRecord
    public var depth: DepthRef?
    public var flags: [String]

    public init(frameId: String, index: Int, timestampS: Double, wallclock: String, coordinateFrameId: Int,
                roomId: String?, image: ImageRef, camera: CameraRecord, tracking: TrackingRecord,
                exposure: ExposureRecord, quality: QualityRecord, selection: SelectionRecord, depth: DepthRef?,
                flags: [String]) {
        self.frameId = frameId; self.index = index; self.timestampS = timestampS; self.wallclock = wallclock
        self.coordinateFrameId = coordinateFrameId; self.roomId = roomId; self.image = image; self.camera = camera
        self.tracking = tracking; self.exposure = exposure; self.quality = quality; self.selection = selection
        self.depth = depth; self.flags = flags
    }
    enum CodingKeys: String, CodingKey {
        case frameId = "frame_id", index, timestampS = "timestamp_s", wallclock, coordinateFrameId = "coordinate_frame_id",
             roomId = "room_id", image, camera, tracking, exposure, quality, selection, depth, flags
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(frameId, forKey: .frameId); try c.encode(index, forKey: .index)
        try c.encode(timestampS, forKey: .timestampS); try c.encode(wallclock, forKey: .wallclock)
        try c.encode(coordinateFrameId, forKey: .coordinateFrameId); try c.encodeNullable(roomId, forKey: .roomId)
        try c.encode(image, forKey: .image); try c.encode(camera, forKey: .camera); try c.encode(tracking, forKey: .tracking)
        try c.encode(exposure, forKey: .exposure); try c.encode(quality, forKey: .quality)
        try c.encode(selection, forKey: .selection); try c.encodeNullable(depth, forKey: .depth)
        try c.encode(flags, forKey: .flags)
    }
}

public struct ImageRef: Codable, Sendable, Equatable {
    public var path: String
    public var width: Int
    public var height: Int
    /// "high_resolution_capture" (ARSession.captureHighResolutionFrame) or "video_stream" (ARFrame.capturedImage)
    public var source: String
    public var interfaceOrientation: String
    public init(path: String, width: Int, height: Int, source: String, interfaceOrientation: String) {
        self.path = path; self.width = width; self.height = height; self.source = source
        self.interfaceOrientation = interfaceOrientation
    }
    enum CodingKeys: String, CodingKey { case path, width, height, source, interfaceOrientation = "interface_orientation" }
}

public struct Intrinsics: Codable, Sendable, Equatable {
    public var fx: Double, fy: Double, cx: Double, cy: Double
    public var width: Int, height: Int
    public init(fx: Double, fy: Double, cx: Double, cy: Double, width: Int, height: Int) {
        self.fx = fx; self.fy = fy; self.cx = cx; self.cy = cy; self.width = width; self.height = height
    }
    /// The same pinhole camera for an image of another size covering the same field of view.
    public func scaled(toWidth w: Int, height h: Int) -> Intrinsics {
        let sx = Double(w) / Double(width), sy = Double(h) / Double(height)
        return Intrinsics(fx: fx * sx, fy: fy * sy, cx: cx * sx, cy: cy * sy, width: w, height: h)
    }
}

public struct CameraRecord: Codable, Sendable, Equatable {
    /// T_world_from_camera, row-major.
    public var tWorldFromCamera: [[Double]]
    public var intrinsics: Intrinsics
    /// Euler angles reported by ARKit (pitch, yaw, roll; radians) for convenience only.
    public var eulerAngles: [Double]
    public init(tWorldFromCamera: [[Double]], intrinsics: Intrinsics, eulerAngles: [Double]) {
        self.tWorldFromCamera = tWorldFromCamera; self.intrinsics = intrinsics; self.eulerAngles = eulerAngles
    }
    enum CodingKeys: String, CodingKey { case tWorldFromCamera = "T_world_from_camera", intrinsics, eulerAngles = "euler_angles_rad" }
}

public struct TrackingRecord: Codable, Sendable, Equatable {
    /// "normal" | "limited" | "not_available"
    public var state: String
    /// null when normal; otherwise "initializing" | "excessive_motion" | "insufficient_features" | "relocalizing" | "unknown"
    public var reason: String?
    /// "not_available" | "limited" | "extending" | "mapped"
    public var worldMapping: String
    public init(state: String, reason: String?, worldMapping: String) {
        self.state = state; self.reason = reason; self.worldMapping = worldMapping
    }
    enum CodingKeys: String, CodingKey { case state, reason, worldMapping = "world_mapping" }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(state, forKey: .state); try c.encodeNullable(reason, forKey: .reason)
        try c.encode(worldMapping, forKey: .worldMapping)
    }
}

/// Values the frame actually carried; null = the device did not report it.
public struct ExposureRecord: Codable, Sendable, Equatable {
    public var exposureDurationS: Double?
    public var iso: Double?
    public var brightnessValue: Double?
    public var exposureBiasEV: Double?
    public var ambientIntensityLumen: Double?
    public var ambientColorTemperatureK: Double?
    public init(exposureDurationS: Double? = nil, iso: Double? = nil, brightnessValue: Double? = nil,
                exposureBiasEV: Double? = nil, ambientIntensityLumen: Double? = nil, ambientColorTemperatureK: Double? = nil) {
        self.exposureDurationS = exposureDurationS; self.iso = iso; self.brightnessValue = brightnessValue
        self.exposureBiasEV = exposureBiasEV; self.ambientIntensityLumen = ambientIntensityLumen
        self.ambientColorTemperatureK = ambientColorTemperatureK
    }
    enum CodingKeys: String, CodingKey {
        case exposureDurationS = "exposure_duration_s", iso, brightnessValue = "brightness_value",
             exposureBiasEV = "exposure_bias_ev", ambientIntensityLumen = "ambient_intensity_lm",
             ambientColorTemperatureK = "ambient_color_temperature_k"
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeNullable(exposureDurationS, forKey: .exposureDurationS); try c.encodeNullable(iso, forKey: .iso)
        try c.encodeNullable(brightnessValue, forKey: .brightnessValue)
        try c.encodeNullable(exposureBiasEV, forKey: .exposureBiasEV)
        try c.encodeNullable(ambientIntensityLumen, forKey: .ambientIntensityLumen)
        try c.encodeNullable(ambientColorTemperatureK, forKey: .ambientColorTemperatureK)
    }
}

public struct QualityRecord: Codable, Sendable, Equatable {
    public var metrics: QualityMetrics
    /// Non-blocking issues (frame kept).
    public var warnings: [String]
    /// true when the user forced the frame past a failed gate.
    public var overridden: Bool
    public init(metrics: QualityMetrics, warnings: [String], overridden: Bool) {
        self.metrics = metrics; self.warnings = warnings; self.overridden = overridden
    }
}

public struct SelectionRecord: Codable, Sendable, Equatable {
    /// "auto" | "manual" | "annotation" | "loop_back"
    public var trigger: String
    /// why the candidate was considered: "first_frame" | "translation" | "rotation" | "manual" | "stale"
    public var reason: String
    public var translationSinceLastM: Double?
    public var rotationSinceLastDeg: Double?
    public init(trigger: String, reason: String, translationSinceLastM: Double?, rotationSinceLastDeg: Double?) {
        self.trigger = trigger; self.reason = reason
        self.translationSinceLastM = translationSinceLastM; self.rotationSinceLastDeg = rotationSinceLastDeg
    }
    enum CodingKeys: String, CodingKey {
        case trigger, reason, translationSinceLastM = "translation_since_last_m", rotationSinceLastDeg = "rotation_since_last_deg"
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(trigger, forKey: .trigger); try c.encode(reason, forKey: .reason)
        try c.encodeNullable(translationSinceLastM, forKey: .translationSinceLastM)
        try c.encodeNullable(rotationSinceLastDeg, forKey: .rotationSinceLastDeg)
    }
}

public struct DepthRef: Codable, Sendable, Equatable {
    public var path: String
    public var confidencePath: String?
    public var width: Int
    public var height: Int
    /// "arkit_scene_depth" | "arkit_smoothed_scene_depth"
    public var source: String
    /// Pinhole model for the depth grid: the intrinsics of the ARFrame that produced the depth, scaled to the depth size
    /// (ARKit depth covers that frame's full field of view).
    public var intrinsics: Intrinsics
    /// Pose and time of the ARFrame that produced this depth map. Equal to the image's when `pairing` is "same_frame";
    /// otherwise the depth came from the video-stream frame just before the high-resolution still, and must be
    /// reprojected with this pose, not the image's.
    public var timestampS: Double
    public var tWorldFromCamera: [[Double]]
    /// "same_frame" | "stream_frame_before_still"
    public var pairing: String
    public var validSampleFraction: Double
    public init(path: String, confidencePath: String?, width: Int, height: Int, source: String, intrinsics: Intrinsics,
                timestampS: Double, tWorldFromCamera: [[Double]], pairing: String, validSampleFraction: Double) {
        self.path = path; self.confidencePath = confidencePath; self.width = width; self.height = height
        self.source = source; self.intrinsics = intrinsics; self.timestampS = timestampS
        self.tWorldFromCamera = tWorldFromCamera; self.pairing = pairing; self.validSampleFraction = validSampleFraction
    }
    enum CodingKeys: String, CodingKey {
        case path, confidencePath = "confidence_path", width, height, source, intrinsics,
             timestampS = "timestamp_s", tWorldFromCamera = "T_world_from_camera", pairing,
             validSampleFraction = "valid_sample_fraction"
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(path, forKey: .path); try c.encodeNullable(confidencePath, forKey: .confidencePath)
        try c.encode(width, forKey: .width); try c.encode(height, forKey: .height); try c.encode(source, forKey: .source)
        try c.encode(intrinsics, forKey: .intrinsics); try c.encode(timestampS, forKey: .timestampS)
        try c.encode(tWorldFromCamera, forKey: .tWorldFromCamera); try c.encode(pairing, forKey: .pairing)
        try c.encode(finiteOrZero(validSampleFraction), forKey: .validSampleFraction)
    }
}

public struct RejectionRecord: Codable, Sendable, Equatable {
    public var candidateIndex: Int
    public var timestampS: Double
    public var coordinateFrameId: Int
    public var trigger: String
    public var reasons: [String]
    public var metrics: QualityMetrics?
    public init(candidateIndex: Int, timestampS: Double, coordinateFrameId: Int, trigger: String, reasons: [String],
                metrics: QualityMetrics?) {
        self.candidateIndex = candidateIndex; self.timestampS = timestampS; self.coordinateFrameId = coordinateFrameId
        self.trigger = trigger; self.reasons = reasons; self.metrics = metrics
    }
    enum CodingKeys: String, CodingKey {
        case candidateIndex = "candidate_index", timestampS = "timestamp_s", coordinateFrameId = "coordinate_frame_id",
             trigger, reasons, metrics
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(candidateIndex, forKey: .candidateIndex); try c.encode(timestampS, forKey: .timestampS)
        try c.encode(coordinateFrameId, forKey: .coordinateFrameId); try c.encode(trigger, forKey: .trigger)
        try c.encode(reasons, forKey: .reasons); try c.encodeNullable(metrics, forKey: .metrics)
    }
}

public struct TrajectorySample: Codable, Sendable, Equatable {
    public var timestampS: Double
    public var coordinateFrameId: Int
    public var position: [Double]
    /// x, y, z, w of the camera-to-world rotation
    public var quaternionXYZW: [Double]
    public var tracking: String
    public var trackingReason: String?
    public init(timestampS: Double, coordinateFrameId: Int, position: [Double], quaternionXYZW: [Double],
                tracking: String, trackingReason: String?) {
        self.timestampS = timestampS; self.coordinateFrameId = coordinateFrameId; self.position = position
        self.quaternionXYZW = quaternionXYZW; self.tracking = tracking; self.trackingReason = trackingReason
    }
    enum CodingKeys: String, CodingKey {
        case timestampS = "timestamp_s", coordinateFrameId = "coordinate_frame_id", position,
             quaternionXYZW = "quaternion_xyzw", tracking, trackingReason = "tracking_reason"
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(timestampS, forKey: .timestampS); try c.encode(coordinateFrameId, forKey: .coordinateFrameId)
        try c.encode(position, forKey: .position); try c.encode(quaternionXYZW, forKey: .quaternionXYZW)
        try c.encode(tracking, forKey: .tracking); try c.encodeNullable(trackingReason, forKey: .trackingReason)
    }
}

public struct ScanEvent: Codable, Sendable, Equatable {
    public var seq: Int
    public var type: String
    public var timestampS: Double?
    public var wallclock: String
    public var coordinateFrameId: Int?
    public var roomId: String?
    public var position: [Double]?
    public var frameId: String?
    public var data: [String: String]
    public init(seq: Int, type: String, timestampS: Double?, wallclock: String, coordinateFrameId: Int?, roomId: String?,
                position: [Double]?, frameId: String?, data: [String: String] = [:]) {
        self.seq = seq; self.type = type; self.timestampS = timestampS; self.wallclock = wallclock
        self.coordinateFrameId = coordinateFrameId; self.roomId = roomId; self.position = position
        self.frameId = frameId; self.data = data
    }
    enum CodingKeys: String, CodingKey {
        case seq, type, timestampS = "timestamp_s", wallclock, coordinateFrameId = "coordinate_frame_id",
             roomId = "room_id", position, frameId = "frame_id", data
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(seq, forKey: .seq); try c.encode(type, forKey: .type)
        try c.encodeNullable(timestampS, forKey: .timestampS); try c.encode(wallclock, forKey: .wallclock)
        try c.encodeNullable(coordinateFrameId, forKey: .coordinateFrameId); try c.encodeNullable(roomId, forKey: .roomId)
        try c.encodeNullable(position, forKey: .position); try c.encodeNullable(frameId, forKey: .frameId)
        try c.encode(data, forKey: .data)
    }
}

/// Event types written to events.jsonl (open set; readers must ignore types they do not know).
public enum EventType {
    public static let sessionStart = "session_start"
    public static let sessionEnd = "session_end"
    public static let coverageOverride = "coverage_override"
    public static let roomStart = "room_start"
    public static let roomRenamed = "room_renamed"
    public static let doorway = "doorway"
    public static let doorwayLookBackCaptured = "doorway_look_back_captured"
    public static let loopBack = "loop_back"
    public static let trackingChanged = "tracking_changed"
    public static let coordinateFrameStarted = "coordinate_frame_started"
    public static let interruptionBegan = "interruption_began"
    public static let interruptionEnded = "interruption_ended"
    public static let cameraControl = "camera_control"
    public static let scaleValidation = "scale_validation"
    public static let brightnessJump = "brightness_jump"
    public static let warning = "warning"
}

public struct SurfaceAnnotation: Codable, Sendable, Equatable, Identifiable {
    public var id: String
    /// "mirror" | "glass" | "window" | "other"
    public var kind: String
    public var frameId: String
    /// (u, v) in [0,1] of the stored image (sensor landscape), origin top-left.
    public var imagePointNormalized: [Double]
    public var coordinateFrameId: Int
    public var tWorldFromCamera: [[Double]]
    /// Estimated 3D point, only when a raycast hit something; null otherwise.
    public var worldPoint: [Double]?
    /// "raycast_existing_plane_geometry" | "raycast_estimated_plane" | null
    public var worldPointMethod: String?
    public var roomId: String?
    public var note: String
    public var createdAt: String
    public var updatedAt: String
    public init(id: String, kind: String, frameId: String, imagePointNormalized: [Double], coordinateFrameId: Int,
                tWorldFromCamera: [[Double]], worldPoint: [Double]?, worldPointMethod: String?, roomId: String?,
                note: String, createdAt: String, updatedAt: String) {
        self.id = id; self.kind = kind; self.frameId = frameId; self.imagePointNormalized = imagePointNormalized
        self.coordinateFrameId = coordinateFrameId; self.tWorldFromCamera = tWorldFromCamera
        self.worldPoint = worldPoint; self.worldPointMethod = worldPointMethod; self.roomId = roomId; self.note = note
        self.createdAt = createdAt; self.updatedAt = updatedAt
    }
    enum CodingKeys: String, CodingKey {
        case id, kind, frameId = "frame_id", imagePointNormalized = "image_point_normalized",
             coordinateFrameId = "coordinate_frame_id", tWorldFromCamera = "T_world_from_camera",
             worldPoint = "world_point", worldPointMethod = "world_point_method", roomId = "room_id", note,
             createdAt = "created_at", updatedAt = "updated_at"
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id); try c.encode(kind, forKey: .kind); try c.encode(frameId, forKey: .frameId)
        try c.encode(imagePointNormalized, forKey: .imagePointNormalized)
        try c.encode(coordinateFrameId, forKey: .coordinateFrameId)
        try c.encode(tWorldFromCamera, forKey: .tWorldFromCamera)
        try c.encodeNullable(worldPoint, forKey: .worldPoint)
        try c.encodeNullable(worldPointMethod, forKey: .worldPointMethod)
        try c.encodeNullable(roomId, forKey: .roomId); try c.encode(note, forKey: .note)
        try c.encode(createdAt, forKey: .createdAt); try c.encode(updatedAt, forKey: .updatedAt)
    }
}

public struct AnnotationsDocument: Codable, Sendable {
    public var annotations: [SurfaceAnnotation]
    public init(annotations: [SurfaceAnnotation]) { self.annotations = annotations }
}

// MARK: - Integrity

public struct IntegrityDocument: Codable, Sendable {
    public var algorithm = "sha256"
    public var fileCount: Int
    public var totalBytes: Int64
    public var files: [IntegrityEntry]
    public init(files: [IntegrityEntry]) {
        self.files = files; fileCount = files.count; totalBytes = files.reduce(0) { $0 + $1.bytes }
    }
    enum CodingKeys: String, CodingKey { case algorithm, fileCount = "file_count", totalBytes = "total_bytes", files }
}

public struct IntegrityEntry: Codable, Sendable, Equatable {
    public var path: String
    public var bytes: Int64
    public var sha256: String
    public init(path: String, bytes: Int64, sha256: String) { self.path = path; self.bytes = bytes; self.sha256 = sha256 }
}

// MARK: - helpers

extension KeyedEncodingContainer {
    /// Encode `nil` as an explicit JSON null (Codable's default omits the key).
    mutating func encodeNullable<T: Encodable>(_ value: T?, forKey key: Key) throws {
        if let value { try encode(value, forKey: key) } else { try encodeNil(forKey: key) }
    }
}
