import Foundation
import simd

/// Keyframe policy. Photogrammetry needs parallax: photos taken while turning on the spot add little geometry, so a
/// pure turn only produces a keyframe at a large angle and only a few times in a row. Field evidence: the first real
/// capture (Testing Room, 2026-11-22, iPhone18,1) had 172 of 201 keyframes triggered by ~10 degree turns with 2-20 cm
/// of movement; 26 m walked inside a 0.8 x 0.7 x 3.8 m box.
public struct KeyframeThresholds: Codable, Sendable, Equatable {
    /// A new candidate is considered after this much camera translation since the last keyframe...
    public var minTranslationM: Double = 0.20
    /// ...or this much rotation, provided the camera also moved at least `rotationMinTranslationM` (default equal to
    /// minTranslationM: replaying the Testing Room trajectory, 10 cm gave 79 % turn-triggered keyframes, 20 cm gave 29 %)...
    public var minRotationDeg: Double = 10
    public var rotationMinTranslationM: Double = 0.20
    /// ...or, turning without moving, this much rotation, at most `maxConsecutiveRotationOnly` times in a row
    /// (keeps overlap when someone turns a corner on the spot; spinning in place stops producing photos).
    public var rotationOnlyMinDeg: Double = 30
    public var maxConsecutiveRotationOnly: Int = 2
    /// Movement is measured at an estimated body pivot this far behind the camera (horizontal view direction), not at
    /// the camera: turning on the spot with the phone held out swings the camera around a ~30 cm circle (the Testing
    /// Room walked 26 m that way) without adding useful parallax. Heuristic; 0 = measure at the camera (app 1.0).
    public var pivotArmM: Double = 0.30
    /// Above this rotation between consecutive keyframes, overlap is likely too low for matching: warn the user.
    public var lowOverlapRotationDeg: Double = 30
    /// Hard rate limit (the high-resolution capture takes a few hundred ms).
    public var minIntervalS: Double = 0.4

    public init() {}

    /// The policy of app version 1.0 (used for the Testing Room capture): any 10 degree turn made a keyframe.
    public static var v1_0: KeyframeThresholds {
        var t = KeyframeThresholds()
        t.rotationMinTranslationM = 0
        t.rotationOnlyMinDeg = t.minRotationDeg
        t.maxConsecutiveRotationOnly = .max
        t.pivotArmM = 0
        return t
    }

    enum CodingKeys: String, CodingKey {
        case minTranslationM = "min_translation_m", minRotationDeg = "min_rotation_deg",
             rotationMinTranslationM = "rotation_min_translation_m", rotationOnlyMinDeg = "rotation_only_min_deg",
             maxConsecutiveRotationOnly = "max_consecutive_rotation_only", pivotArmM = "pivot_arm_m",
             lowOverlapRotationDeg = "low_overlap_rotation_deg", minIntervalS = "min_interval_s"
    }

    /// Fields added after 1.0 are absent in sessions saved by app 1.0; those decode to the 1.0 behaviour they were
    /// captured with, so old sessions still load, resume and export.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        minTranslationM = try c.decode(Double.self, forKey: .minTranslationM)
        minRotationDeg = try c.decode(Double.self, forKey: .minRotationDeg)
        lowOverlapRotationDeg = try c.decode(Double.self, forKey: .lowOverlapRotationDeg)
        minIntervalS = try c.decode(Double.self, forKey: .minIntervalS)
        rotationMinTranslationM = try c.decodeIfPresent(Double.self, forKey: .rotationMinTranslationM) ?? 0
        rotationOnlyMinDeg = try c.decodeIfPresent(Double.self, forKey: .rotationOnlyMinDeg) ?? minRotationDeg
        let m = try c.decodeIfPresent(Int.self, forKey: .maxConsecutiveRotationOnly) ?? -1
        maxConsecutiveRotationOnly = m < 0 ? .max : m
        pivotArmM = try c.decodeIfPresent(Double.self, forKey: .pivotArmM) ?? 0
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(minTranslationM, forKey: .minTranslationM); try c.encode(minRotationDeg, forKey: .minRotationDeg)
        try c.encode(rotationMinTranslationM, forKey: .rotationMinTranslationM)
        try c.encode(rotationOnlyMinDeg, forKey: .rotationOnlyMinDeg)
        // "no limit" is written as -1 (Int.max does not survive every JSON reader)
        try c.encode(maxConsecutiveRotationOnly == .max ? -1 : maxConsecutiveRotationOnly, forKey: .maxConsecutiveRotationOnly)
        try c.encode(pivotArmM, forKey: .pivotArmM)
        try c.encode(lowOverlapRotationDeg, forKey: .lowOverlapRotationDeg); try c.encode(minIntervalS, forKey: .minIntervalS)
    }
}

