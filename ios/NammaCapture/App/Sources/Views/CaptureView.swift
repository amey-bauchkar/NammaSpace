import ARKit
import NammaCaptureCore
import RealityKit
import SwiftUI

/// Holds the ARView so SwiftUI controls can raycast and convert screen points.
final class ARViewHolder {
    weak var view: ARView?

    /// Normalized point in the stored (sensor-landscape) image for a point on screen.
    func imagePoint(for p: CGPoint) -> CGPoint? {
        guard let v = view, let f = v.session.currentFrame, v.bounds.width > 0 else { return nil }
        let t = f.displayTransform(for: .portrait, viewportSize: v.bounds.size).inverted()
        return CGPoint(x: p.x / v.bounds.width, y: p.y / v.bounds.height).applying(t)
    }

    /// World point under a screen point, labelled by what the raycast hit.
    func raycast(_ p: CGPoint) -> (SIMD3<Float>, String)? {
        guard let v = view else { return nil }
        for (allow, label) in [(ARRaycastQuery.Target.existingPlaneGeometry, "raycast_existing_plane_geometry"),
                               (.estimatedPlane, "raycast_estimated_plane")] {
            if let hit = v.raycast(from: p, allowing: allow, alignment: .any).first {
                let c = hit.worldTransform.columns.3
                return (SIMD3(c.x, c.y, c.z), label)
            }
        }
        return nil
    }

    var center: CGPoint { view.map { CGPoint(x: $0.bounds.midX, y: $0.bounds.midY) } ?? .zero }
}

struct ARViewContainer: UIViewRepresentable {
    let engine: CaptureEngine
    let holder: ARViewHolder
    var onTap: (CGPoint) -> Void

    func makeUIView(context: Context) -> ARView {
        let v = ARView(frame: .zero, cameraMode: .ar, automaticallyConfigureSession: false)
        v.renderOptions = [.disableMotionBlur, .disableDepthOfField, .disablePersonOcclusion, .disableGroundingShadows,
                           .disableHDR, .disableCameraGrain, .disableAREnvironmentLighting, .disableFaceMesh]
        v.environment.sceneUnderstanding.options = []
        holder.view = v
        engine.attach(v.session)
        let tap = UITapGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.tapped(_:)))
        v.addGestureRecognizer(tap)
        return v
    }
    func updateUIView(_ uiView: ARView, context: Context) { context.coordinator.onTap = onTap }
    func makeCoordinator() -> Coordinator { Coordinator(onTap: onTap) }

    final class Coordinator: NSObject {
        var onTap: (CGPoint) -> Void
        init(onTap: @escaping (CGPoint) -> Void) { self.onTap = onTap }
        @objc func tapped(_ g: UITapGestureRecognizer) { onTap(g.location(in: g.view)) }
    }
}

