import ARKit
import NammaCaptureCore
import UIKit

/// Snapshot of capture state for the UI (built on the capture queue, published on the main actor ~5x per second).
struct CaptureSnapshot: Equatable {
    var trackingState = "not_available"
    var trackingReason: String?
    var guidance: [Guidance] = []
    var accepted = 0
    var rejected = 0
    var depthFrames = 0
    var highResFrames = 0
    var pendingWrites = 0
    var lastAcceptedAt: Date?
    var lastRejectReasons: [String] = []
    var autoCapture = true
    var interrupted = false
    var roomLabel = ""
    var rooms: [Room] = []
    var coverage: CoverageSummary?
    var uncoveredHeadings: [Double] = []
    var headingNow: Double = 0
    var coordinateFrames = 1
    var exposureControls: [CameraControlResult] = []
    var pendingLookBack = false
    var suggestLoopBack = false
    var annotations = 0
    var errors: [String] = []
    var elapsed: TimeInterval = 0
    var finished = false
    var lastLoopCandidates = 0
    /// e.g. "Exposure & colour locked", "Exposure auto (lock unavailable on this phone)"
    var cameraLock = "Exposure: waiting to lock"
    var cameraLockOK = false
    var spinning = false
    var newAreaSuggested = false
    var uncoveredDown: [Double] = []
    var uncoveredUp: [Double] = []
    var directionsCovered = 0
    var directionsTotal = 36
    var scaleChecks = 0
    var hiResFallback = false
}

final class CaptureEngine: NSObject, ARSessionDelegate, @unchecked Sendable {
    // MARK: configuration
    let queue = DispatchQueue(label: "space.namma.capture.engine", qos: .userInitiated)
    private let encodeQueue = DispatchQueue(label: "space.namma.capture.encode", qos: .utility)
    let caps: DeviceCapabilities
    let store: SessionStore
    private(set) var doc: SessionDocument
    let writer: SessionWriter
    let settings: CaptureSettings
    var jpegQuality: Double { settings.jpegQuality }
    static let maxPendingWrites = 3

    // MARK: state (capture queue only)
    private weak var session: ARSession?
    private var selector: KeyframeSelector
    private var motion = MotionEstimator()
    private var scan = ScanState()
    private var coverage: [String: CoverageModel] = [:]
    private var coordFrame = 0
    private var frameIndex = 0
    private var candidateIndex = 0
    private var eventSeq = 0
    private var captureInFlight = false
    private var pending = 0
    private var lastTrajectoryT = -1.0
    private var lastCoverageT = -1.0
    private var lastCoverageWriteT = -1.0
    private var lastUIT = -1.0
    private var lastStorageCheckT = -1.0
    private var lastThumb: [UInt8]?
    private var lastTracking: (String, String?) = ("", nil)
    private var lastPose: (Pose, Double)?
    private var relocalizedAt: Double?
    private var interrupted = false
    private var autoCapture = true
    private var forceNext: (trigger: String, annotation: PendingAnnotation?)?
    private var thermal: ProcessInfo.ThermalState = .nominal
    private var storageLow = false
    private var storageCritical = false
    private var lastLowOverlap = false
    private var snapshot = CaptureSnapshot()
    private var annotations: [SurfaceAnnotation] = []
    private var startDate = Date()
    private var lastBrightness: Double?
    private var finished = false
    private var ioTail: Task<Void, Never>?
    private var spin = SpinDetector()
    // high-resolution capture failures (issue D)
    private var hiResFailuresInARow = 0
    private var hiResFallback = false
    static let hiResFailuresBeforeFallback = 3
    // automatic exposure / white-balance lock (issue A)
    private var autoLockWanted = true
    private var lockApplied = false
    private var lockAttempts = 0
    private var lockReverts = 0
    private var normalSince: Double?
    private var lastLockCheckT = -1.0
    private var exposureRejectsInARow = 0

    struct PendingAnnotation { let kind: String; let imagePoint: CGPoint; let worldPoint: SIMD3<Float>?; let method: String?; let note: String }

    /// Called on the main actor with each new snapshot.
    var onSnapshot: ((CaptureSnapshot) -> Void)?

    // MARK: init

    /// New scan.
    init(store: SessionStore, name: String, caps: DeviceCapabilities, appVersion: String, appBuild: String) throws {
        self.caps = caps; self.store = store
        var s = CaptureSettings(keyframe: KeyframeThresholds(), quality: QualityThresholds(), jpegQuality: 0.92,
                                trajectorySampleHz: 5, cameraControls: [])
        s.cameraControls.append(CameraControlResult(control: "video_hdr", outcome: "disabled",
                                                    detail: "HDR video off for consistent exposure between frames", at: ISOTime.string()))
        if !caps.configurableDevice { s.cameraControls.append(contentsOf: CameraControls.unavailableAtStart()) }
        settings = s
        selector = KeyframeSelector(thresholds: s.keyframe)
        let id = UUID().uuidString.lowercased()
        var d = SessionDocument(id: id, name: name, startedAt: ISOTime.string(), device: caps.deviceInfo,
                                capabilities: caps.manifestCapabilities(using: caps.highResFormat), settings: s,
                                depthSource: caps.sceneDepth ? "arkit_scene_depth" : nil,
                                depthUnavailableReason: caps.sceneDepth ? nil : "device has no LiDAR: ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) is false",
                                appVersion: appVersion, appBuild: appBuild)
        d.coordinateFrames = [CoordinateFrame(id: 0, startedAt: d.startedAt, startedAtTimestampS: nil, reason: "session_start")]
        let dir = try store.create(d)
        doc = d
        writer = SessionWriter(directory: dir)
        super.init()
        let room = scan.startRoom(label: "Room 1", at: nil, time: nil)
        doc.rooms = scan.rooms
        try store.save(doc)
        event(EventType.sessionStart, roomId: room.id)
    }

