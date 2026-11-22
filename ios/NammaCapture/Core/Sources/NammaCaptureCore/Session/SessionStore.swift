import Foundation

/// On-device layout of one scan while it is being captured (never the export format itself):
///
///   <root>/<session-id>/session.json      SessionDocument (rewritten atomically when it changes)
///   <root>/<session-id>/frames.jsonl      one FrameRecord per accepted keyframe (append-only)
///   <root>/<session-id>/rejections.jsonl  RejectionRecord (append-only)
///   <root>/<session-id>/trajectory.jsonl  TrajectorySample (append-only)
///   <root>/<session-id>/events.jsonl      ScanEvent (append-only)
///   <root>/<session-id>/annotations.json  AnnotationsDocument (rewritten atomically; editable)
///   <root>/<session-id>/coverage.json     latest coverage summary (rewritten atomically)
///   <root>/<session-id>/images/, depth/   assets, each written to a temporary name and renamed when complete
///   <root>/<session-id>/exports/          finished archives (<name>.nammacap) and in-progress ones (.part)
///
/// Write order for a keyframe: assets first, then its record line. A record therefore never points at a file that was
/// not completely written; a crash can leave orphan assets (reported on recovery) but never a dangling record.
public struct SessionDocument: Codable, Sendable {
    public var id: String
    public var name: String
    /// "capturing" | "finished"
    public var state: String
    public var startedAt: String
    public var endedAt: String?
    public var device: DeviceInfo
    public var capabilities: Capabilities
    public var settings: CaptureSettings
    public var depthSource: String?
    public var depthUnavailableReason: String?
    public var rooms: [Room]
    public var coordinateFrames: [CoordinateFrame]
    public var scaleValidations: [ScaleValidation]
    public var warnings: [String]
    public var appVersion: String
    public var appBuild: String

    public init(id: String, name: String, startedAt: String, device: DeviceInfo, capabilities: Capabilities,
                settings: CaptureSettings, depthSource: String?, depthUnavailableReason: String?, appVersion: String,
                appBuild: String) {
        self.id = id; self.name = name; self.state = "capturing"; self.startedAt = startedAt; self.endedAt = nil
        self.device = device; self.capabilities = capabilities; self.settings = settings; self.depthSource = depthSource
        self.depthUnavailableReason = depthUnavailableReason; self.rooms = []; self.coordinateFrames = []
        self.scaleValidations = []; self.warnings = []; self.appVersion = appVersion; self.appBuild = appBuild
    }
}

public enum SessionError: Error, CustomStringConvertible {
    case io(String)
    case notFound(String)
    case invalid(String)
    case lowStorage(available: Int64, required: Int64)
    public var description: String {
        switch self {
        case .io(let s): return "Storage error: \(s)"
        case .notFound(let s): return "Not found: \(s)"
        case .invalid(let s): return "Invalid session: \(s)"
        case .lowStorage(let a, let r):
            return "Not enough free storage (\(a / 1_000_000) MB free, \(r / 1_000_000) MB needed)."
        }
    }
}

public struct SessionSummary: Identifiable, Sendable {
    public var id: String
    public var name: String
    public var state: String
    public var startedAt: String
    public var endedAt: String?
    public var acceptedFrames: Int
    public var bytesOnDisk: Int64
    public var exports: [URL]
    public var directory: URL
    /// capturing state found at launch: the app was closed or crashed mid-scan.
    public var interrupted: Bool
}

public final class SessionStore: @unchecked Sendable {
    public let root: URL
    private let fm = FileManager.default

