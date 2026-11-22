import Foundation
import Testing
@testable import NammaCaptureCore

@Suite struct ExportTests {
    @Test func endToEndExportValidates() async throws {
        let s = try await Fixtures.session(frames: 8, depth: true)
        let r = try Exporter.export(sessionDirectory: s.dir)
        #expect(r.report.ok, "\(r.report)")
        #expect(r.report.frameCount == 8)
        #expect(r.report.depthFrameCount == 8)
        #expect(r.archive.pathExtension == "nammacap")
        #expect(!FileManager.default.fileExists(atPath: r.archive.path + ".part"))
        #expect(r.manifest.session.status == "complete")
        #expect(r.manifest.scale.status == "device_tracked_unverified")
        #expect(r.manifest.depth.available && r.manifest.depth.source == "arkit_scene_depth")
        #expect(r.manifest.recovery == nil)
        // revalidate from scratch
        #expect(ArchiveValidator.validate(r.archive).ok)

        // Optional hand-off for the cross-language check (Python importer validates the Swift-written archive).
        if let out = ProcessInfo.processInfo.environment["NAMMACAP_FIXTURE_OUT"] {
            let dst = URL(fileURLWithPath: out)
            try? FileManager.default.removeItem(at: dst)
            try FileManager.default.copyItem(at: r.archive, to: dst)
        }
    }

    @Test func nonLidarExportMarksDepthUnavailable() async throws {
        let s = try await Fixtures.session(frames: 4, depth: false)
        let r = try Exporter.export(sessionDirectory: s.dir)
        #expect(r.report.ok, "\(r.report)")
        #expect(r.manifest.depth.available == false)
        #expect(r.manifest.depth.source == nil)
        #expect(r.manifest.depth.unavailableReason?.contains("LiDAR") == true)
        #expect(r.manifest.counts.framesWithDepth == 0)
        let json = String(decoding: try NammaJSON.encoder().encode(r.manifest), as: UTF8.self)
        #expect(json.contains("\"source\":null"))     // explicit null, not an omitted key
    }

    @Test func manifestIsStrictJSON() async throws {
        let s = try await Fixtures.session(frames: 3, depth: false)
        let r = try Exporter.export(sessionDirectory: s.dir)
        let z = try ZipReader(url: r.archive)
        let m = try z.data(z.entries.first { $0.path == "manifest.json" }!)
        let text = String(decoding: m, as: UTF8.self)
        #expect(!text.contains("NaN") && !text.contains("Infinity"))
        #expect(z.entries.last?.path == "integrity.json")
    }

    @Test func interruptedSessionIsRecoveredAndReported() async throws {
        let s = try await Fixtures.session(frames: 5, depth: true, finish: false)
        // simulate a crash: a torn final line, a record whose image vanished, an orphan asset
        let frames = s.dir.appendingPathComponent("frames.jsonl")
        let h = try FileHandle(forWritingTo: frames); try h.seekToEnd()
        try h.write(contentsOf: Data("{\"frame_id\":\"f0000".utf8)); try h.close()
        try FileManager.default.removeItem(at: s.dir.appendingPathComponent("images/f000003.jpg"))
        try Data([1, 2, 3]).write(to: s.dir.appendingPathComponent("images/f000099.jpg"))

        let r = try Exporter.export(sessionDirectory: s.dir)
        #expect(r.report.ok, "\(r.report)")
        #expect(r.manifest.session.status == "recovered")
        let rec = try #require(r.manifest.recovery)
        #expect(rec.interrupted)
        #expect(rec.droppedPartialLines == 1)
        #expect(rec.framesWithoutImage == ["f000003"])
        #expect(rec.notes.contains { $0.contains("without a frame record") })
        #expect(r.report.frameCount == 4)
        #expect(r.report.warnings.contains { $0.contains("lost before export") })
    }

    @Test func writerResumesOnFreshLineAfterTornWrite() async throws {
        let s = try await Fixtures.session(frames: 2, depth: false, finish: false)
        let events = s.dir.appendingPathComponent("events.jsonl")
        let h = try FileHandle(forWritingTo: events); try h.seekToEnd()
        try h.write(contentsOf: Data("{\"seq\":9".utf8)); try h.close()
        let w = SessionWriter(directory: s.dir)
        try await w.writeEvent(ScanEvent(seq: 3, type: EventType.loopBack, timestampS: 9, wallclock: ISOTime.string(),
                                         coordinateFrameId: 0, roomId: "room_1", position: [0, 0, 0], frameId: nil))
        await w.close()
        let read = try JSONLines.read(ScanEvent.self, from: events)
        #expect(read.items.map(\.seq) == [1, 2, 3])
        #expect(read.corruptLines == 1)       // the torn line, now followed by a newline, is reported, not hidden
    }

    @Test func emptySessionRefusesToExport() async throws {
        let s = try await Fixtures.session(frames: 0, depth: false)
        #expect(throws: ExportError.self) { try Exporter.export(sessionDirectory: s.dir) }
    }
}

@Suite struct ValidatorRejectionTests {
    func exported() async throws -> URL {
        try await Exporter.export(sessionDirectory: Fixtures.session(frames: 4, depth: true).dir).archive
    }

    @Test func truncatedArchiveIsRejected() async throws {
        let u = try await exported()
        let d = try Data(contentsOf: u)
        try d.prefix(d.count - 300).write(to: u)
        let r = ArchiveValidator.validate(u)
        #expect(!r.ok)
    }

    @Test func flippedByteIsRejected() async throws {
        let u = try await exported()
        var d = try Data(contentsOf: u)
        // corrupt a byte inside the first image's data (search for its JPEG marker after the manifest)
        let marker = Data([0xFF, 0xD8, 0xFF, 0xE0])
        let at = try #require(d.range(of: marker)).lowerBound + 100
        d[at] ^= 0xFF
        try d.write(to: u)
        let r = ArchiveValidator.validate(u)
        #expect(!r.ok)
        #expect(r.errors.contains { $0.contains("CRC") || $0.contains("sha256") })
    }

    @Test func futureMajorVersionIsRejectedClearly() async throws {
        let dir = Fixtures.tempDir("future")
        let u = dir.appendingPathComponent("x.nammacap")
        let zw = try ZipWriter(url: u)
        try zw.addData(path: "manifest.json", data: Data(#"{"format":"nammacap","schema_version":"2.0","min_reader_version":"2.0"}"#.utf8))
        try zw.finish()
        let r = ArchiveValidator.validate(u)
        #expect(!r.ok)
        #expect(r.errors.first?.contains("incompatible schema 2.0") == true)
    }

    @Test func unlistedOrUnsafeMembersAreRejected() async throws {
        #expect(!ArchivePath.isSafe("../etc/passwd"))
        #expect(!ArchivePath.isSafe("/abs"))
        #expect(!ArchivePath.isSafe("a//b"))
        #expect(!ArchivePath.isSafe("a\\b"))
        #expect(!ArchivePath.isSafe("C:x"))
        #expect(ArchivePath.isSafe("images/f000001.jpg"))
        let dir = Fixtures.tempDir("unsafe")
        let u = dir.appendingPathComponent("x.nammacap")
        let zw = try ZipWriter(url: u)
        #expect(throws: ZipError.self) { try zw.addData(path: "../x", data: Data()) }
    }

    @Test func notAZipIsRejected() throws {
        let u = Fixtures.tempDir("nz").appendingPathComponent("x.nammacap")
        try Data("hello".utf8).write(to: u)
        #expect(!ArchiveValidator.validate(u).ok)
    }
}
