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

    /// RealityKit's scene-understanding overlay: the LiDAR mesh drawn as a grid over everything measured (like
    /// Polycam's scanning view). On by default on LiDAR phones.
    func showSurfaces(_ on: Bool) {
        guard let v = view else { return }
        if on { v.debugOptions.insert(.showSceneUnderstanding) } else { v.debugOptions.remove(.showSceneUnderstanding) }
    }
}

struct ARViewContainer: UIViewRepresentable {
    let engine: CaptureEngine
    let holder: ARViewHolder
    var showSurfaces = false
    var onTap: (CGPoint) -> Void

    func makeUIView(context: Context) -> ARView {
        let v = ARView(frame: .zero, cameraMode: .ar, automaticallyConfigureSession: false)
        v.renderOptions = [.disableMotionBlur, .disableDepthOfField, .disablePersonOcclusion, .disableGroundingShadows,
                           .disableHDR, .disableCameraGrain, .disableAREnvironmentLighting, .disableFaceMesh]
        v.environment.sceneUnderstanding.options = []
        // Polycam-style live mesh grid on everything LiDAR has measured (LiDAR phones; can be hidden from the ⋯ menu)
        if showSurfaces { v.debugOptions.insert(.showSceneUnderstanding) }
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
    @State private var exposureLockWanted = true       // locked automatically once exposure settles
    @State private var finishing = false
    @State private var showFinish = false
    @State private var autoBeforeSheet: Bool?
    @State private var showSurfaces = false
    @State private var confirmReset = false
    @State private var flash = false

    var body: some View {
        ZStack {
            if let engine {
                ARViewContainer(engine: engine, holder: holder, showSurfaces: showSurfaces) { p in handleTap(p) }
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
        // no photos while a sheet covers the camera (the phone is usually pointing at the floor then)
        .onChange(of: showFinish || showScale) { _, sheetOpen in
            guard let engine, !finishing else { return }
            if sheetOpen {
                autoBeforeSheet = snap.autoCapture
                engine.setAutoCapture(false)
            } else if let was = autoBeforeSheet {
                engine.setAutoCapture(was)
                autoBeforeSheet = nil
            }
        }
        .onChange(of: showSurfaces) { _, on in holder.showSurfaces(on) }
        .sheet(isPresented: $showFinish) {
            if let engine {
                FinishSheet(engine: engine, snap: snap,
                            onCheckScale: { showFinish = false; DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { showScale = true } },
                            onFinish: { overrideReason in
                                showFinish = false
                                finishing = true
                                engine.finish(coverageOverride: overrideReason) { result in
                                    if case .success = result { onFinished(engine.sessionDirectory.lastPathComponent) }
                                }
                            })
                    .presentationDetents([.medium, .large])
            }
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
            showSurfaces = model.capabilities.sceneReconstruction      // live mesh grid on by default with LiDAR
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
            if snap.newAreaSuggested {
                HStack {
                    Button("Mark doorway") { showRoomSheet = true }.buttonStyle(.borderedProminent)
                    Button("Same room") { engine.dismissNewArea() }.buttonStyle(.bordered).tint(.white)
                }
            }
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
                CoverageDial(summary: snap.coverage, uncoveredDown: snap.uncoveredDown, uncoveredLevel: snap.uncoveredHeadings,
                             uncoveredUp: snap.uncoveredUp, heading: snap.headingNow,
                             directions: (snap.directionsCovered, snap.directionsTotal))
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
        .overlay(alignment: .bottom) {
            // what the camera actually reports, not what was requested
            Label(snap.cameraLock, systemImage: snap.cameraLockOK ? "lock.fill" : "lock.open")
                .font(.caption2.weight(.semibold))
                .padding(.horizontal, 8).padding(.vertical, 3)
                .background((snap.cameraLockOK ? Color.green : Color.orange).opacity(0.8), in: Capsule())
                .foregroundStyle(.white)
                .offset(y: 24)
                .accessibilityLabel(snap.cameraLock)
        }
        .padding(.bottom, 20)
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
                if model.capabilities.sceneReconstruction {
                    Button { showSurfaces.toggle() } label: {
                        Label(showSurfaces ? "Hide scanned surfaces" : "Show scanned surfaces (LiDAR)", systemImage: "square.3.layers.3d")
                    }
                }
                Button {
                    exposureLockWanted.toggle()
                    engine.setExposureLock(exposureLockWanted)
                } label: { Label(exposureLockWanted ? "Turn exposure lock off" : "Lock exposure & colour", systemImage: exposureLockWanted ? "lock.open" : "lock") }
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
                Button { showFinish = true } label: { Text("Finish").font(.headline) }
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

/// Compact coverage indicator: three rings of 12 viewing-direction sectors around the current heading (inner = looking
/// down at the floor, middle = level, outer = looking up at the ceiling; white = seen from two places, blue = still
/// needed), the covered-direction count and floor/ceiling bars. Plain fills, no grid lines or outlines.
struct CoverageDial: View {
    let summary: CoverageSummary?
    let uncoveredDown: [Double]
    let uncoveredLevel: [Double]
    let uncoveredUp: [Double]
    let heading: Double
    let directions: (Int, Int)

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ZStack {
                ring(uncoveredUp, inner: 0.70, outer: 1.0)
                ring(uncoveredLevel, inner: 0.42, outer: 0.68)
                ring(uncoveredDown, inner: 0.16, outer: 0.40)
                Image(systemName: "location.north.fill").font(.system(size: 9)).foregroundStyle(.white)
            }
            .frame(width: 80, height: 80)
            Text("Directions \(directions.0)/\(directions.1)").font(.system(size: 10).monospacedDigit()).foregroundStyle(.white)
            if let s = summary {
                bar("Floor", s.floorCoveredFractionEstimate)
                bar("Ceiling", s.ceilingCoveredFractionEstimate)
                Text(methodLabel(s)).font(.system(size: 9)).foregroundStyle(.white.opacity(0.8))
            }
        }
        .padding(8)
        .background(.black.opacity(0.45), in: RoundedRectangle(cornerRadius: 14))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Coverage: \(directions.0) of \(directions.1) viewing directions; floor \(pct(summary?.floorCoveredFractionEstimate)); ceiling \(pct(summary?.ceilingCoveredFractionEstimate))")
    }

    func ring(_ uncovered: [Double], inner: Double, outer: Double) -> some View {
        ZStack {
            ForEach(0..<12, id: \.self) { i in
                let mid = (Double(i) + 0.5) * 30
                let need = uncovered.contains { abs($0 - mid) < 1 }
                SectorShape(start: Double(i) * 30 - heading, end: Double(i + 1) * 30 - heading, innerFraction: inner, outerFraction: outer)
                    .fill(need ? Color.blue.opacity(0.6) : Color.white.opacity(0.85))
            }
        }
    }

    func pct(_ v: Double?) -> String { v.map { "about \(Int($0 * 100)) percent" } ?? "unknown" }

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
    var innerFraction = 0.0
    var outerFraction = 1.0
    func path(in r: CGRect) -> Path {
        var p = Path()
        let c = CGPoint(x: r.midX, y: r.midY)
        let ro = r.width / 2 * outerFraction, ri = r.width / 2 * innerFraction
        let a0 = Angle.degrees(start - 90 + 1.5), a1 = Angle.degrees(end - 90 - 1.5)
        p.addArc(center: c, radius: ro, startAngle: a0, endAngle: a1, clockwise: false)
        p.addArc(center: c, radius: ri, startAngle: a1, endAngle: a0, clockwise: true)
        p.closeSubpath()
        return p
    }
}

/// End-of-scan summary: coverage per room, a scale-check prompt, and a confirmation when coverage is low.
/// It warns; finishing is never blocked.
struct FinishSheet: View {
    let engine: CaptureEngine
    let snap: CaptureSnapshot
    let onCheckScale: () -> Void
    let onFinish: (String?) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var assessment: CoverageAssessment.Result?
    @State private var overrideReason = ""

    private var trimmedReason: String {
        overrideReason.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        NavigationStack {
            List {
                Section("This scan") {
                    LabeledContent("Photos", value: "\(snap.accepted)")
                    if snap.depthFrames > 0 { LabeledContent("With LiDAR depth", value: "\(snap.depthFrames)") }
                    if let a = assessment {
                        LabeledContent("Viewing directions", value: "\(a.directionsCovered) of \(a.directionsTotal)")
                        LabeledContent("Floor (estimate)", value: a.floor.map { "~\(Int($0 * 100))%" } ?? "unknown")
                        LabeledContent("Ceiling (estimate)", value: a.ceiling.map { "~\(Int($0 * 100))%" } ?? "unknown")
                    }
                    LabeledContent("Exposure", value: snap.cameraLock)
                    if snap.hiResFallback { Text("High-resolution photos stopped working; later photos are video resolution.").foregroundStyle(.orange) }
                }
                if let a = assessment, a.isLow {
                    Section {
                        ForEach(a.issues, id: \.self) { Text($0) }
                        Text("Keep scanning, or record why you’re finishing with incomplete coverage.")
                            .font(.footnote).foregroundStyle(.secondary)
                        TextField("Reason for finishing", text: $overrideReason)
                    } header: { Label("Coverage looks low", systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange) }
                }
                if snap.scaleChecks == 0 {
                    Section("Scale check (recommended)") {
                        Text("Measure one door width or any straight edge with a tape measure and compare it with ARKit. Takes 30 seconds and tells the reconstruction whether the scan's scale can be trusted.")
                            .font(.footnote)
                        Button { onCheckScale() } label: { Label("Check scale now", systemImage: "ruler") }
                    }
                }
                Section {
                    Button { dismiss() } label: { Label("Keep scanning", systemImage: "camera.viewfinder") }
                    Button(role: (assessment?.isLow ?? false) ? .destructive : nil) {
                        guard let assessment else { return }
                        onFinish(assessment.isLow ? trimmedReason : nil)
                    } label: {
                        Label((assessment?.isLow ?? false) ? "Finish anyway" : "Finish scan", systemImage: "checkmark.circle")
                    }
                    .disabled(assessment == nil || (assessment?.isLow == true && trimmedReason.isEmpty))
                }
            }
            .navigationTitle("Finish scan?").navigationBarTitleDisplayMode(.inline)
            .onAppear {
                engine.coverageForReview { rooms, roomList in
                    assessment = CoverageAssessment.assess(rooms) { id in roomList.first { $0.id == id }?.label ?? id ?? "Room" }
                }
            }
        }
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