    public init(root: URL) throws {
        self.root = root
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func directory(for id: String) -> URL { root.appendingPathComponent(id, isDirectory: true) }

    public func create(_ doc: SessionDocument) throws -> URL {
        let d = directory(for: doc.id)
        guard !fm.fileExists(atPath: d.path) else { throw SessionError.invalid("session \(doc.id) already exists") }
        for sub in ["images", "depth", "exports"] {
            try fm.createDirectory(at: d.appendingPathComponent(sub), withIntermediateDirectories: true)
        }
        try Self.writeAtomically(try NammaJSON.encoder(pretty: true).encode(doc), to: d.appendingPathComponent("session.json"))
        try Self.writeAtomically(try NammaJSON.encoder(pretty: true).encode(AnnotationsDocument(annotations: [])),
                                 to: d.appendingPathComponent(NammaCapFormat.annotationsPath))
        return d
    }

    public func load(_ id: String) throws -> SessionDocument {
        let u = directory(for: id).appendingPathComponent("session.json")
        guard let d = try? Data(contentsOf: u) else { throw SessionError.notFound(id) }
        return try NammaJSON.decoder().decode(SessionDocument.self, from: d)
    }

    public func save(_ doc: SessionDocument) throws {
        try Self.writeAtomically(try NammaJSON.encoder(pretty: true).encode(doc),
                                 to: directory(for: doc.id).appendingPathComponent("session.json"))
    }

    public func list() -> [SessionSummary] {
        guard let ids = try? fm.contentsOfDirectory(atPath: root.path) else { return [] }
        var out: [SessionSummary] = []
        for id in ids where !id.hasPrefix(".") {
            guard let doc = try? load(id) else { continue }
            let dir = directory(for: id)
            let frames = JSONLines.countCompleteLines(dir.appendingPathComponent(NammaCapFormat.framesPath))
            let exportsDir = dir.appendingPathComponent("exports")
            let exports = ((try? fm.contentsOfDirectory(at: exportsDir, includingPropertiesForKeys: nil)) ?? [])
                .filter { $0.pathExtension == NammaCapFormat.fileExtension }
                .sorted { $0.lastPathComponent < $1.lastPathComponent }
            out.append(SessionSummary(id: id, name: doc.name, state: doc.state, startedAt: doc.startedAt,
                                      endedAt: doc.endedAt, acceptedFrames: frames, bytesOnDisk: Self.treeBytes(dir),
                                      exports: exports, directory: dir, interrupted: doc.state == "capturing"))
        }
        return out.sorted { $0.startedAt > $1.startedAt }
    }

    /// Moves a session to <root>/.trash (never a hard delete from the app's normal UI).
    public func trash(_ id: String) throws {
        let trash = root.appendingPathComponent(".trash", isDirectory: true)
        try fm.createDirectory(at: trash, withIntermediateDirectories: true)
        let dst = trash.appendingPathComponent("\(id)-\(Int(Date().timeIntervalSince1970))")
        try fm.moveItem(at: directory(for: id), to: dst)
    }

    // MARK: helpers

    public static func writeAtomically(_ data: Data, to url: URL) throws {
        let tmp = url.deletingLastPathComponent().appendingPathComponent(".\(url.lastPathComponent).\(UUID().uuidString).tmp")
        do {
            try data.write(to: tmp, options: [.atomic])
            if FileManager.default.fileExists(atPath: url.path) {
                _ = try FileManager.default.replaceItemAt(url, withItemAt: tmp)
            } else {
                try FileManager.default.moveItem(at: tmp, to: url)
            }
        } catch {
            try? FileManager.default.removeItem(at: tmp)
            throw SessionError.io("\(url.lastPathComponent): \(error.localizedDescription)")
        }
    }

    public static func treeBytes(_ url: URL) -> Int64 {
        guard let e = FileManager.default.enumerator(at: url, includingPropertiesForKeys: [.fileSizeKey, .isRegularFileKey])
        else { return 0 }
        var total: Int64 = 0
        for case let f as URL in e {
            if let v = try? f.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey]), v.isRegularFile == true {
                total += Int64(v.fileSize ?? 0)
            }
        }
        return total
    }

    /// Free space the system would let the app use for important data (falls back to plain free space).
    public static func availableBytes(at url: URL) -> Int64? {
        if let v = try? url.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]),
           let n = v.volumeAvailableCapacityForImportantUsage { return n }
        if let a = try? FileManager.default.attributesOfFileSystem(forPath: url.path),
           let n = a[.systemFreeSize] as? NSNumber { return n.int64Value }
        return nil
    }
}

/// Reading append-only JSON Lines files written by SessionWriter, tolerating a torn final line from a crash.
public enum JSONLines {
    public struct ReadResult<T> {
        public var items: [T]
        /// lines that did not parse (a crash can tear only the last line; anything else is reported as corrupt)
        public var droppedPartialLines: Int
        public var corruptLines: Int
    }

    public static func read<T: Decodable>(_ type: T.Type, from url: URL) throws -> ReadResult<T> {
        guard FileManager.default.fileExists(atPath: url.path) else { return ReadResult(items: [], droppedPartialLines: 0, corruptLines: 0) }
        let data = try Data(contentsOf: url, options: [.mappedIfSafe])
        var items: [T] = []
        var dropped = 0, corrupt = 0
        let dec = NammaJSON.decoder()
        let lines = data.split(separator: 0x0A, omittingEmptySubsequences: false)
        for (i, line) in lines.enumerated() {
            if line.isEmpty { continue }
            let isLast = i == lines.count - 1          // last element has no trailing newline => torn write
            do {
                if isLast { dropped += 1; continue }
                items.append(try dec.decode(T.self, from: Data(line)))
            } catch {
                corrupt += 1
            }
        }
        return ReadResult(items: items, droppedPartialLines: dropped, corruptLines: corrupt)
    }