    /// Continue a scan that was interrupted (app closed/crashed). Starts a new coordinate frame: the old ARKit world
    /// cannot be recovered after a restart, and frames from the two are never mixed silently.
    init(resuming id: String, store: SessionStore, caps: DeviceCapabilities) throws {
        self.caps = caps; self.store = store
        doc = try store.load(id)
        settings = doc.settings
        selector = KeyframeSelector(thresholds: doc.settings.keyframe)
        let dir = store.directory(for: id)
        writer = SessionWriter(directory: dir)
        super.init()
        let frames = try JSONLines.read(FrameRecord.self, from: dir.appendingPathComponent(NammaCapFormat.framesPath))
        frameIndex = frames.items.map(\.index).max() ?? 0
        candidateIndex = JSONLines.countCompleteLines(dir.appendingPathComponent(NammaCapFormat.rejectionsPath)) + frameIndex
        eventSeq = (try? JSONLines.read(ScanEvent.self, from: dir.appendingPathComponent(NammaCapFormat.eventsPath)).items.map(\.seq).max()) ?? 0
        annotations = (try? NammaJSON.decoder().decode(AnnotationsDocument.self,
                        from: Data(contentsOf: dir.appendingPathComponent(NammaCapFormat.annotationsPath))).annotations) ?? []
        coordFrame = (doc.coordinateFrames.map(\.id).max() ?? -1) + 1
        doc.coordinateFrames.append(CoordinateFrame(id: coordFrame, startedAt: ISOTime.string(), startedAtTimestampS: nil,
                                                    reason: "resumed_after_interruption"))
        doc.state = "capturing"
        // Rooms keep their ids (room_1, room_2, ... are recreated in order); capture continues in the last one.
        var restored = ScanState()
        for r in doc.rooms { restored.startRoom(label: r.label, at: nil, time: nil, wallclock: r.createdAt) }
        if restored.rooms.isEmpty { restored.startRoom(label: "Room 1", at: nil, time: nil) }
        if !caps.configurableDevice { doc.settings.cameraControls.append(contentsOf: CameraControls.unavailableAtStart()) }
        scan = restored
        doc.rooms = scan.rooms
        try store.save(doc)
        event(EventType.coordinateFrameStarted, data: ["reason": "resumed_after_interruption"])
    }

    var sessionDirectory: URL { store.directory(for: doc.id) }

    // MARK: session control (any thread)

    func attach(_ session: ARSession) {
        queue.async {
            self.session = session
            session.delegate = self
            session.delegateQueue = self.queue
            self.run(reset: false)
        }
    }

    private func configuration() -> ARWorldTrackingConfiguration {
        let c = ARWorldTrackingConfiguration()
        if let f = caps.highResFormat { c.videoFormat = f }
        c.videoHDRAllowed = false
        c.planeDetection = [.horizontal, .vertical]
        c.environmentTexturing = .none
        if caps.sceneDepth { c.frameSemantics.insert(.sceneDepth) }
        // LiDAR mesh: saved with the scan (Namma Space's mesh route works from a scanned mesh) and drawable on screen
        if caps.sceneReconstruction { c.sceneReconstruction = .mesh }
        return c
    }

    private func run(reset: Bool) {
        guard let s = session else { return }
        s.run(configuration(), options: reset ? [.resetTracking, .removeExistingAnchors] : [])
        if reset { startDate = Date() }
        // running a configuration may reconfigure the camera: lock again once exposure has settled
        lockApplied = false; lockAttempts = 0; normalSince = nil
    }

    func setAutoCapture(_ on: Bool) { queue.async { self.autoCapture = on; self.publish(force: true) } }

    func manualCapture(override: Bool) { queue.async { self.forceNext = (override ? "manual_override" : "manual", nil) } }

    func resetTracking() {
        queue.async {
            self.coordFrame += 1
            self.doc.coordinateFrames.append(CoordinateFrame(id: self.coordFrame, startedAt: ISOTime.string(),
                                                             startedAtTimestampS: nil, reason: "user_reset_tracking"))
            self.motion.reset(); self.lastPose = nil; self.lastThumb = nil
            self.saveDoc()
            self.event(EventType.coordinateFrameStarted, data: ["reason": "user_reset_tracking"])
            self.run(reset: true)
        }
    }

    /// User toggle. Locking is automatic by default; turning it off is recorded and respected.
    func setExposureLock(_ lock: Bool) {
        queue.async {
            self.autoLockWanted = lock
            self.applyLock(lock, reason: lock ? "user requested lock" : "user turned the lock off")
        }
    }

    private func recordControls(_ results: [CameraControlResult]) {
        doc.settings.cameraControls.append(contentsOf: results)
        saveDoc()
        for r in results {
            event(EventType.cameraControl, data: ["control": r.control, "outcome": r.outcome, "detail": r.detail ?? ""])
        }
        snapshot.exposureControls = results
        updateLockLabel()
    }

    private func applyLock(_ lock: Bool, reason: String) {
        let results = CameraControls.setLocked(lock, reason: reason)
        recordControls(results)
        lockApplied = lock && results.first { $0.control == "exposure_lock" }?.outcome == "locked"
        lastLockCheckT = lastPose?.1 ?? -1
        publish(force: true)
    }

    private func updateLockLabel() {
        let latest: [String: String] = Dictionary(doc.settings.cameraControls.map { ($0.control, $0.outcome) }, uniquingKeysWith: { _, b in b })
        let e = latest["exposure_lock"], w = latest["white_balance_lock"]
        switch (e, w) {
        case ("locked", "locked"): snapshot.cameraLock = "Exposure & colour locked"; snapshot.cameraLockOK = true
        case ("locked", _): snapshot.cameraLock = "Exposure locked, colour auto"; snapshot.cameraLockOK = false
        case ("unavailable", _): snapshot.cameraLock = "Exposure auto: lock not available on this phone"; snapshot.cameraLockOK = false
        case ("failed", _): snapshot.cameraLock = "Exposure auto: lock failed"; snapshot.cameraLockOK = false
        case ("reverted", _): snapshot.cameraLock = "Exposure auto: lock was undone, retrying"; snapshot.cameraLockOK = false
        case ("disabled", _), ("not_requested", _): snapshot.cameraLock = "Exposure auto (lock off)"; snapshot.cameraLockOK = false
        default: snapshot.cameraLock = "Exposure: waiting to lock"; snapshot.cameraLockOK = false
        }
    }

