import Foundation

/// Independent check of a finished .nammacap file. Used by the app right after export (before it reports success) and
/// in tests. The Namma Space importer has its own implementation of the same rules (src/namma/capture/nammacap.py).
public struct ValidationReport: Sendable, CustomStringConvertible {
    public var ok: Bool { errors.isEmpty }
    public var errors: [String] = []
    public var warnings: [String] = []
    public var schemaVersion: String?
    public var frameCount = 0
    public var depthFrameCount = 0
    public var fileCount = 0
    public var totalBytes: Int64 = 0

    public var description: String {
        var s = ok ? "VALID" : "INVALID"
        s += " schema=\(schemaVersion ?? "?") frames=\(frameCount) depth_frames=\(depthFrameCount) files=\(fileCount) bytes=\(totalBytes)"
        for e in errors { s += "\n  error: \(e)" }
        for w in warnings { s += "\n  warning: \(w)" }
        return s
    }
}

public enum ArchiveValidator {
    /// Major version this reader understands.
    public static let supportedMajor = 1
    public static let supportedMinor = 0

    public static func validate(_ url: URL) -> ValidationReport {
        var r = ValidationReport()
        let zip: ZipReader
        do { zip = try ZipReader(url: url) } catch { r.errors.append("\(error)"); return r }

        var byPath: [String: ZipReader.Entry] = [:]
        for e in zip.entries {
            if e.path.hasSuffix("/") { continue }
            guard ArchivePath.isSafe(e.path) else { r.errors.append("unsafe member path \(e.path)"); continue }
            if byPath[e.path] != nil { r.errors.append("duplicate member \(e.path)") }
            byPath[e.path] = e
        }
        guard r.errors.isEmpty else { return r }

        // --- manifest + version gate (before trusting anything else)
        guard let me = byPath[NammaCapFormat.manifestPath], let md = try? zip.data(me) else {
            r.errors.append("manifest.json missing or unreadable"); return r
        }
        guard let raw = (try? JSONSerialization.jsonObject(with: md)) as? [String: Any] else {
            r.errors.append("manifest.json is not a JSON object"); return r
        }
        guard raw["format"] as? String == NammaCapFormat.formatName else {
            r.errors.append("manifest.format is not \"nammacap\""); return r
        }
        guard let vs = raw["schema_version"] as? String, let v = SchemaVersion(string: vs) else {
            r.errors.append("manifest.schema_version missing or malformed"); return r
        }
        r.schemaVersion = vs
        if v.major != supportedMajor {
            r.errors.append("incompatible schema \(vs): this reader supports \(supportedMajor).x only"); return r
        }
        if let mrs = raw["min_reader_version"] as? String, let mr = SchemaVersion(string: mrs),
           mr > SchemaVersion(major: supportedMajor, minor: supportedMinor) {
            r.errors.append("archive needs reader \(mrs) or newer"); return r
        }
        if v.minor > supportedMinor {
            r.warnings.append("schema \(vs) is newer than this reader (\(supportedMajor).\(supportedMinor)); unknown fields ignored")
        }
        let manifest: Manifest
        do { manifest = try NammaJSON.decoder().decode(Manifest.self, from: md) } catch {
            r.errors.append("manifest.json does not match schema: \(error)"); return r
        }

        // --- integrity: every member except integrity.json listed exactly once, with matching size and hash
        guard let ie = byPath[NammaCapFormat.integrityPath], let idata = try? zip.data(ie),
              let integrity = try? NammaJSON.decoder().decode(IntegrityDocument.self, from: idata) else {
            r.errors.append("integrity.json missing or unreadable (export not finalized)"); return r
        }
        guard integrity.algorithm == "sha256" else { r.errors.append("unsupported integrity algorithm"); return r }
        var listed = Set<String>()
        var total: Int64 = 0
        for f in integrity.files {
            guard ArchivePath.isSafe(f.path) else { r.errors.append("integrity lists unsafe path \(f.path)"); continue }
            guard listed.insert(f.path).inserted else { r.errors.append("integrity lists \(f.path) twice"); continue }
            guard let e = byPath[f.path] else { r.errors.append("missing file \(f.path)"); continue }
            do {
                let (n, h) = try zip.hashEntry(e)
                if n != f.bytes { r.errors.append("size mismatch \(f.path): \(n) != \(f.bytes)") }
                if h != f.sha256 { r.errors.append("sha256 mismatch \(f.path)") }
                total += n
            } catch { r.errors.append("\(f.path): \(error)") }
        }
        for p in byPath.keys where p != NammaCapFormat.integrityPath && !listed.contains(p) {
            r.errors.append("file not covered by integrity.json: \(p)")
        }
        if integrity.fileCount != integrity.files.count { r.errors.append("integrity.file_count does not match its list") }
        if integrity.totalBytes != total { r.errors.append("integrity.total_bytes \(integrity.totalBytes) != \(total)") }
        r.fileCount = integrity.files.count
        r.totalBytes = total
        guard r.errors.isEmpty else { return r }

        // --- records
        let frameIds = Set(manifest.coordinateFrames.map(\.id))
        let roomIds = Set(manifest.rooms.map(\.id))
        for name in [NammaCapFormat.framesPath, NammaCapFormat.rejectionsPath, NammaCapFormat.trajectoryPath,
                     NammaCapFormat.eventsPath, NammaCapFormat.annotationsPath, NammaCapFormat.coveragePath]
        where byPath[name] == nil {
            r.errors.append("missing \(name)")
        }
        guard r.errors.isEmpty, let fe = byPath[NammaCapFormat.framesPath], let fd = try? zip.data(fe) else { return r }
        var seen = Set<String>()
        var lastIndex = -1
        var depthFrames = 0
        let lines = fd.split(separator: 0x0A, omittingEmptySubsequences: true)
        for (i, line) in lines.enumerated() {
            guard let f = try? NammaJSON.decoder().decode(FrameRecord.self, from: Data(line)) else {
                r.errors.append("frames.jsonl line \(i + 1) does not parse"); continue
            }
            if !seen.insert(f.frameId).inserted { r.errors.append("duplicate frame_id \(f.frameId)") }
            if f.index <= lastIndex { r.errors.append("frame \(f.frameId): index not increasing") }
            lastIndex = f.index
            if !frameIds.contains(f.coordinateFrameId) { r.errors.append("frame \(f.frameId): unknown coordinate_frame_id") }
            if let rid = f.roomId, !roomIds.contains(rid) { r.errors.append("frame \(f.frameId): unknown room_id \(rid)") }
            if byPath[f.image.path] == nil { r.errors.append("frame \(f.frameId): image \(f.image.path) missing") }
            if !Self.validPose(f.camera.tWorldFromCamera) { r.errors.append("frame \(f.frameId): invalid pose matrix") }
            let k = f.camera.intrinsics
            if !(k.fx > 0 && k.fy > 0 && k.width == f.image.width && k.height == f.image.height) {
                r.errors.append("frame \(f.frameId): intrinsics do not describe the stored image")
            }
            if let d = f.depth {
                depthFrames += 1
                if !Self.validPose(d.tWorldFromCamera) { r.errors.append("frame \(f.frameId): invalid depth pose matrix") }
                if let e = byPath[d.path] {
                    if e.size != UInt64(d.width * d.height * 4) { r.errors.append("frame \(f.frameId): depth size mismatch") }
                } else { r.errors.append("frame \(f.frameId): depth \(d.path) missing") }
                if let cp = d.confidencePath {
                    if let e = byPath[cp] {
                        if e.size != UInt64(d.width * d.height) { r.errors.append("frame \(f.frameId): confidence size mismatch") }
                    } else { r.errors.append("frame \(f.frameId): confidence \(cp) missing") }
                }
            }
        }
        r.frameCount = seen.count
        r.depthFrameCount = depthFrames
        if manifest.counts.framesAccepted != seen.count {
            r.errors.append("manifest counts \(manifest.counts.framesAccepted) frames, archive has \(seen.count)")
        }
        if manifest.counts.framesWithDepth != depthFrames {
            r.errors.append("manifest counts \(manifest.counts.framesWithDepth) depth frames, archive has \(depthFrames)")
        }
        if manifest.depth.available != (depthFrames > 0) {
            r.errors.append("manifest.depth.available disagrees with the frames")
        }
        if seen.isEmpty { r.errors.append("archive has no frames") }
        if let mp = manifest.files.mesh {
            if let e = byPath[mp], let head = try? zip.data(e, limit: UInt64.max).prefix(4) {
                if Array(head) != Array("glTF".utf8) { r.errors.append("\(mp) is not a glTF binary") }
            } else { r.errors.append("manifest names mesh \(mp) but it is missing") }
        }
        if let rec = manifest.recovery, !rec.framesWithoutImage.isEmpty {
            r.warnings.append("\(rec.framesWithoutImage.count) frame(s) were lost before export and are listed in manifest.recovery")
        }
        r.warnings.append(contentsOf: manifest.warnings)
        return r
    }

    static func validPose(_ m: [[Double]]) -> Bool {
        guard m.count == 4, m.allSatisfy({ $0.count == 4 && $0.allSatisfy(\.isFinite) }) else { return false }
        let last = m[3]
        guard abs(last[0]) < 1e-6, abs(last[1]) < 1e-6, abs(last[2]) < 1e-6, abs(last[3] - 1) < 1e-6 else { return false }
        // rotation part should be orthonormal (ARKit transforms are rigid)
        for i in 0..<3 {
            let n = m[0][i] * m[0][i] + m[1][i] * m[1][i] + m[2][i] * m[2][i]
            if abs(n - 1) > 1e-3 { return false }
        }
        return true
    }
}