struct CaptureView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var phase
    let resumeId: String?
    let name: String
    let onFinished: (String) -> Void

    @State private var engine: CaptureEngine?
    @State private var startError: String?
    @State private var snap = CaptureSnapshot()
    @State private var holder = ARViewHolder()
    @State private var annotating: String?          // kind being marked
    @State private var showRoomSheet = false
    @State private var showScale = false
    @State private var exposureLocked = false
    @State private var finishing = false
    @State private var confirmReset = false
    @State private var flash = false

    var body: some View {
        ZStack {
            if let engine {
                ARViewContainer(engine: engine, holder: holder) { p in handleTap(p) }
                    .ignoresSafeArea()
                    .overlay {
                        RoundedRectangle(cornerRadius: 0).strokeBorder(Color.green.opacity(flash ? 0.8 : 0), lineWidth: 6)
                            .ignoresSafeArea().allowsHitTesting(false)
                    }
                overlay(engine)
            } else if let startError {
                ContentUnavailableView("Cannot start the scan", systemImage: "exclamationmark.triangle", description: Text(startError))
            } else {
                ProgressView("Starting camera…")
            }
        }
        .navigationBarBackButtonHidden(true)
        .toolbar(.hidden, for: .navigationBar)
        .statusBarHidden(false)
        .onAppear(perform: start)
        .onDisappear { UIApplication.shared.isIdleTimerDisabled = false }
        .onReceive(NotificationCenter.default.publisher(for: ProcessInfo.thermalStateDidChangeNotification)) { _ in
            engine?.thermalChanged(ProcessInfo.processInfo.thermalState)
        }
        .onChange(of: snap.lastAcceptedAt) { _, _ in
            withAnimation(.easeOut(duration: 0.08)) { flash = true }
            withAnimation(.easeIn(duration: 0.35).delay(0.08)) { flash = false }
        }
        .sheet(isPresented: $showRoomSheet) {
            RoomSheet(rooms: snap.rooms, current: snap.roomLabel,
                      onDoorway: { next in engine?.markDoorway(nextRoom: next) },
                      onRename: { id, label in engine?.renameRoom(id, to: label) })
                .presentationDetents([.medium])
        }
        .sheet(isPresented: $showScale) {
            if let engine { ScaleCheckSheet(engine: engine, holder: holder).presentationDetents([.medium]) }
        }
        .confirmationDialog("Reset tracking?", isPresented: $confirmReset, titleVisibility: .visible) {
            Button("Reset tracking", role: .destructive) { engine?.resetTracking() }
        } message: {
            Text("Use only if tracking cannot recover. Photos after the reset are in a new coordinate frame and must be aligned to the earlier ones during reconstruction.")
        }
    }

    private func start() {
        guard engine == nil else { return }
        UIApplication.shared.isIdleTimerDisabled = true
        do {
            let e: CaptureEngine
            if let id = resumeId {
                e = try CaptureEngine(resuming: id, store: model.store, caps: model.capabilities)
            } else {
                e = try CaptureEngine(store: model.store, name: name, caps: model.capabilities,
                                      appVersion: model.appVersion, appBuild: model.appBuild)
            }
            e.onSnapshot = { s in snap = s }
            engine = e
        } catch {
            startError = "\(error)"
        }
    }

    private func handleTap(_ p: CGPoint) {
        guard let kind = annotating, let engine else { return }
        guard let ip = holder.imagePoint(for: p) else { return }
        let hit = holder.raycast(p)
        engine.annotate(kind: kind, imagePoint: ip, worldPoint: hit?.0, method: hit?.1)
        annotating = nil
    }

    // MARK: overlay

    @ViewBuilder private func overlay(_ engine: CaptureEngine) -> some View {
        VStack(spacing: 10) {
            topBar
            if let g = snap.guidance.first { GuidanceBanner(g: g) }
            if let a = annotating {
                Text("Tap the \(a) on screen").font(.headline).padding(10)
                    .background(.ultraThinMaterial, in: Capsule())
            }
            ForEach(snap.errors.suffix(1), id: \.self) { e in
                Text(e).font(.footnote).padding(8).background(.red.opacity(0.85), in: RoundedRectangle(cornerRadius: 10))
                    .foregroundStyle(.white)
            }
            Spacer()
            HStack(alignment: .bottom) {
                CoverageDial(summary: snap.coverage, uncovered: snap.uncoveredHeadings, heading: snap.headingNow)
                Spacer()
                statsColumn
            }
            .padding(.horizontal)
            controls(engine)
        }
        .padding(.top, 8)
        .padding(.bottom, 12)
    }

    private var topBar: some View {
        HStack(spacing: 12) {
            Button { showRoomSheet = true } label: {
                Label(snap.roomLabel.isEmpty ? "Room" : snap.roomLabel, systemImage: "door.left.hand.open")
                    .font(.subheadline.weight(.semibold)).lineLimit(1)
            }
            .buttonStyle(.borderedProminent).tint(.black.opacity(0.55))
            Spacer()
            TrackingDot(state: snap.trackingState)
            Text(Duration.seconds(snap.elapsed).formatted(.time(pattern: .minuteSecond)))
                .font(.subheadline.monospacedDigit()).padding(.horizontal, 10).padding(.vertical, 6)
                .background(.black.opacity(0.55), in: Capsule()).foregroundStyle(.white)
        }
        .padding(.horizontal)
    }

    private var statsColumn: some View {
        VStack(alignment: .trailing, spacing: 2) {
            Text("\(snap.accepted)").font(.title.weight(.bold).monospacedDigit())
            Text("photos").font(.caption)
            if model.capabilities.hasLiDAR { Text("\(snap.depthFrames) with depth").font(.caption2) }
            if snap.rejected > 0 { Text("\(snap.rejected) skipped").font(.caption2).foregroundStyle(.white.opacity(0.75)) }
            if snap.coordinateFrames > 1 { Text("\(snap.coordinateFrames) tracking segments").font(.caption2).foregroundStyle(.orange) }
        }
        .foregroundStyle(.white)
        .padding(10)
        .background(.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 14))
        .accessibilityElement(children: .combine)
    }

    private func controls(_ engine: CaptureEngine) -> some View {
        HStack(alignment: .center) {
            Menu {
                Button { showRoomSheet = true } label: { Label("Doorway / next room", systemImage: "door.left.hand.open") }
                Button { engine.markLoopBack() } label: { Label("Mark loop-back here", systemImage: "arrow.triangle.capsulepath") }
                Menu {
                    ForEach(["mirror", "glass", "window", "other"], id: \.self) { k in
                        Button(k.capitalized) { annotating = k }
                    }
                } label: { Label("Mark mirror / glass / window", systemImage: "rectangle.dashed") }
                Button { showScale = true } label: { Label("Check scale with a tape measure", systemImage: "ruler") }
                Button {
                    exposureLocked.toggle()
                    engine.setExposureLock(exposureLocked)
                } label: { Label(exposureLocked ? "Unlock exposure & colour" : "Lock exposure & colour", systemImage: exposureLocked ? "lock.open" : "lock") }
                Button(role: .destructive) { confirmReset = true } label: { Label("Reset tracking…", systemImage: "arrow.counterclockwise") }
            } label: {
                Image(systemName: "ellipsis.circle.fill").font(.system(size: 40)).symbolRenderingMode(.hierarchical)
                    .foregroundStyle(.white).accessibilityLabel("More actions")
            }
            Spacer()
            Button { engine.manualCapture(override: true) } label: {
                ZStack {
                    Circle().fill(.white).frame(width: 76, height: 76)
                    Circle().stroke(.white.opacity(0.6), lineWidth: 4).frame(width: 88, height: 88)
                }
            }
            .accessibilityLabel("Take photo now")
            Spacer()
            VStack(spacing: 8) {
                Button { engine.setAutoCapture(!snap.autoCapture) } label: {
                    Label(snap.autoCapture ? "Auto" : "Paused", systemImage: snap.autoCapture ? "play.circle.fill" : "pause.circle.fill")
                        .font(.caption.weight(.semibold))
                }
                .buttonStyle(.borderedProminent).tint(snap.autoCapture ? .green : .gray)
                Button {
                    finishing = true
                    engine.finish { result in
                        if case .success = result, let id = engine.sessionDirectory.lastPathComponent as String? { onFinished(id) }
                    }
                } label: { Text("Finish").font(.headline) }
                .buttonStyle(.borderedProminent).tint(.blue).disabled(finishing)
            }
        }
        .padding(.horizontal, 20)
    }
}