public struct CandidateDecision: Sendable, Equatable {
    public var isCandidate: Bool
    /// "first_frame" | "translation" | "rotation" | "rotation_only" | "stale" | "none"
    public var reason: String
    /// camera translation since the last keyframe (recorded); decisions use the pivot movement
    public var translationM: Double?
    public var rotationDeg: Double?
    public var lowOverlap: Bool
    /// The view turned past `minRotationDeg` but no keyframe was taken because the camera has not moved: the user is
    /// turning on the spot.
    public var turnedWithoutMoving: Bool
    public init(isCandidate: Bool, reason: String, translationM: Double?, rotationDeg: Double?, lowOverlap: Bool,
                turnedWithoutMoving: Bool = false) {
        self.isCandidate = isCandidate; self.reason = reason; self.translationM = translationM
        self.rotationDeg = rotationDeg; self.lowOverlap = lowOverlap; self.turnedWithoutMoving = turnedWithoutMoving
    }
}

/// Decides when the current view has changed enough to be worth photographing. Quality is judged separately
/// (QualityGate); this only looks at viewpoint change and timing, per coordinate frame.
public struct KeyframeSelector: Sendable {
    public var thresholds: KeyframeThresholds
    public private(set) var lastAccepted: (pose: Pose, time: Double, frame: Int)?
    public private(set) var lastCandidateTime: Double?
    /// rotation-only keyframes accepted since the last keyframe that involved movement
    public private(set) var consecutiveRotationOnly = 0

    public init(thresholds: KeyframeThresholds = KeyframeThresholds()) { self.thresholds = thresholds }

    public func evaluate(pose: Pose, time: Double, coordinateFrame: Int, staleAfterS: Double) -> CandidateDecision {
        if let lc = lastCandidateTime, time - lc < thresholds.minIntervalS {
            return CandidateDecision(isCandidate: false, reason: "none", translationM: nil, rotationDeg: nil, lowOverlap: false)
        }
        guard let last = lastAccepted, last.frame == coordinateFrame else {
            return CandidateDecision(isCandidate: true, reason: "first_frame", translationM: nil, rotationDeg: nil, lowOverlap: false)
        }
        let t = thresholds
        let cam = last.pose.distance(to: pose)
        // body turn (phone held out): camera circles, pivot stays; wrist turn: camera stays, pivot swings;
        // walking: both move. The smaller of the two is the movement that adds parallax.
        let dt = min(cam, simd_distance(last.pose.pivot(armM: t.pivotArmM), pose.pivot(armM: t.pivotArmM)))
        let dr = last.pose.rotationDegrees(to: pose)
        let low = dr > t.lowOverlapRotationDeg
        func d(_ cand: Bool, _ reason: String, spin: Bool = false) -> CandidateDecision {
            CandidateDecision(isCandidate: cand, reason: reason, translationM: cam, rotationDeg: dr, lowOverlap: low,
                              turnedWithoutMoving: spin)
        }
        if dt >= t.minTranslationM { return d(true, "translation") }
        if dr >= t.minRotationDeg {
            if dt >= t.rotationMinTranslationM { return d(true, "rotation") }
            if dr >= t.rotationOnlyMinDeg && consecutiveRotationOnly < t.maxConsecutiveRotationOnly {
                return d(true, "rotation_only")
            }
            return d(false, "none", spin: true)
        }
        // nothing accepted for a while although the camera moved a little: try again so the stale-gate relaxation can
        // act. Movement only: turning on the spot never qualifies.
        if time - last.time >= staleAfterS && dt >= t.minTranslationM / 2 { return d(true, "stale") }
        return d(false, "none")
    }

    public mutating func markCandidate(time: Double) { lastCandidateTime = time }

    /// `reason` is the CandidateDecision.reason the keyframe was selected for.
    public mutating func markAccepted(pose: Pose, time: Double, coordinateFrame: Int, reason: String = "translation") {
        consecutiveRotationOnly = reason == "rotation_only" ? consecutiveRotationOnly + 1 : 0
        lastAccepted = (pose, time, coordinateFrame)
    }

    public func secondsSinceAccept(_ time: Double) -> Double? { lastAccepted.map { time - $0.time } }
}

/// Speeds from consecutive poses (smoothed), used by the quality gate and the "slow down" prompt.
public struct MotionEstimator: Sendable {
    private var last: (pose: Pose, time: Double)?
    public private(set) var angularDegPerS: Double?
    public private(set) var linearMPerS: Double?
    public var smoothing = 0.3