    public static func countCompleteLines(_ url: URL) -> Int {
        guard let d = try? Data(contentsOf: url, options: [.mappedIfSafe]) else { return 0 }
        return d.reduce(0) { $0 + ($1 == 0x0A ? 1 : 0) }
    }
}

/// Serialises every write for one session on its own actor, off the camera callback.
public actor SessionWriter {
    public let directory: URL
    private var handles: [String: FileHandle] = [:]
    private var pending = 0
    private var trajectorySinceSync = 0

    public init(directory: URL) { self.directory = directory }

    /// Number of keyframe writes queued but not finished (used for back-pressure by the capture pipeline).
    public var pendingWrites: Int { pending }

    public func beginWrite() { pending += 1 }

    private func handle(_ name: String) throws -> FileHandle {
        if let h = handles[name] { return h }
        let u = directory.appendingPathComponent(name)
        if !FileManager.default.fileExists(atPath: u.path) {
            guard FileManager.default.createFile(atPath: u.path, contents: nil) else { throw SessionError.io("cannot create \(name)") }
        }
        let h = try FileHandle(forWritingTo: u)
        // A previous run may have torn the last line: start on a fresh line so the next record stays parseable.
        let end = try h.seekToEnd()
        if end > 0 {
            let r = try FileHandle(forReadingFrom: u)
            try r.seek(toOffset: end - 1)
            let last = try r.read(upToCount: 1)
            try r.close()
            if last != Data([0x0A]) { try h.write(contentsOf: Data([0x0A])) }
        }
        handles[name] = h
        return h
    }

    private func append<T: Encodable>(_ value: T, to name: String, sync: Bool) throws {
        let h = try handle(name)
        try h.write(contentsOf: try NammaJSON.line(value))
        if sync { try h.synchronize() }
    }

    private func writeAsset(_ data: Data, relativePath: String) throws {
        guard ArchivePath.isSafe(relativePath) else { throw SessionError.invalid("bad asset path \(relativePath)") }
        let dst = directory.appendingPathComponent(relativePath)
        let tmp = dst.deletingLastPathComponent().appendingPathComponent(".\(dst.lastPathComponent).part")
        try FileManager.default.createDirectory(at: dst.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: tmp, options: [.atomic])
        if FileManager.default.fileExists(atPath: dst.path) { try FileManager.default.removeItem(at: dst) }
        try FileManager.default.moveItem(at: tmp, to: dst)
    }

    /// Write a keyframe: assets first, then the record. Call `beginWrite()` before enqueuing.
    public func writeKeyframe(_ record: FrameRecord, image: Data, depth: Data?, confidence: Data?) throws {
        defer { pending -= 1 }
        try writeAsset(image, relativePath: record.image.path)
        if let dref = record.depth, let depth {
            try writeAsset(depth, relativePath: dref.path)
            if let cp = dref.confidencePath, let confidence { try writeAsset(confidence, relativePath: cp) }
        }
        try append(record, to: NammaCapFormat.framesPath, sync: true)
    }

    public func cancelWrite() { pending -= 1 }

    public func writeRejection(_ r: RejectionRecord) throws { try append(r, to: NammaCapFormat.rejectionsPath, sync: false) }

    public func writeTrajectory(_ s: TrajectorySample) throws {
        trajectorySinceSync += 1
        let sync = trajectorySinceSync >= 25
        if sync { trajectorySinceSync = 0 }
        try append(s, to: NammaCapFormat.trajectoryPath, sync: sync)
    }

    public func writeEvent(_ e: ScanEvent) throws { try append(e, to: NammaCapFormat.eventsPath, sync: true) }

    public func writeAnnotations(_ doc: AnnotationsDocument) throws {
        try SessionStore.writeAtomically(try NammaJSON.encoder(pretty: true).encode(doc),
                                         to: directory.appendingPathComponent(NammaCapFormat.annotationsPath))
    }

    public func writeCoverage(_ summary: ScanCoverage) throws {
        try SessionStore.writeAtomically(try NammaJSON.encoder(pretty: true).encode(summary),
                                         to: directory.appendingPathComponent(NammaCapFormat.coveragePath))
    }

    public func close() {
        for h in handles.values { try? h.synchronize(); try? h.close() }
        handles.removeAll()
    }
}