struct GuidanceBanner: View {
    let g: Guidance
    var body: some View {
        Label(g.message, systemImage: icon)
            .font(.headline)
            .multilineTextAlignment(.leading)
            .padding(.horizontal, 14).padding(.vertical, 10)
            .background(color.opacity(0.88), in: RoundedRectangle(cornerRadius: 14))
            .foregroundStyle(.white)
            .padding(.horizontal)
            .accessibilityAddTraits(.updatesFrequently)
    }
    var color: Color { switch g.level { case .blocking: .red; case .warning: .orange; case .suggestion: .blue; case .info: .gray } }
    var icon: String { switch g.level { case .blocking: "exclamationmark.octagon.fill"; case .warning: "exclamationmark.triangle.fill"; default: "lightbulb.fill" } }
}

struct TrackingDot: View {
    let state: String
    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: 10, height: 10)
            Text(label).font(.caption.weight(.semibold))
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(.black.opacity(0.55), in: Capsule()).foregroundStyle(.white)
        .accessibilityLabel("Tracking \(label)")
    }
    var color: Color { state == "normal" ? .green : state == "limited" ? .orange : .red }
    var label: String { state == "normal" ? "Tracking" : state == "limited" ? "Limited" : "No tracking" }
}

/// Compact coverage indicator: a ring of 12 viewing-direction sectors around the current heading (filled = seen from two
/// spots; soft blue = still needed) and floor/ceiling bars. Plain fills, no grid lines or outlines.
struct CoverageDial: View {
    let summary: CoverageSummary?
    let uncovered: [Double]
    let heading: Double

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ZStack {
                ForEach(0..<12, id: \.self) { i in
                    let mid = (Double(i) + 0.5) * 30
                    let need = uncovered.contains { abs($0 - mid) < 1 }
                    SectorShape(start: Double(i) * 30 - heading, end: Double(i + 1) * 30 - heading)
                        .fill(need ? Color.blue.opacity(0.55) : Color.white.opacity(0.85))
                }
                Circle().fill(.black.opacity(0.55)).frame(width: 34, height: 34)
                Image(systemName: "location.north.fill").font(.caption).foregroundStyle(.white)
            }
            .frame(width: 72, height: 72)
            if let s = summary {
                bar("Floor", s.floorCoveredFractionEstimate)
                bar("Ceiling", s.ceilingCoveredFractionEstimate)
                Text(methodLabel(s)).font(.system(size: 9)).foregroundStyle(.white.opacity(0.8))
            }
        }
        .padding(8)
        .background(.black.opacity(0.45), in: RoundedRectangle(cornerRadius: 14))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Coverage: \(12 - uncovered.count) of 12 directions covered")
    }

    func bar(_ label: String, _ v: Double?) -> some View {
        HStack(spacing: 4) {
            Text(label).font(.system(size: 10)).foregroundStyle(.white).frame(width: 38, alignment: .leading)
            ZStack(alignment: .leading) {
                Capsule().fill(.white.opacity(0.25)).frame(width: 40, height: 5)
                Capsule().fill(.white).frame(width: 40 * (v ?? 0), height: 5)
            }
            Text(v.map { "~\(Int($0 * 100))%" } ?? "–").font(.system(size: 9).monospacedDigit()).foregroundStyle(.white)
        }
    }

    func methodLabel(_ s: CoverageSummary) -> String {
        switch s.method {
        case "lidar_depth_samples": return "from LiDAR depth (estimate)"
        case "arkit_plane_estimates": return "from detected planes (rough estimate)"
        default: return "viewing directions only"
        }
    }
}