    /// Called every frame: lock exposure + white balance automatically once tracking has been normal for 2 s and the
    /// camera has stopped adjusting; then check every 5 s that the lock still holds (ARKit may reconfigure the camera).
    private func maintainCameraLock(_ t: Double) {
        guard caps.configurableDevice, autoLockWanted, !interrupted else { return }
        if !lockApplied {
            guard let ns = normalSince, t - ns >= 2, lockAttempts < 3, lockReverts <= 3 else { return }
            if CameraControls.isAdjusting && t - ns < 6 { return }          // still converging; give it up to 6 s
            lockAttempts += 1
            applyLock(true, reason: lockReverts > 0 ? "re-applied after the lock was undone" : "automatic after exposure settled")
            return
        }
        guard t - lastLockCheckT >= 5 else { return }
        lastLockCheckT = t
        guard let rb = CameraControls.readBack(), !(rb.exposureLocked && rb.whiteBalanceLocked) else { return }
        lockReverts += 1
        let now = ISOTime.string()
        var r: [CameraControlResult] = []
        if !rb.exposureLocked { r.append(CameraControlResult(control: "exposure_lock", outcome: "reverted", detail: "device reports auto exposure again (check \(lockReverts))", at: now)) }
        if !rb.whiteBalanceLocked { r.append(CameraControlResult(control: "white_balance_lock", outcome: "reverted", detail: "device reports auto white balance again (check \(lockReverts))", at: now)) }
        if lockReverts > 3 {
            r.append(CameraControlResult(control: "exposure_lock", outcome: "failed", detail: "the lock was undone more than 3 times; left on auto", at: now))
            warn("Exposure lock keeps being undone on this phone; exposure is automatic for the rest of the scan.")
        }
        recordControls(r)
        lockApplied = false; lockAttempts = 0; normalSince = t
        publish(force: true)
    }

    /// Unlock and let auto-exposure settle again; maintainCameraLock re-locks ~2 s later. Used when the light changes
    /// (new room, or several photos in a row rejected as too dark / too bright under a lock).
    private func remeter(_ why: String, t: Double?) {
        guard caps.configurableDevice, autoLockWanted, lockApplied else { return }
        applyLock(false, reason: "re-metering: \(why)")
        lockApplied = false; lockAttempts = 0; normalSince = t
        exposureRejectsInARow = 0
    }

    func dismissNewArea() {
        queue.async {
            guard let (pose, _) = self.lastPose else { return }
            self.scan.dismissNewArea(at: pose.position)
            self.event(EventType.warning, position: pose.position, data: ["kind": "new_area_prompt_dismissed"])
            self.publish(force: true)
        }
    }

    /// All coverage summaries (every room / tracking segment), for the finish screen.
    func coverageForReview(_ done: @escaping ([CoverageSummary], [Room]) -> Void) {
        queue.async {
            let all = self.coverage.keys.sorted().compactMap { k in self.coverage[k]?.summary(roomId: String(k.split(separator: "#").first ?? "")) }
            let rooms = self.scan.rooms
            DispatchQueue.main.async { done(all, rooms) }
        }
    }

    func thermalChanged(_ s: ProcessInfo.ThermalState) {
        queue.async {
            self.thermal = s
            self.selector.thresholds.minIntervalS = s.rawValue >= ProcessInfo.ThermalState.serious.rawValue
                ? self.settings.keyframe.minIntervalS * 2.5 : self.settings.keyframe.minIntervalS
            if s == .critical { self.autoCapture = false; self.warn("Device overheating: automatic capture paused") }
            self.event(EventType.warning, data: ["thermal_state": "\(s.rawValue)"])
        }
    }

    // MARK: rooms, doorways, loop-backs, annotations, scale

    func markDoorway(nextRoom: String?) {
        queue.async {
            guard let (pose, t) = self.lastPose else { return }
            let from = self.scan.currentRoomId
            let d = self.scan.markDoorway(pose: pose, time: t, coordinateFrame: self.coordFrame, nextRoomLabel: nextRoom)
            self.doc.rooms = self.scan.rooms
            self.saveDoc()
            if let to = d.toRoomId {
                self.event(EventType.roomStart, roomId: to, position: pose.position, t: t,
                           data: ["label": self.scan.currentRoom?.label ?? "", "via_doorway": d.id])
            }
            self.event(EventType.doorway, roomId: self.scan.currentRoomId, position: pose.position, t: t,
                       data: ["doorway_id": d.id, "from_room": from ?? "", "to_room": d.toRoomId ?? "",
                              "forward": "\(pose.forward.x),\(pose.forward.y),\(pose.forward.z)"])
            if nextRoom != nil { self.remeter("new room", t: t) }
            self.forceNext = ("doorway", nil)        // photograph the doorway itself
            self.writeCoverage()
            self.publish(force: true)
        }
    }

    func renameRoom(_ id: String, to label: String) {
        queue.async {
            guard self.scan.renameRoom(id, to: label) else { return }
            self.doc.rooms = self.scan.rooms
            self.saveDoc()
            self.event(EventType.roomRenamed, roomId: id, data: ["label": label])
            self.publish(force: true)
        }
    }

    func markLoopBack() {
        queue.async {
            guard let (pose, t) = self.lastPose else { return }
            let cands = self.scan.loopCandidates(near: pose, time: t, coordinateFrame: self.coordFrame)
            self.scan.loopBackMarked()
            self.event(EventType.loopBack, roomId: self.scan.currentRoomId, position: pose.position, t: t,
                       data: ["candidate_frames": cands.joined(separator: ","),
                              "note": cands.isEmpty ? "no earlier keyframe within radius in this coordinate frame" : ""])
            self.snapshot.lastLoopCandidates = cands.count
            self.forceNext = ("loop_back", nil)
            self.publish(force: true)
        }
    }

    /// `imagePoint` normalized (0...1) in the sensor-landscape image; world point from a raycast if one hit.
    func annotate(kind: String, imagePoint: CGPoint, worldPoint: SIMD3<Float>?, method: String?, note: String = "") {
        queue.async {
            self.forceNext = ("annotation", PendingAnnotation(kind: kind, imagePoint: imagePoint, worldPoint: worldPoint, method: method, note: note))
        }
    }

    func updateAnnotations(_ list: [SurfaceAnnotation]) {
        queue.async {
            self.annotations = list
            self.io { try? await $0.writeAnnotations(AnnotationsDocument(annotations: list)) }
            self.snapshot.annotations = list.count
            self.publish(force: true)
        }
    }

