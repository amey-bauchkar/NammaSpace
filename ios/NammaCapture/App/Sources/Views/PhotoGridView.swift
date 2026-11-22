import ImageIO
import NammaCaptureCore
import SwiftUI
import UIKit

/// Browse a scan's keyframes on the phone: thumbnails (decoded small with ImageIO, never full size in a list) and,
/// per photo, the record that the reconstruction will see.
struct PhotoGridView: View {
    let directory: URL
    let frames: [FrameRecord]
    private let columns = [GridItem(.adaptive(minimum: 96), spacing: 4)]

    var body: some View {
        ScrollView {
            LazyVGrid(columns: columns, spacing: 4) {
                ForEach(frames, id: \.frameId) { f in
                    NavigationLink { PhotoDetailView(directory: directory, frame: f) } label: {
                        Thumbnail(url: directory.appendingPathComponent(f.image.path), maxPixel: 240)
                            .aspectRatio(3.0 / 4.0, contentMode: .fill)
                            .clipped()
                            .overlay(alignment: .topTrailing) { badge(f) }
                    }
                    .accessibilityLabel("Photo \(f.index)")
                }
            }
            .padding(4)
        }
        .navigationTitle("\(frames.count) photos")
    }

    @ViewBuilder func badge(_ f: FrameRecord) -> some View {
        if !f.flags.isEmpty || f.quality.overridden || !f.quality.warnings.isEmpty {
            Image(systemName: "exclamationmark.triangle.fill").font(.caption2).padding(3)
                .foregroundStyle(.white).background(.orange, in: Circle()).padding(3)
        }
    }
}

struct PhotoDetailView: View {
    let directory: URL
    let frame: FrameRecord

    var body: some View {
        List {
            Section {
                Thumbnail(url: directory.appendingPathComponent(frame.image.path), maxPixel: 1400)
                    .aspectRatio(3.0 / 4.0, contentMode: .fit)
            }
            Section("Photo") {
                row("Size", "\(frame.image.width) × \(frame.image.height)")
                row("Source", frame.image.source == "high_resolution_capture" ? "High-resolution still" : "Video frame")
                row("Taken because", frame.selection.reason.replacingOccurrences(of: "_", with: " "))
                row("Tracking", [frame.tracking.state, frame.tracking.reason].compactMap { $0 }.joined(separator: ", "))
                row("Sharpness", String(format: "%.0f", frame.quality.metrics.sharpness))
                row("Brightness", String(format: "%.0f / 255", frame.quality.metrics.meanLuma))
                if let iso = frame.exposure.iso { row("ISO", String(format: "%.0f", iso)) }
                if let e = frame.exposure.exposureDurationS, e > 0 { row("Shutter", "1/\(Int((1 / e).rounded())) s") }
                row("LiDAR depth", frame.depth.map { "\($0.width) × \($0.height), \(Int($0.validSampleFraction * 100))% valid" } ?? "none")
            }
            if !frame.quality.warnings.isEmpty || !frame.flags.isEmpty {
                Section("Notes") {
                    ForEach(frame.quality.warnings + frame.flags, id: \.self) { Text($0.replacingOccurrences(of: "_", with: " ")).font(.footnote) }
                }
            }
        }
        .navigationTitle(frame.frameId)
        .navigationBarTitleDisplayMode(.inline)
    }

    func row(_ k: String, _ v: String) -> some View { LabeledContent(k, value: v) }
}

/// Stored images are in sensor (landscape) orientation; the app is held upright, so they are shown rotated to portrait.
struct Thumbnail: View {
    let url: URL
    let maxPixel: Int
    @State private var image: UIImage?

    var body: some View {
        ZStack {
            Color.secondary.opacity(0.15)
            if let image { Image(uiImage: image).resizable().scaledToFill() }
        }
        .task(id: url) {
            let u = url, m = maxPixel
            image = await Task.detached(priority: .utility) { Self.load(u, maxPixel: m) }.value
        }
    }

    nonisolated static func load(_ url: URL, maxPixel: Int) -> UIImage? {
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
              let cg = CGImageSourceCreateThumbnailAtIndex(src, 0, [
                  kCGImageSourceCreateThumbnailFromImageAlways: true,
                  kCGImageSourceThumbnailMaxPixelSize: maxPixel,
                  kCGImageSourceCreateThumbnailWithTransform: true,
              ] as CFDictionary) else { return nil }
        return UIImage(cgImage: cg, scale: 1, orientation: .right)
    }
}
