import NammaCaptureCore
import SwiftUI

/// "Export like Polycam": raw data, images and the coloured glTF mesh, each built on demand and shared from here.
struct PolycamExportsSection: View {
    let directory: URL
    let hasMesh: Bool

    @State private var busy: ShareExports.Kind?
    @State private var progress = 0.0
    @State private var files: [ShareExports.Kind: URL] = [:]
    @State private var error: String?

    var body: some View {
        Section {
            row(.raw, title: "Raw data (.zip)", icon: "cube.transparent",
                detail: "Full-resolution photos, poses, LiDAR depth and confidence in Polycam's raw layout. Namma Space's LiDAR route reads it directly.")
            row(.images, title: "Images (.zip)", icon: "photo.stack", detail: "All keyframe photos at full resolution.")
            if hasMesh {
                row(.gltf, title: "glTF mesh (.zip)", icon: "square.3.layers.3d",
                    detail: "LiDAR mesh with a photo texture baked from the 12 MP photos (sharpest view per surface, LiDAR occlusion check, colour-matched). Import it together with the raw data for the mesh route.")
            } else {
                Label("No LiDAR mesh in this scan (needs a LiDAR iPhone and a finished scan).", systemImage: "info.circle")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            if let busy {
                ProgressView(value: progress) { Text(busy == .gltf ? "Texturing the mesh from the full-resolution photos (about a minute)…" : "Packing…").font(.caption) }
            }
            if let error { Text(error).foregroundStyle(.red).font(.footnote) }
        } header: { Text("Export like Polycam") } footer: {
            Text("For Namma Space: send the raw data and the glTF zip together to the Studio's 3D-scan import.")
        }
    }

    @ViewBuilder func row(_ kind: ShareExports.Kind, title: String, icon: String, detail: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Label(title, systemImage: icon).font(.headline)
                Spacer()
                if let url = files[kind] {
                    ShareLink(item: url) { Image(systemName: "square.and.arrow.up") }
                } else {
                    Button("Create") { make(kind) }.disabled(busy != nil)
                }
            }
            Text(detail).font(.caption).foregroundStyle(.secondary)
            if let url = files[kind] {
                Text("\(url.lastPathComponent) · \(size(url))").font(.caption2).foregroundStyle(.secondary)
            }
        }
    }

    func size(_ u: URL) -> String {
        let n: Int = (try? u.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
        return ByteCountFormatter.string(fromByteCount: Int64(n), countStyle: .file)
    }

    func make(_ kind: ShareExports.Kind) {
        busy = kind; progress = 0; error = nil
        let dir = directory
        Task.detached(priority: .userInitiated) {
            do {
                let url = try ShareExports.export(kind, sessionDirectory: dir) { p in Task { @MainActor in progress = p } }
                await MainActor.run { files[kind] = url; busy = nil }
            } catch {
                await MainActor.run { self.error = "\(error)"; busy = nil }
            }
        }
    }
}