    func currentAnnotations(_ done: @escaping ([SurfaceAnnotation]) -> Void) {
        queue.async { let a = self.annotations; DispatchQueue.main.async { done(a) } }
    }

    func addScaleValidation(a: SIMD3<Float>, b: SIMD3<Float>, trueMeters: Double) {
        queue.async {
            let pa = SIMD3<Double>(a), pb = SIMD3<Double>(b)
            let v = ScaleValidation(id: "scale_\(self.doc.scaleValidations.count + 1)", at: ISOTime.string(),
                                    coordinateFrameId: self.coordFrame, pointA: [pa.x, pa.y, pa.z], pointB: [pb.x, pb.y, pb.z],
                                    measuredMeters: simd_distance(pa, pb), trueMeters: trueMeters,
                                    method: "two_raycast_points_vs_user_tape_measure")
            self.doc.scaleValidations.append(v)
            self.saveDoc()
            self.event(EventType.scaleValidation, data: ["measured_m": String(format: "%.4f", v.measuredMeters),
                                                         "true_m": String(format: "%.4f", trueMeters),
                                                         "ratio": String(format: "%.4f", v.ratio)])
        }
    }

    // MARK: finish

    func finish(_ done: @escaping (Result<URL, Error>) -> Void) {
        queue.async {
            self.finished = true
            self.saveSceneMesh()
            self.session?.pause()
            self.finishWhenIdle(done, attempts: 0)
        }
    }

    /// Wait (on the capture queue, without blocking it) until every keyframe write has finished, then close the session.
    private func finishWhenIdle(_ done: @escaping (Result<URL, Error>) -> Void, attempts: Int) {
        if (pending > 0 || captureInFlight) && attempts < 200 {
            queue.asyncAfter(deadline: .now() + 0.05) { self.finishWhenIdle(done, attempts: attempts + 1) }
            return
        }
        if pending > 0 { warn("\(pending) photo(s) were still being saved when the scan was finished") }
        if !doc.settings.cameraControls.contains(where: { $0.control == "exposure_lock" }) {
            recordControls([CameraControlResult(control: "exposure_lock", outcome: "not_requested",
                                                detail: "scan ended before tracking was normal long enough to lock", at: ISOTime.string()),
                            CameraControlResult(control: "white_balance_lock", outcome: "not_requested",
                                                detail: "scan ended before tracking was normal long enough to lock", at: ISOTime.string()),
                            CameraControlResult(control: "focus_lock", outcome: "not_requested", detail: CameraControls.focusPolicy, at: ISOTime.string())])
        }
        writeCoverage()
        event(EventType.sessionEnd)
        doc.state = "finished"
        doc.endedAt = ISOTime.string()
        saveDoc()
        // runs after every write queued above (the io chain is strictly ordered)
        let dir = sessionDirectory
        io { w in
            await w.close()
            DispatchQueue.main.async { done(.success(dir)) }
        }
    }

    // MARK: ARSessionDelegate (capture queue)

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        if finished { return }
        let t = frame.timestamp
        let cam = frame.camera
        let pose = Pose(cam.transform)
        let tq = FrameEncoding.trackingQuality(cam.trackingState)
        let ts = FrameEncoding.trackingStrings(cam.trackingState)

        if ts != lastTracking {
            if lastTracking.1 == "relocalizing" && ts.0 == "normal" { relocalizedAt = t }
            lastTracking = ts
            event(EventType.trackingChanged, t: t, data: ["state": ts.0, "reason": ts.1 ?? ""])
        }
        if tq == .normal, let (lp, lt) = lastPose, t - lt < 0.2, lp.distance(to: pose) > 0.5 {
            event(EventType.warning, position: pose.position, t: t,
                  data: ["kind": "pose_discontinuity", "jump_m": String(format: "%.3f", lp.distance(to: pose))])
        }
        if tq != .notAvailable {
            motion.update(pose: pose, time: t)
            spin.update(pose: pose, time: t)
            lastPose = (pose, t)
            scan.updatePose(pose, time: t, coordinateFrame: coordFrame)
        }
        if tq == .normal { if normalSince == nil { normalSince = t } } else { normalSince = nil }
        maintainCameraLock(t)

        // trajectory (5 Hz), recorded in every tracking state so gaps are visible
        if t - lastTrajectoryT >= 1.0 / settings.trajectorySampleHz {
            lastTrajectoryT = t
            let p = pose.position
            let s = TrajectorySample(timestampS: t, coordinateFrameId: coordFrame, position: [p.x, p.y, p.z],
                                     quaternionXYZW: pose.quaternionXYZW, tracking: ts.0, trackingReason: ts.1)
            io { try? await $0.writeTrajectory(s) }
        }

        if tq == .normal && t - lastCoverageT >= 0.33 {
            lastCoverageT = t
            feedCoverage(frame, pose: pose)
        }
        if t - lastStorageCheckT >= 10 {
            lastStorageCheckT = t
            checkStorage()
        }