struct SectorShape: Shape {
    let start: Double, end: Double
    func path(in r: CGRect) -> Path {
        var p = Path()
        let c = CGPoint(x: r.midX, y: r.midY)
        p.move(to: c)
        p.addArc(center: c, radius: r.width / 2, startAngle: .degrees(start - 90 + 1), endAngle: .degrees(end - 90 - 1), clockwise: false)
        p.closeSubpath()
        return p
    }
}

struct RoomSheet: View {
    let rooms: [Room]
    let current: String
    let onDoorway: (String?) -> Void
    let onRename: (String, String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var nextName = ""
    @State private var renaming: Room?
    @State private var renameText = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Going through a doorway") {
                    TextField("Next room name (e.g. Kitchen)", text: $nextName)
                    Button("Mark doorway and start this room") {
                        onDoorway(nextName.isEmpty ? "Room \(rooms.count + 1)" : nextName); dismiss()
                    }
                    Button("Mark doorway only (same room)") { onDoorway(nil); dismiss() }
                    Text("Stand in the doorway when you tap. Then turn around once and photograph the room you came from through the doorway.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("Rooms") {
                    ForEach(rooms) { r in
                        HStack {
                            Text(r.label)
                            if r.label == current { Text("current").font(.caption).foregroundStyle(.secondary) }
                            Spacer()
                            Button("Rename") { renaming = r; renameText = r.label }.font(.caption)
                        }
                    }
                }
            }
            .navigationTitle("Rooms").navigationBarTitleDisplayMode(.inline)
            .alert("Rename room", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
                TextField("Name", text: $renameText)
                Button("Save") { if let r = renaming { onRename(r.id, renameText) }; renaming = nil }
                Button("Cancel", role: .cancel) { renaming = nil }
            }
        }
    }
}

/// Optional check of ARKit's metric scale against a tape measure. It records the comparison; it never rescales poses.
struct ScaleCheckSheet: View {
    let engine: CaptureEngine
    let holder: ARViewHolder
    @Environment(\.dismiss) private var dismiss
    @State private var a: SIMD3<Float>?
    @State private var b: SIMD3<Float>?
    @State private var tape = ""
    @State private var message: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Aim the centre of the screen at one end of something you have measured (a door width, a table edge), tap Set A; then the other end, Set B. Enter the tape-measured length.")
                        .font(.footnote)
                }
                Section {
                    Button(a == nil ? "Set A" : "A set ✓") { a = holder.raycast(holder.center)?.0; if a == nil { message = "No surface found at the centre; move closer." } }
                    Button(b == nil ? "Set B" : "B set ✓") { b = holder.raycast(holder.center)?.0; if b == nil { message = "No surface found at the centre; move closer." } }
                        .disabled(a == nil)
                    if let a, let b { Text(String(format: "ARKit distance: %.3f m", simd_distance(a, b))) }
                    TextField("Tape measure (metres)", text: $tape).keyboardType(.decimalPad)
                }
                if let message { Text(message).foregroundStyle(.orange) }
            }
            .navigationTitle("Check scale").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        if let a, let b, let t = Double(tape.replacingOccurrences(of: ",", with: ".")), t > 0 {
                            engine.addScaleValidation(a: a, b: b, trueMeters: t); dismiss()
                        } else { message = "Set both points and enter a length in metres." }
                    }
                }
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            }
        }
    }
}