    public init() {}

    public mutating func update(pose: Pose, time: Double) {
        defer { last = (pose, time) }
        guard let l = last else { return }
        let dt = time - l.time
        guard dt > 1e-3, dt < 1.0 else { return }
        let a = l.pose.rotationDegrees(to: pose) / dt
        let v = l.pose.distance(to: pose) / dt
        angularDegPerS = angularDegPerS.map { $0 + smoothing * (a - $0) } ?? a
        linearMPerS = linearMPerS.map { $0 + smoothing * (v - $0) } ?? v
    }

    public mutating func reset() { last = nil; angularDegPerS = nil; linearMPerS = nil }
}

/// Detects turning on the spot: over the last `windowS` seconds the view turned at least `minTurnDeg` while the
/// camera's horizontal position moved less than `maxDisplacementM`. Drives the "Move, don't just turn" prompt.
public struct SpinDetector: Sendable {
    public var windowS = 4.0
    public var minTurnDeg = 90.0
    public var maxDisplacementM = 0.35
    public var pivotArmM = 0.30
    private var samples: [(t: Double, pose: Pose)] = []
    public private(set) var spinning = false

    public init() {}

    public mutating func update(pose: Pose, time: Double) {
        if let l = samples.last, time - l.t < 0.1 { return }      // ~10 Hz is enough
        samples.append((time, pose))
        while let f = samples.first, time - f.t > windowS { samples.removeFirst() }
        guard samples.count >= 3, let first = samples.first, time - first.t >= windowS * 0.75 else { spinning = false; return }
        var turn = 0.0
        for i in 1..<samples.count { turn += samples[i - 1].pose.rotationDegrees(to: samples[i].pose) }
        func flat(_ a: SIMD3<Double>, _ b: SIMD3<Double>) -> Double { simd_length(SIMD2(a.x - b.x, a.z - b.z)) }
        let moved = min(flat(first.pose.position, pose.position),
                        flat(first.pose.pivot(armM: pivotArmM), pose.pivot(armM: pivotArmM)))
        spinning = turn >= minTurnDeg && moved < maxDisplacementM
    }

    public mutating func reset() { samples.removeAll(); spinning = false }
}

/// Offline replay of a pose stream through the keyframe selector (no quality gate: every candidate is accepted).
/// Used to compare keyframe policies on recorded trajectories (e.g. a capture's trajectory.jsonl).
public enum KeyframeReplay {
    public struct Result: Sendable, Equatable {
        public var byReason: [String: Int]
        public var spinSamples: Int
        public var total: Int { byReason.values.reduce(0, +) }
        /// share of keyframes selected by turning ("rotation" or "rotation_only")
        public var rotationShare: Double {
            total == 0 ? 0 : Double((byReason["rotation"] ?? 0) + (byReason["rotation_only"] ?? 0)) / Double(total)
        }
    }

    public static func run(_ samples: [(time: Double, pose: Pose)], thresholds: KeyframeThresholds,
                           staleAfterS: Double = QualityThresholds().staleAfterS) -> Result {
        var s = KeyframeSelector(thresholds: thresholds)
        var counts: [String: Int] = [:]
        var spin = 0
        for (t, p) in samples {
            let d = s.evaluate(pose: p, time: t, coordinateFrame: 0, staleAfterS: staleAfterS)
            if d.turnedWithoutMoving { spin += 1 }
            guard d.isCandidate else { continue }
            s.markCandidate(time: t)
            s.markAccepted(pose: p, time: t, coordinateFrame: 0, reason: d.reason)
            counts[d.reason, default: 0] += 1
        }
        return Result(byReason: counts, spinSamples: spin)
    }

    /// Parse trajectory.jsonl lines (only coordinate frame 0 and normal tracking are used).
    public static func samples(fromTrajectoryJSONL data: Data) throws -> [(time: Double, pose: Pose)] {
        let dec = NammaJSON.decoder()
        var out: [(Double, Pose)] = []
        for line in data.split(separator: 0x0A) where !line.isEmpty {
            let s = try dec.decode(TrajectorySample.self, from: Data(line))
            guard s.coordinateFrameId == 0, s.tracking == "normal", s.position.count == 3, s.quaternionXYZW.count == 4 else { continue }
            let q = simd_quatd(ix: s.quaternionXYZW[0], iy: s.quaternionXYZW[1], iz: s.quaternionXYZW[2], r: s.quaternionXYZW[3])
            var m = simd_double4x4(q)
            m.columns.3 = SIMD4(s.position[0], s.position[1], s.position[2], 1)
            out.append((s.timestampS, Pose(m)))
        }
        return out
    }
}
