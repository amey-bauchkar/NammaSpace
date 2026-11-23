import Foundation

public struct ExportResult: Sendable {
    public var archive: URL
    public var report: ValidationReport
    public var manifest: Manifest
}

public enum ExportError: Error, CustomStringConvertible {
    case session(String)
    case validationFailed(ValidationReport)
    case io(String)
    public var description: String {
        switch self {
        case .session(let s): return s
        case .validationFailed(let r): return "The export did not pass validation:\n\(r)"
        case .io(let s): return "Export failed: \(s)"
        }
    }
}

/// Builds a .nammacap from a session directory. Memory use is bounded by one copy chunk plus the record files
/// (JSON lines are re-written one record at a time).
///
/// Finalization is atomic: everything is written to `<name>.nammacap.part`, the finished file is validated by
/// ArchiveValidator, and only then renamed to `<name>.nammacap`. A file with the final extension is therefore always a
/// complete, validated archive; a `.part` file is never reported as a finished export.
public enum Exporter {
    public static func export(sessionDirectory dir: URL, outputDirectory: URL? = nil,
                              progress: ((Double) -> Void)? = nil) throws -> ExportResult {
        let fm = FileManager.default
        let docData: Data
        do { docData = try Data(contentsOf: dir.appendingPathComponent("session.json")) } catch {
            throw ExportError.session("session.json is missing: \(error.localizedDescription)")
        }
        var doc = try NammaJSON.decoder().decode(SessionDocument.self, from: docData)
        let interrupted = doc.state != "finished"

        // ---- read records (tolerating a torn final line)
        let frames = try JSONLines.read(FrameRecord.self, from: dir.appendingPathComponent(NammaCapFormat.framesPath))
        let rejections = try JSONLines.read(RejectionRecord.self, from: dir.appendingPathComponent(NammaCapFormat.rejectionsPath))
        let trajectory = try JSONLines.read(TrajectorySample.self, from: dir.appendingPathComponent(NammaCapFormat.trajectoryPath))
        let events = try JSONLines.read(ScanEvent.self, from: dir.appendingPathComponent(NammaCapFormat.eventsPath))
        let annotations = (try? NammaJSON.decoder().decode(AnnotationsDocument.self,
            from: Data(contentsOf: dir.appendingPathComponent(NammaCapFormat.annotationsPath)))) ?? AnnotationsDocument(annotations: [])
        let coverageData = try? Data(contentsOf: dir.appendingPathComponent(NammaCapFormat.coveragePath))

        var notes: [String] = []
        let dropped = frames.droppedPartialLines + rejections.droppedPartialLines + trajectory.droppedPartialLines + events.droppedPartialLines
        let corrupt = frames.corruptLines + rejections.corruptLines + trajectory.corruptLines + events.corruptLines
        if dropped > 0 { notes.append("\(dropped) incomplete final record line(s) from an interrupted write were not exported") }
        if corrupt > 0 { notes.append("\(corrupt) unreadable record line(s) were not exported") }

        // ---- frames whose assets are complete
        var keptFrames: [FrameRecord] = []
        var lost: [String] = []
        var seenIds = Set<String>()
        for var f in frames.items {
            guard seenIds.insert(f.frameId).inserted else { notes.append("duplicate record for \(f.frameId) ignored"); continue }
            guard fm.fileExists(atPath: dir.appendingPathComponent(f.image.path).path) else { lost.append(f.frameId); continue }
            if let d = f.depth {
                let depthOK = fm.fileExists(atPath: dir.appendingPathComponent(d.path).path)
                let confOK = d.confidencePath.map { fm.fileExists(atPath: dir.appendingPathComponent($0).path) } ?? true
                if !depthOK || !confOK {
                    notes.append("\(f.frameId): depth files missing; frame kept without depth")
                    f.depth = nil
                    f.flags.append("depth_lost_before_export")
                }
            }
            keptFrames.append(f)
        }
        keptFrames.sort { $0.index < $1.index }
        if !lost.isEmpty { notes.append("\(lost.count) frame record(s) without an image file were not exported: \(lost.prefix(20).joined(separator: ", "))") }
        guard !keptFrames.isEmpty else { throw ExportError.session("This scan has no complete keyframes to export.") }

        // orphan assets (written but never recorded): reported, not exported
        let referenced = Set(keptFrames.flatMap { f -> [String] in
            [f.image.path] + (f.depth.map { [$0.path] + ($0.confidencePath.map { [$0] } ?? []) } ?? [])
        })
        var orphans = 0
        for sub in [NammaCapFormat.imagesDir, NammaCapFormat.depthDir] {
            for name in (try? fm.contentsOfDirectory(atPath: dir.appendingPathComponent(sub).path)) ?? []
            where !name.hasPrefix(".") && !referenced.contains("\(sub)/\(name)") { orphans += 1 }
        }
        if orphans > 0 { notes.append("\(orphans) asset file(s) without a frame record (interrupted write) were not exported") }

        // ---- manifest
        if doc.endedAt == nil { doc.endedAt = events.items.last?.wallclock ?? doc.startedAt }
        let usedFrames = Set(keptFrames.map(\.coordinateFrameId))
        var coordFrames = doc.coordinateFrames
        if coordFrames.isEmpty || !usedFrames.isSubset(of: Set(coordFrames.map(\.id))) {
            for id in usedFrames.subtracting(coordFrames.map(\.id)).sorted() {
                coordFrames.append(CoordinateFrame(id: id, startedAt: doc.startedAt, startedAtTimestampS: nil,
                                                   reason: "reconstructed_from_records"))
                notes.append("coordinate frame \(id) was not in session.json and was reconstructed from frame records")
            }
        }
        var rooms = doc.rooms
        for rid in Set(keptFrames.compactMap(\.roomId)).subtracting(rooms.map(\.id)).sorted() {
            rooms.append(Room(id: rid, label: rid, createdAt: doc.startedAt))
            notes.append("room \(rid) was not in session.json; exported with its id as label")
        }
        var counts = Counts()
        counts.framesAccepted = keptFrames.count
        counts.framesRejected = rejections.items.count
        counts.framesWithDepth = keptFrames.filter { $0.depth != nil }.count
        counts.framesHighResolution = keptFrames.filter { $0.image.source == "high_resolution_capture" }.count
        counts.trajectorySamples = trajectory.items.count
        counts.events = events.items.count
        counts.annotations = annotations.annotations.count

        let validations = doc.scaleValidations
        let scaleStatus: String
        if validations.isEmpty { scaleStatus = "device_tracked_unverified" }
        else if validations.allSatisfy({ abs($0.ratio - 1) <= 0.03 }) { scaleStatus = "user_validated" }
        else { scaleStatus = "user_validation_failed" }

        let depthAvailable = counts.framesWithDepth > 0
        var warnings = doc.warnings
        if interrupted { warnings.append("This scan was interrupted and finished by recovery; see manifest.recovery.") }
        if coordFrames.count > 1 { warnings.append("Tracking was reset \(coordFrames.count - 1) time(s): \(coordFrames.count) coordinate frames must be registered separately.") }
        let meshURL = dir.appendingPathComponent(NammaCapFormat.meshPath)
        let hasMesh = fm.fileExists(atPath: meshURL.path)
        var manifest = Manifest(
            createdBy: CreatedBy(appVersion: doc.appVersion, build: doc.appBuild),
            session: SessionInfo(id: doc.id, name: doc.name, startedAt: doc.startedAt, endedAt: doc.endedAt,
                                 status: interrupted ? "recovered" : "complete"),
            device: doc.device, capabilities: doc.capabilities,
            depth: DepthInfo(available: depthAvailable, source: depthAvailable ? doc.depthSource : nil,
                             unavailableReason: depthAvailable ? nil : (doc.depthUnavailableReason ?? "no frame carried depth")),
            captureSettings: doc.settings, scale: ScaleInfo(status: scaleStatus, validations: validations),
            coordinateFrames: coordFrames.sorted { $0.id < $1.id }, rooms: rooms, counts: counts, warnings: warnings,
            recovery: (interrupted || !notes.isEmpty || !lost.isEmpty)
                ? RecoveryInfo(interrupted: interrupted, droppedPartialLines: dropped, framesWithoutImage: lost, notes: notes)
                : nil)
        manifest.files.mesh = hasMesh ? NammaCapFormat.meshPath : nil

        // ---- write archive
        let outDir = outputDirectory ?? dir.appendingPathComponent("exports")
        try fm.createDirectory(at: outDir, withIntermediateDirectories: true)
        let base = Self.fileBase(doc.name, id: doc.id)
        let final = outDir.appendingPathComponent("\(base).\(NammaCapFormat.fileExtension)")
        let part = outDir.appendingPathComponent("\(base).\(NammaCapFormat.fileExtension).part")
        try? fm.removeItem(at: part)
        if let free = SessionStore.availableBytes(at: outDir) {
            let need = SessionStore.treeBytes(dir.appendingPathComponent("images")) + SessionStore.treeBytes(dir.appendingPathComponent("depth")) + 50_000_000
            if free < need { throw SessionError.lowStorage(available: free, required: need) }
        }

        var entries: [IntegrityEntry] = []
        do {
            let zw = try ZipWriter(url: part)
            func add(_ path: String, _ data: Data) throws {
                let (n, h) = try zw.addData(path: path, data: data)
                entries.append(IntegrityEntry(path: path, bytes: n, sha256: h))
            }
            func addFile(_ path: String) throws {
                let (n, h) = try zw.addFile(path: path, from: dir.appendingPathComponent(path))
                entries.append(IntegrityEntry(path: path, bytes: n, sha256: h))
            }
            try add(NammaCapFormat.manifestPath, try NammaJSON.encoder(pretty: true).encode(manifest))
            try add(NammaCapFormat.framesPath, try Self.jsonl(keptFrames))
            try add(NammaCapFormat.rejectionsPath, try Self.jsonl(rejections.items))
            try add(NammaCapFormat.trajectoryPath, try Self.jsonl(trajectory.items))
            try add(NammaCapFormat.eventsPath, try Self.jsonl(events.items))
            try add(NammaCapFormat.annotationsPath, try NammaJSON.encoder(pretty: true).encode(annotations))
            try add(NammaCapFormat.coveragePath, coverageData ?? Data("{\"available\":false}".utf8))
            if hasMesh { try addFile(NammaCapFormat.meshPath) }
            let total = Double(max(1, keptFrames.count))
            for (i, f) in keptFrames.enumerated() {
                try addFile(f.image.path)
                if let d = f.depth {
                    try addFile(d.path)
                    if let c = d.confidencePath { try addFile(c) }
                }
                progress?(Double(i + 1) / total * 0.9)
            }
            // integrity.json is written last; it covers every other member
            _ = try zw.addData(path: NammaCapFormat.integrityPath,
                               data: try NammaJSON.encoder(pretty: true).encode(IntegrityDocument(files: entries)))
            try zw.finish()
        } catch {
            try? fm.removeItem(at: part)
            throw ExportError.io("\(error)")
        }

        let report = ArchiveValidator.validate(part)
        progress?(1.0)
        guard report.ok else {
            try? fm.removeItem(at: part)
            throw ExportError.validationFailed(report)
        }
        if fm.fileExists(atPath: final.path) { try fm.removeItem(at: final) }
        try fm.moveItem(at: part, to: final)
        return ExportResult(archive: final, report: report, manifest: manifest)
    }

    static func jsonl<T: Encodable>(_ items: [T]) throws -> Data {
        var d = Data()
        for it in items { d.append(try NammaJSON.line(it)) }
        return d
    }

    public static func fileBase(_ name: String, id: String) -> String {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_"))
        let cleaned = String(name.unicodeScalars.map { allowed.contains($0) && $0.isASCII ? Character($0) : "-" })
            .split(separator: "-").joined(separator: "-")
        let stem = cleaned.isEmpty ? "scan" : String(cleaned.prefix(40))
        return "\(stem)-\(id.prefix(8))"
    }
}
