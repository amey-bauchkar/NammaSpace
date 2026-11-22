import NammaCaptureCore
import SwiftUI

/// Everything known about a scan on the phone, read back from its files (not from memory), plus annotation editing
/// and export / transfer.
struct ReviewView: View {
    @Environment(AppModel.self) private var model
    let sessionId: String
    let onContinue: () -> Void

    @State private var doc: SessionDocument?
    @State private var frames: [FrameRecord] = []
    @State private var rejections: [RejectionRecord] = []
    @State private var events: [ScanEvent] = []
    @State private var annotations: [SurfaceAnnotation] = []
    @State private var coverage: ScanCoverage?
    @State private var loadError: String?
    @State private var exporting = false
    @State private var exportProgress = 0.0
    @State private var exportResult: ExportResult?
    @State private var exportError: String?

    var dir: URL { model.store.directory(for: sessionId) }

    var body: some View {
        List {
            if let loadError { Text(loadError).foregroundStyle(.red) }
            if let doc {
                if doc.state != "finished" {
                    Section {
                        Label("This scan was interrupted before it was finished.", systemImage: "exclamationmark.circle").foregroundStyle(.orange)
                        Button("Continue scanning", action: onContinue)
                        Text("Continuing starts a new tracking segment: ARKit cannot restore the earlier one after the app closed. Exporting now keeps everything saved so far and marks the scan as recovered.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
                if !frames.isEmpty {
                    Section {
                        NavigationLink { PhotoGridView(directory: dir, frames: frames) } label: {
                            Label("Browse \(frames.count) photos", systemImage: "photo.on.rectangle")
                        }
                        if FileManager.default.fileExists(atPath: dir.appendingPathComponent(NammaCapFormat.meshPath).path) {
                            Label("LiDAR surface mesh saved with this scan", systemImage: "square.3.layers.3d").foregroundStyle(.secondary)
                        }
                    }
                }
                Section("Summary") {
                    row("Photos", "\(frames.count)")
                    row("High-resolution stills", "\(frames.filter { $0.image.source == "high_resolution_capture" }.count)")
                    row("With LiDAR depth", doc.capabilities.sceneDepth ? "\(frames.filter { $0.depth != nil }.count)" : "not available on this device")
                    row("Skipped candidates", "\(rejections.count)")
                    row("Rooms", doc.rooms.map(\.label).joined(separator: ", "))
                    row("Doorways marked", "\(events.filter { $0.type == EventType.doorway }.count)")
                    row("Doorway look-backs", "\(events.filter { $0.type == EventType.doorwayLookBackCaptured }.count)")
                    row("Loop-backs", "\(events.filter { $0.type == EventType.loopBack }.count)")
                    row("Tracking segments", "\(doc.coordinateFrames.count)")
                    row("Scale", doc.scaleValidations.isEmpty ? "ARKit metric, not checked" :
                            doc.scaleValidations.map { String(format: "%.1f%% off tape", ($0.ratio - 1) * 100) }.joined(separator: ", "))
                    row("Exposure lock", exposureSummary(doc))
                }
                if !warnings(doc).isEmpty {
                    Section("Warnings") {
                        ForEach(warnings(doc), id: \.self) { Text($0).font(.footnote) }
                    }
                }
                if let coverage, !coverage.rooms.isEmpty {
                    Section("Coverage (capture-time estimate)") {
                        ForEach(Array(coverage.rooms.enumerated()), id: \.offset) { _, c in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(doc.rooms.first { $0.id == c.roomId }?.label ?? c.roomId ?? "Room").font(.subheadline.weight(.semibold))
                                Text("Floor \(pct(c.floorCoveredFractionEstimate)) · Ceiling \(pct(c.ceilingCoveredFractionEstimate)) · Directions \(c.directionsCovered.filter { $0 }.count)/\(c.directionsCovered.count)")
                                    .font(.caption)
                                Text(c.measured ? "Based on LiDAR depth samples." : (c.method == "arkit_plane_estimates" ? "Based on ARKit's detected planes: a rough estimate, not measured geometry." : "Viewing directions only."))
                                    .font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                Section("Mirrors, glass, windows") {
                    if annotations.isEmpty { Text("None marked.").foregroundStyle(.secondary) }
                    ForEach($annotations) { $a in
                        HStack {
                            Picker(a.frameId, selection: $a.kind) {
                                ForEach(["mirror", "glass", "window", "other"], id: \.self) { Text($0.capitalized).tag($0) }
                            }
                            Text(a.worldPoint == nil ? "image only" : "with 3D point").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .onDelete { annotations.remove(atOffsets: $0); saveAnnotations() }
                }
                .onChange(of: annotations) { _, _ in saveAnnotations() }

                Section("Export") {
                    if exporting { ProgressView(value: exportProgress) { Text("Packaging and verifying…") } }
                    Button { export() } label: { Label("Finish and export", systemImage: "shippingbox") }
                        .disabled(exporting || frames.isEmpty)
                    if let exportError { Text(exportError).foregroundStyle(.red).font(.footnote) }
                }
                if let r = exportResult {
                    ExportPanel(result: r, title: doc.name)
                }
            }
        }
        .navigationTitle(doc?.name ?? "Scan")
        .onAppear(perform: load)
    }

    private func row(_ k: String, _ v: String) -> some View {
        HStack(alignment: .top) { Text(k); Spacer(); Text(v).foregroundStyle(.secondary).multilineTextAlignment(.trailing) }
    }

    private func pct(_ v: Double?) -> String { v.map { "~\(Int($0 * 100))%" } ?? "unknown" }

    private func warnings(_ d: SessionDocument) -> [String] {
        var w = d.warnings
        let lostHigh = frames.filter { $0.flags.contains { $0.hasPrefix("high_resolution_capture_failed") } }.count
        if lostHigh > 0 { w.append("\(lostHigh) photo(s) fell back to video-stream resolution because the high-resolution capture failed.") }
        let relaxed = frames.filter { $0.flags.contains("relaxed_gate") }.count
        if relaxed > 0 { w.append("\(relaxed) photo(s) were kept with relaxed quality limits to avoid gaps (dim or low-detail areas).") }
        if d.coordinateFrames.count > 1 { w.append("Tracking was reset; the scan has \(d.coordinateFrames.count) separate tracking segments.") }
        return w
    }

    private func exposureSummary(_ d: SessionDocument) -> String {
        guard let last = d.settings.cameraControls.last(where: { $0.control == "exposure_lock" }) else { return "not requested (auto)" }
        return last.outcome
    }

    private func load() {
        do {
            doc = try model.store.load(sessionId)
            frames = try JSONLines.read(FrameRecord.self, from: dir.appendingPathComponent(NammaCapFormat.framesPath)).items
            rejections = try JSONLines.read(RejectionRecord.self, from: dir.appendingPathComponent(NammaCapFormat.rejectionsPath)).items
            events = try JSONLines.read(ScanEvent.self, from: dir.appendingPathComponent(NammaCapFormat.eventsPath)).items
            annotations = (try? NammaJSON.decoder().decode(AnnotationsDocument.self,
                            from: Data(contentsOf: dir.appendingPathComponent(NammaCapFormat.annotationsPath))).annotations) ?? []
            coverage = try? NammaJSON.decoder().decode(ScanCoverage.self, from: Data(contentsOf: dir.appendingPathComponent(NammaCapFormat.coveragePath)))
        } catch {
            loadError = "Could not read this scan: \(error)"
        }
    }

    private func saveAnnotations() {
        let now = ISOTime.string()
        let list = annotations.map { a -> SurfaceAnnotation in var b = a; b.updatedAt = now; return b }
        try? SessionStore.writeAtomically(try NammaJSON.encoder(pretty: true).encode(AnnotationsDocument(annotations: list)),
                                          to: dir.appendingPathComponent(NammaCapFormat.annotationsPath))
    }

    private func export() {
        exporting = true; exportError = nil; exportResult = nil
        let d = dir
        Task.detached(priority: .userInitiated) {
            do {
                let r = try Exporter.export(sessionDirectory: d) { p in Task { @MainActor in exportProgress = p } }
                await MainActor.run { exportResult = r; exporting = false; model.refresh() }
            } catch {
                await MainActor.run { exportError = "\(error)"; exporting = false }
            }
        }
    }
}

struct ExportPanel: View {
    let result: ExportResult
    let title: String
    @State private var link = ""
    @State private var code = ""
    @State private var sending = false
    @State private var progress = 0.0
    @State private var status = ""
    @State private var sendError: String?
    @State private var sent: StudioClient.UploadResult?

    var size: String {
        let n: Int = (try? result.archive.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
        return ByteCountFormatter.string(fromByteCount: Int64(n), countStyle: .file)
    }

    var body: some View {
        Section("Exported file") {
            Label(result.report.ok ? "Verified: every file present with matching SHA-256" : "Verification failed",
                  systemImage: result.report.ok ? "checkmark.seal.fill" : "xmark.octagon.fill")
                .foregroundStyle(result.report.ok ? .green : .red)
            Text("\(result.archive.lastPathComponent) · \(size) · \(result.report.frameCount) photos · \(result.report.depthFrameCount) depth maps")
                .font(.caption)
            ForEach(result.report.warnings, id: \.self) { Text($0).font(.caption).foregroundStyle(.orange) }
            ShareLink(item: result.archive) { Label("Save to Files / AirDrop", systemImage: "square.and.arrow.up") }
            Text("The file also stays in Files > On My iPhone > Namma Capture > Scans.").font(.caption2).foregroundStyle(.secondary)
        }
        Section("Send to Namma Space Studio") {
            TextField("Studio link (https://192.168.x.x:8443/#c=…)", text: $link)
                .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
            TextField("Pairing code (if not in the link)", text: $code).keyboardType(.numberPad)
            if sending { ProgressView(value: progress) { Text(status).font(.caption) } }
            Button { send() } label: { Label("Send", systemImage: "paperplane") }.disabled(sending || link.isEmpty)
            if let sendError {
                Text(sendError).foregroundStyle(.red).font(.footnote)
                if sendError.contains("different certificate"), let host = URL(string: link.contains("://") ? link : "https://\(link)")?.host {
                    Button("Forget certificate for \(host)") { StudioClient.forgetCertificate(host: host); self.sendError = nil }
                }
            }
            if let sent {
                Label("Imported into Studio project \(sent.projectId) (\(sent.framesImported) photos).", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                Text("Start a reconstruction from the Studio on your computer. A successful upload does not guarantee the reconstruction will succeed.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text("Your phone and computer must be on the same Wi-Fi or hotspot. In the Studio, choose “Capture with your phone” to see the link and code.")
                .font(.caption2).foregroundStyle(.secondary)
        }
    }

    private func send() {
        sendError = nil; sent = nil
        let target: StudioTarget
        do { target = try StudioTarget.parse(link: link, code: code.isEmpty ? nil : code) } catch {
            sendError = error.localizedDescription; return
        }
        sending = true
        let client = StudioClient(target: target)
        let archive = result.archive
        let t = title
        Task {
            do {
                let r = try await client.upload(archive: archive, title: t) { p, s in
                    Task { @MainActor in progress = p; status = s }
                }
                await MainActor.run { sent = r; sending = false }
            } catch {
                await MainActor.run { sendError = error.localizedDescription; sending = false }
            }
        }
    }
}
