import AVFoundation
import NammaCaptureCore
import SwiftUI

enum Route: Hashable {
    case capture(resumeId: String?, name: String)
    case review(String)
}

struct LibraryView: View {
    @Environment(AppModel.self) private var model
    @State private var path: [Route] = []
    @State private var showNew = false
    @State private var trashCandidate: SessionSummary?

    var body: some View {
        NavigationStack(path: $path) {
            List {
                if let e = model.startupError {
                    Label(e, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red)
                }
                Section {
                    Button { showNew = true } label: {
                        Label("New scan", systemImage: "plus.viewfinder").font(.headline)
                    }
                    .disabled(!model.capabilities.worldTracking)
                    if !model.capabilities.worldTracking {
                        Text("This device does not support ARKit world tracking, so it cannot scan. You can still review and export scans copied to it.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
                Section("Scans") {
                    if model.sessions.isEmpty {
                        Text("No scans yet.").foregroundStyle(.secondary)
                    }
                    ForEach(model.sessions) { s in
                        NavigationLink(value: Route.review(s.id)) { SessionRow(s: s) }
                            .swipeActions {
                                Button("Move to Bin", role: .destructive) { trashCandidate = s }
                            }
                    }
                }
            }
            .navigationTitle("Namma Capture")
            .refreshable { model.refresh() }
            .onAppear { model.refresh() }
            .sheet(isPresented: $showNew) {
                NewScanSheet { name in
                    showNew = false
                    path.append(.capture(resumeId: nil, name: name))
                }
            }
            .confirmationDialog("Move this scan to the bin?", isPresented: Binding(get: { trashCandidate != nil }, set: { if !$0 { trashCandidate = nil } }),
                                titleVisibility: .visible) {
                Button("Move to Bin", role: .destructive) {
                    if let s = trashCandidate { try? model.store.trash(s.id); model.refresh() }
                    trashCandidate = nil
                }
            } message: {
                Text("The scan is moved to a hidden bin folder on this phone (Files > Namma Capture > Scans/.trash), not erased.")
            }
            .navigationDestination(for: Route.self) { r in
                switch r {
                case .capture(let resumeId, let name):
                    CaptureView(resumeId: resumeId, name: name) { id in
                        path = [.review(id)]
                        model.refresh()
                    }
                case .review(let id):
                    ReviewView(sessionId: id) { path.append(.capture(resumeId: id, name: "")) }
                }
            }
        }
    }
}

private struct SessionRow: View {
    let s: SessionSummary
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(s.name).font(.headline)
                Spacer()
                if s.interrupted {
                    Label("Interrupted", systemImage: "exclamationmark.circle").font(.caption).foregroundStyle(.orange)
                } else if !s.exports.isEmpty {
                    Label("Exported", systemImage: "checkmark.seal").font(.caption).foregroundStyle(.green)
                }
            }
            Text("\(s.acceptedFrames) photos · \(ByteCountFormatter.string(fromByteCount: s.bytesOnDisk, countStyle: .file)) · \(String(s.startedAt.prefix(16)).replacingOccurrences(of: "T", with: " "))")
                .font(.caption).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
    }
}

struct NewScanSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openURL) private var openURL
    let start: (String) -> Void
    @State private var name = ""
    @State private var camera = AVCaptureDevice.authorizationStatus(for: .video)

    var body: some View {
        NavigationStack {
            Form {
                Section("Space name") {
                    TextField("e.g. Flat 3B, ground floor", text: $name).textInputAutocapitalization(.words)
                }
                Section("This iPhone") {
                    ForEach(model.capabilities.summaryLines, id: \.0) { line in
                        HStack(alignment: .top) {
                            Image(systemName: line.1 ? "checkmark.circle.fill" : "minus.circle")
                                .foregroundStyle(line.1 ? .green : .secondary)
                            VStack(alignment: .leading) {
                                Text(line.0)
                                Text(line.2).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                Section("Camera access") {
                    switch camera {
                    case .authorized:
                        Label("Allowed", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    case .notDetermined:
                        Button("Allow camera access") {
                            AVCaptureDevice.requestAccess(for: .video) { _ in
                                DispatchQueue.main.async { camera = AVCaptureDevice.authorizationStatus(for: .video) }
                            }
                        }
                    default:
                        Text("Camera access is off. Scanning needs the camera.").foregroundStyle(.red)
                        Button("Open Settings") { openURL(URL(string: UIApplication.openSettingsURLString)!) }
                    }
                }
                Section {
                    Text("Walk slowly through each room. Photos are taken automatically when the view has changed enough and the image is sharp. Mark doorways when you change rooms, and look back through each doorway.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("New scan")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Start") { start(name.isEmpty ? "Scan \(Date().formatted(date: .abbreviated, time: .shortened))" : name) }
                        .disabled(camera != .authorized || !model.capabilities.worldTracking)
                }
            }
        }
    }
}