        considerKeyframe(frame, pose: pose, tracking: tq, reason: ts.1)
        if t - lastUIT >= 0.2 { lastUIT = t; publish(force: false) }
    }

    func session(_ session: ARSession, didFailWithError error: Error) {
        warn("AR session failed: \(error.localizedDescription)")
        event(EventType.warning, data: ["kind": "session_failed", "error": error.localizedDescription])
        publish(force: true)
    }

    func sessionWasInterrupted(_ session: ARSession) {
        interrupted = true
        event(EventType.interruptionBegan)
        publish(force: true)
    }

    func sessionInterruptionEnded(_ session: ARSession) {
        interrupted = false
        event(EventType.interruptionEnded, data: ["relocalization": "attempted"])
        publish(force: true)
    }

    func sessionShouldAttemptRelocalization(_ session: ARSession) -> Bool { true }

    // MARK: keyframes

    private func considerKeyframe(_ frame: ARFrame, pose: Pose, tracking: TrackingQuality, reason: String?) {
        guard !captureInFlight, !interrupted else { return }
        let t = frame.timestamp
        let forced = forceNext
        var decision: CandidateDecision
        if let f = forced {
            decision = CandidateDecision(isCandidate: true, reason: f.trigger == "manual_override" ? "manual" : f.trigger,
                                         translationM: selector.lastAccepted.map { $0.pose.distance(to: pose) },
                                         rotationDeg: selector.lastAccepted.map { $0.pose.rotationDegrees(to: pose) }, lowOverlap: false)
        } else {
            guard autoCapture, !storageCritical, tracking != .notAvailable else { return }
            decision = selector.evaluate(pose: pose, time: t, coordinateFrame: coordFrame, staleAfterS: settings.quality.staleAfterS)
            guard decision.isCandidate else { return }
        }
        selector.markCandidate(time: t)
        candidateIndex += 1
        lastLowOverlap = decision.lowOverlap

        guard let luma = FrameEncoding.luma(frame.capturedImage) else { return }
        var m = QualityMetrics.measure(luma)
        m.angularSpeedDegPerS = motion.angularDegPerS
        m.linearSpeedMPerS = motion.linearMPerS
        let thumb = luma.thumbnail()
        if let lt = lastThumb, forced == nil { m.differenceFromLastKeyframe = QualityMetrics.thumbnailDifference(lt, thumb) }
        let trigger = forced?.trigger ?? "auto"
        let override = forced != nil            // any user-requested photo is kept unless tracking is gone
        let gate = QualityGate.evaluate(GateInput(metrics: m, tracking: tracking, trackingReason: reason,
                                                  secondsSinceLastAccept: selector.secondsSinceAccept(t), override: override),
                                        settings.quality)
        if !gate.accept {
            if gate.rejectReasons.contains("underexposed") || gate.rejectReasons.contains("overexposed") {
                exposureRejectsInARow += 1
                if exposureRejectsInARow >= 3 { remeter("3 photos in a row too dark or too bright under the lock", t: t) }
            } else { exposureRejectsInARow = 0 }
            snapshot.rejected += 1
            snapshot.lastRejectReasons = gate.rejectReasons
            let r = RejectionRecord(candidateIndex: candidateIndex, timestampS: t, coordinateFrameId: coordFrame,
                                    trigger: trigger, reasons: gate.rejectReasons, metrics: m)
            io { try? await $0.writeRejection(r) }
            if forced != nil {
                forceNext = nil
                warn("Photo not taken: \(gate.rejectReasons.joined(separator: ", "))")
            }
            return
        }
        guard pending < Self.maxPendingWrites else {
            // storage is behind: record it, do not grow memory
            let r = RejectionRecord(candidateIndex: candidateIndex, timestampS: t, coordinateFrameId: coordFrame,
                                    trigger: trigger, reasons: ["writer_backpressure"], metrics: m)
            io { try? await $0.writeRejection(r) }
            snapshot.rejected += 1
            return
        }
        forceNext = nil
        exposureRejectsInARow = 0
        snapshot.lastRejectReasons = []
        captureKeyframe(streamFrame: frame, trigger: trigger, decision: decision, metrics: m, gate: gate, thumb: thumb,
                        annotation: forced?.annotation)
    }

    private func captureKeyframe(streamFrame: ARFrame, trigger: String, decision: CandidateDecision, metrics: QualityMetrics,
                                 gate: GateDecision, thumb: [UInt8], annotation: PendingAnnotation?) {
        captureInFlight = true
        frameIndex += 1
        let index = frameIndex
        let fid = String(format: "f%06d", index)
        // Everything needed from the stream frame is copied now; the ARFrame itself is not retained.
        let streamImage = streamFrame.capturedImage
        let streamCamera = streamFrame.camera
        let streamT = streamFrame.timestamp
        let streamMapping = streamFrame.worldMappingStatus
        let streamExposure = FrameEncoding.exposure(streamFrame)
        let streamDepth = streamFrame.sceneDepth.flatMap(FrameEncoding.copyDepth)

        let storeStream: ([String]) -> Void = { flags in
            self.storeKeyframe(fid: fid, index: index, image: streamImage, camera: streamCamera, t: streamT,
                               mapping: streamMapping, exposure: streamExposure, source: "video_stream",
                               depth: streamDepth.map { ($0, streamCamera, streamT, "same_frame") },
                               trigger: trigger, decision: decision, metrics: metrics, gate: gate, thumb: thumb,
                               extraFlags: flags, annotation: annotation)
        }
        guard caps.highResFormat != nil, !hiResFallback, let s = session else {
            storeStream(hiResFallback ? ["stream_fallback_after_repeated_high_resolution_failures"] : [])
            return
        }
        // High-resolution still: one retry; if both fail the candidate is skipped (recorded as a rejection) instead of
        // mixing a lower-resolution frame into the photo set. Only after repeated failures in a row does the scan switch
        // to video-stream photos, with a warning in the manifest and on screen.
        func attempt(_ n: Int) {
            s.captureHighResolutionFrame { hi, err in
                self.queue.async {
                    if let hi {
                        self.hiResFailuresInARow = 0
                        let hiDepth = hi.sceneDepth.flatMap(FrameEncoding.copyDepth)
                        self.storeKeyframe(fid: fid, index: index, image: hi.capturedImage, camera: hi.camera, t: hi.timestamp,
                                           mapping: hi.worldMappingStatus, exposure: FrameEncoding.exposure(hi),
                                           source: "high_resolution_capture",
                                           depth: hiDepth.map { ($0, hi.camera, hi.timestamp, "same_frame") },
                                           trigger: trigger, decision: decision, metrics: metrics, gate: gate, thumb: thumb,
                                           extraFlags: n > 0 ? ["high_resolution_capture_retried"] : [], annotation: annotation)
                    } else if n == 0, !self.finished {
                        self.queue.asyncAfter(deadline: .now() + 0.15) { attempt(1) }
                    } else {
                        self.highResFailed(err?.localizedDescription ?? "no frame", t: streamT, trigger: trigger, metrics: metrics,
                                           annotation: annotation, storeStream: storeStream)
                    }
                }
            }
        }
        attempt(0)
    }

    private func highResFailed(_ msg: String, t: Double, trigger: String, metrics: QualityMetrics,
                               annotation: PendingAnnotation?, storeStream: ([String]) -> Void) {
        hiResFailuresInARow += 1
        if hiResFailuresInARow >= Self.hiResFailuresBeforeFallback {
            hiResFallback = true
            snapshot.hiResFallback = true
            warn("High-resolution photos failed \(hiResFailuresInARow) times in a row; the rest of this scan uses video-resolution photos.")
            event(EventType.warning, t: t, data: ["kind": "high_resolution_unavailable", "error": msg,
                                                  "failures_in_a_row": "\(hiResFailuresInARow)"])
            storeStream(["high_resolution_capture_failed: \(msg)", "stream_fallback_after_repeated_high_resolution_failures"])
            return
        }
        let r = RejectionRecord(candidateIndex: candidateIndex, timestampS: t, coordinateFrameId: coordFrame, trigger: trigger,
                                reasons: ["high_resolution_capture_failed"], metrics: metrics)
        io { try? await $0.writeRejection(r) }
        snapshot.rejected += 1
        captureInFlight = false
        // a user-requested photo (annotation, doorway, manual) is tried again on the next frame
        if trigger != "auto" { forceNext = (trigger, annotation) }
        publish(force: true)
    }

    private func storeKeyframe(fid: String, index: Int, image: CVPixelBuffer, camera: ARCamera, t: TimeInterval,
                               mapping: ARFrame.WorldMappingStatus, exposure: ExposureRecord, source: String,
                               depth: (FrameEncoding.DepthCopy, ARCamera, TimeInterval, String)?,
                               trigger: String, decision: CandidateDecision, metrics: QualityMetrics, gate: GateDecision,
                               thumb: [UInt8], extraFlags: [String], annotation: PendingAnnotation?) {
        var metrics = metrics
        var gate = gate
        var thumb = thumb
        if ProcessInfo.processInfo.arguments.contains("-NammaSavedStillGate") {
            let tracking = FrameEncoding.trackingQuality(camera.trackingState)
            var reasons: [String] = []
            if let luma = FrameEncoding.luma(image) {
                metrics = QualityMetrics.measure(luma)
                metrics.angularSpeedDegPerS = motion.angularDegPerS
                metrics.linearSpeedMPerS = motion.linearMPerS
                thumb = luma.thumbnail()
                if let previous = lastThumb {
                    metrics.differenceFromLastKeyframe = QualityMetrics.thumbnailDifference(previous, thumb)
                }
                gate = QualityGate.evaluate(GateInput(metrics: metrics, tracking: tracking,
                    trackingReason: FrameEncoding.trackingStrings(camera.trackingState).1,
                    secondsSinceLastAccept: selector.secondsSinceAccept(t), override: false), settings.quality)
                reasons = gate.rejectReasons
            } else {
                reasons = ["still_luma_unavailable"]
            }
            if let r = relocalizedAt, t - r < 3 { reasons.append("shortly_after_relocalization") }
            if !reasons.isEmpty {
                let rejection = RejectionRecord(candidateIndex: candidateIndex, timestampS: t,
                    coordinateFrameId: coordFrame, trigger: trigger, reasons: reasons, metrics: metrics)
                io { try? await $0.writeRejection(rejection) }
                snapshot.rejected += 1
                snapshot.lastRejectReasons = reasons
                captureInFlight = false
                if trigger != "auto" { warn("Requested photo rejected by saved-still quality gate.") }
                publish(force: true)
                return
            }
        }
        let pose = Pose(camera.transform)
        let kSource = FrameEncoding.intrinsics(camera)
        let stored = FrameEncoding.storedSize(width: kSource.width, height: kSource.height)
        let k = kSource.scaled(toWidth: stored.0, height: stored.1)
        var flags = extraFlags
        if stored.0 != kSource.width { flags.append("downscaled_from_\(kSource.width)x\(kSource.height)") }
        if gate.relaxed { flags.append("relaxed_gate") }
        if gate.overridden { flags.append("quality_override") }
        if let r = relocalizedAt, t - r < 3 { flags.append("shortly_after_relocalization") }
        var dref: DepthRef? = nil
        if let (dc, dcam, dt, pairing) = depth {
            dref = DepthRef(path: "depth/\(fid).depth.f32", confidencePath: dc.confidence == nil ? nil : "depth/\(fid).conf.u8",
                            width: dc.width, height: dc.height, source: "arkit_scene_depth",
                            intrinsics: FrameEncoding.intrinsics(dcam).scaled(toWidth: dc.width, height: dc.height),
                            timestampS: dt, tWorldFromCamera: Pose(dcam.transform).rowMajor, pairing: pairing,
                            validSampleFraction: dc.validFraction)
        } else if caps.sceneDepth {
            flags.append("depth_missing_on_lidar_device")
        }
        var euler = [Double(camera.eulerAngles.x), Double(camera.eulerAngles.y), Double(camera.eulerAngles.z)]
        if !euler.allSatisfy(\.isFinite) { euler = [0, 0, 0] }
        let rec = FrameRecord(
            frameId: fid, index: index, timestampS: t, wallclock: ISOTime.string(), coordinateFrameId: coordFrame,
            roomId: scan.currentRoomId,
            image: ImageRef(path: "images/\(fid).jpg", width: k.width, height: k.height, source: source,
                            interfaceOrientation: "portrait"),
            camera: CameraRecord(tWorldFromCamera: pose.rowMajor, intrinsics: k, eulerAngles: euler),
            tracking: FrameEncoding.tracking(camera, mapping: mapping), exposure: exposure,
            quality: QualityRecord(metrics: metrics, warnings: gate.warnings, overridden: gate.overridden),
            selection: SelectionRecord(trigger: trigger, reason: decision.reason, translationSinceLastM: decision.translationM,
                                       rotationSinceLastDeg: decision.rotationDeg),
            depth: dref, flags: flags)

        // brightness jump: record for downstream colour correction (exposure is not locked on every device)
        if let b = exposure.brightnessValue {
            if let lb = lastBrightness, abs(b - lb) > 1.5 {
                event(EventType.brightnessJump, frameId: fid, data: ["from": String(format: "%.2f", lb), "to": String(format: "%.2f", b)])
            }
            lastBrightness = b
        }

        pending += 1
        let quality = jpegQuality
        let w = writer
        let depthData = depth?.0
        let acceptedThumb = thumb
        let model = caps.modelIdentifier
        encodeQueue.async {
            let jpeg = FrameEncoding.jpeg(image, quality: quality, size: stored, intrinsics: k, model: model, exposure: exposure)
            Task {
                var ok = false
                var message = ""
                await w.beginWrite()
                if let jpeg {
                    do {
                        try await w.writeKeyframe(rec, image: jpeg, depth: depthData?.depth, confidence: depthData?.confidence)
                        ok = true
                    } catch { message = "\(error)" }
                } else {
                    await w.cancelWrite()
                    message = "JPEG encoding failed"
                }
                self.queue.async { self.keyframeWritten(rec, ok: ok, message: message, thumb: acceptedThumb, pose: pose, annotation: annotation) }
            }
        }
        // a new candidate may be considered while this one is encoding; the pending limit bounds memory
        captureInFlight = false
        selector.markAccepted(pose: pose, time: t, coordinateFrame: coordFrame, reason: decision.reason)
        lastThumb = thumb
    }

    private func keyframeWritten(_ rec: FrameRecord, ok: Bool, message: String, thumb: [UInt8], pose: Pose, annotation: PendingAnnotation?) {
        pending -= 1
        guard ok else {
            warn("Could not save photo \(rec.frameId): \(message)")
            event(EventType.warning, frameId: rec.frameId, data: ["kind": "keyframe_write_failed", "error": message])
            publish(force: true)
            return
        }
        snapshot.accepted += 1
        if rec.depth != nil { snapshot.depthFrames += 1 }
        if rec.image.source == "high_resolution_capture" { snapshot.highResFrames += 1 }
        snapshot.lastAcceptedAt = Date()
        if let d = scan.keyframeAccepted(frameId: rec.frameId, pose: pose, time: rec.timestampS, coordinateFrame: rec.coordinateFrameId) {
            event(EventType.doorwayLookBackCaptured, roomId: scan.currentRoomId, frameId: rec.frameId, data: ["doorway_id": d.id])
        }
        if let a = annotation {
            let wp = a.worldPoint.map { [Double($0.x), Double($0.y), Double($0.z)] }
            let ann = SurfaceAnnotation(id: "ann_\(annotations.count + 1)_\(rec.frameId)", kind: a.kind, frameId: rec.frameId,
                                        imagePointNormalized: [Double(a.imagePoint.x), Double(a.imagePoint.y)],
                                        coordinateFrameId: rec.coordinateFrameId, tWorldFromCamera: rec.camera.tWorldFromCamera,
                                        worldPoint: wp?.allSatisfy(\.isFinite) == true ? wp : nil,
                                        worldPointMethod: wp == nil ? nil : a.method, roomId: rec.roomId, note: a.note,
                                        createdAt: ISOTime.string(), updatedAt: ISOTime.string())
            annotations.append(ann)
            let list = annotations
            io { try? await $0.writeAnnotations(AnnotationsDocument(annotations: list)) }
            snapshot.annotations = annotations.count
        }
        publish(force: true)
    }

    // MARK: LiDAR scene mesh

    /// Writes ARKit's scene-reconstruction mesh (all ARMeshAnchors, world coordinates) as mesh/arkit_scene_mesh.glb.
    /// Only the current coordinate frame's mesh exists (ARKit drops anchors on a tracking reset); recorded in the event.
    private func saveSceneMesh() {
        guard caps.sceneReconstruction, let frame = session?.currentFrame else { return }
        let anchors = frame.anchors.compactMap { $0 as? ARMeshAnchor }
        guard !anchors.isEmpty else {
            event(EventType.warning, data: ["kind": "scene_mesh_empty"])
            return
        }
        var pos: [Float] = [], idx: [UInt32] = []
        for a in anchors {
            let g = a.geometry
            let base = UInt32(pos.count / 3)
            let vs = g.vertices
            guard vs.format == .float3, vs.componentsPerVector == 3 else { continue }
            let vb = vs.buffer.contents()
            pos.reserveCapacity(pos.count + vs.count * 3)
            for i in 0..<vs.count {
                let o = vs.offset + vs.stride * i
                let x = vb.load(fromByteOffset: o, as: Float.self), y = vb.load(fromByteOffset: o + 4, as: Float.self)
                let z = vb.load(fromByteOffset: o + 8, as: Float.self)
                let w = a.transform * SIMD4<Float>(x, y, z, 1)
                pos.append(w.x); pos.append(w.y); pos.append(w.z)
            }
            let f = g.faces
            guard f.indexCountPerPrimitive == 3 else { continue }
            let fb = f.buffer.contents()
            let n = f.count * 3
            for i in 0..<n {
                let v: UInt32 = f.bytesPerIndex == 4 ? fb.load(fromByteOffset: i * 4, as: UInt32.self)
                                                     : UInt32(fb.load(fromByteOffset: i * 2, as: UInt16.self))
                idx.append(base + v)
            }
        }
        do {
            let glb = try GLBWriter.data(positions: pos, indices: idx)
            let url = sessionDirectory.appendingPathComponent(NammaCapFormat.meshPath)
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try SessionStore.writeAtomically(glb, to: url)
            event("scene_mesh", data: ["kind": "saved", "vertices": "\(pos.count / 3)",
                                            "triangles": "\(idx.count / 3)", "anchors": "\(anchors.count)",
                                            "bytes": "\(glb.count)", "coordinate_frame_id": "\(coordFrame)"])
        } catch {
            warn("Could not save the LiDAR mesh: \(error)")
        }
    }

    // MARK: coverage

    private func coverageKey() -> String { "\(scan.currentRoomId ?? "none")#\(coordFrame)" }

    private func feedCoverage(_ frame: ARFrame, pose: Pose) {
        let key = coverageKey()
        var model = coverage[key] ?? CoverageModel()
        let camY = pose.position.y
        // floor / ceiling heights from ARKit's horizontal planes (estimates)
        var floorY: Double? = model.floorY, ceilY: Double? = model.ceilingY
        for case let p as ARPlaneAnchor in frame.anchors where p.alignment == .horizontal {
            let y = Double(p.transform.columns.3.y)
            if y < camY - 0.5 { floorY = min(floorY ?? y, y) }
            if y > camY + 0.3 { ceilY = max(ceilY ?? y, y) }
        }
        if let f = floorY { model.setFloor(y: f) }
        if let c = ceilY { model.setCeiling(y: c) }
        model.addCameraView(pose: pose)
        let camPos = pose.position
        if let depth = frame.sceneDepth {
            for p in FrameEncoding.depthSamples(depth, camera: frame.camera) {
                if let cls = model.classify(p, cameraY: camY) {
                    model.addObservation(point: p, surface: cls, from: camPos, source: .lidarDepth)
                }
            }
        } else if let s = session {
            // non-LiDAR: a sparse grid of raycasts against ARKit's detected planes (labelled as estimates)
            for gy in stride(from: 0.15, through: 0.85, by: 0.233) {
                for gx in stride(from: 0.15, through: 0.85, by: 0.175) {
                    let q = frame.raycastQuery(from: CGPoint(x: gx, y: gy), allowing: .existingPlaneGeometry, alignment: .any)
                    guard let hit = s.raycast(q).first else { continue }
                    let c = hit.worldTransform.columns.3
                    let p = SIMD3<Double>(Double(c.x), Double(c.y), Double(c.z))
                    if let cls = model.classify(p, cameraY: camY) {
                        model.addObservation(point: p, surface: cls, from: camPos, source: .planeEstimate)
                    }
                }
            }
        }
        coverage[key] = model
        let summary = model.summary(roomId: scan.currentRoomId)
        snapshot.coverage = summary
        snapshot.uncoveredHeadings = model.uncoveredHeadings()
        snapshot.uncoveredDown = model.uncoveredHeadings(band: 0)
        snapshot.uncoveredUp = model.uncoveredHeadings(band: 2)
        snapshot.directionsCovered = summary.directionsCovered.filter { $0 }.count
        snapshot.directionsTotal = summary.directionsCovered.count
        snapshot.headingNow = pose.headingDegrees
        if frame.timestamp - lastCoverageWriteT > 10 { lastCoverageWriteT = frame.timestamp; writeCoverage() }
    }

    private func writeCoverage() {
        let all = ScanCoverage(rooms: coverage.keys.sorted().compactMap { k in
            coverage[k]?.summary(roomId: String(k.split(separator: "#").first ?? ""))
        })
        io { try? await $0.writeCoverage(all) }
    }

    // MARK: storage

    private func checkStorage() {
        guard let free = SessionStore.availableBytes(at: sessionDirectory) else { return }
        let wasLow = storageLow
        storageLow = free < 1_000_000_000
        storageCritical = free < 300_000_000
        if storageLow && !wasLow { event(EventType.warning, data: ["kind": "storage_low", "free_bytes": "\(free)"]) }
        if storageCritical && autoCapture {
            autoCapture = false
            warn("Storage almost full: automatic capture stopped. Finish the scan.")
        }
    }

    // MARK: helpers

    private func saveDoc() {
        do { try store.save(doc) } catch { warn("Could not save scan details: \(error)") }
    }

    private func warn(_ s: String) {
        snapshot.errors.append(s)
        if snapshot.errors.count > 5 { snapshot.errors.removeFirst(snapshot.errors.count - 5) }
        if !doc.warnings.contains(s) { doc.warnings.append(s); saveDoc() }
    }

    private func event(_ type: String, roomId: String? = nil, position: SIMD3<Double>? = nil, t: Double? = nil,
                       frameId: String? = nil, data: [String: String] = [:]) {
        eventSeq += 1
        let e = ScanEvent(seq: eventSeq, type: type, timestampS: t ?? lastPose?.1, wallclock: ISOTime.string(),
                          coordinateFrameId: coordFrame, roomId: roomId ?? scan.currentRoomId,
                          position: position.map { [$0.x, $0.y, $0.z] }, frameId: frameId, data: data)
        io { try? await $0.writeEvent(e) }
    }

    /// Strictly ordered writes: each operation starts only after the previous one finished. Called on the capture
    /// queue only (so `ioTail` is never raced). Separate unstructured Tasks would not guarantee order.
    private func io(_ op: @escaping (SessionWriter) async -> Void) {
        let prev = ioTail
        let w = writer
        ioTail = Task {
            await prev?.value
            await op(w)
        }
    }

    private func publish(force: Bool) {
        snapshot.trackingState = lastTracking.0
        snapshot.trackingReason = lastTracking.1
        snapshot.autoCapture = autoCapture
        snapshot.interrupted = interrupted
        snapshot.roomLabel = scan.currentRoom?.label ?? ""
        snapshot.rooms = scan.rooms
        snapshot.coordinateFrames = coordFrame + 1 - (doc.coordinateFrames.first?.id ?? 0)
        snapshot.pendingWrites = pending
        snapshot.pendingLookBack = scan.pendingLookBack != nil
        snapshot.suggestLoopBack = scan.loopBackSuggested
        snapshot.elapsed = Date().timeIntervalSince(startDate)
        snapshot.spinning = spin.spinning
        snapshot.newAreaSuggested = scan.newAreaSuggested
        snapshot.scaleChecks = doc.scaleValidations.count
        if snapshot.exposureControls.isEmpty { updateLockLabel() }
        let tq: TrackingQuality = lastTracking.0 == "normal" ? .normal : (lastTracking.0 == "limited" ? .limited : .notAvailable)
        snapshot.guidance = GuidanceBuilder.build(
            tracking: tq, trackingReason: lastTracking.1, interrupted: interrupted,
            angularSpeed: motion.angularDegPerS, linearSpeed: motion.linearMPerS, thresholds: settings.quality,
            lastRejectReasons: snapshot.lastRejectReasons, lowOverlap: lastLowOverlap, coverage: snapshot.coverage,
            pendingLookBack: snapshot.pendingLookBack, suggestLoopBack: snapshot.suggestLoopBack, storageLow: storageLow,
            thermalSerious: thermal.rawValue >= ProcessInfo.ThermalState.serious.rawValue,
            relocalizedRecently: relocalizedAt.map { (lastPose?.1 ?? 0) - $0 < 5 } ?? false,
            spinning: spin.spinning, newAreaSuggested: scan.newAreaSuggested)
        let snap = snapshot
        let cb = onSnapshot
        DispatchQueue.main.async { cb?(snap) }
    }
}
