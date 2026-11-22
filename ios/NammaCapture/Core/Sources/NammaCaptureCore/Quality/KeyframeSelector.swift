import Foundation

public struct KeyframeThresholds: Codable, Sendable, Equatable {
    /// A new candidate is considered after this much camera translation since the last keyframe...
    public var minTranslationM: Double = 0.20
    /// ...or this much rotation.
    public var minRotationDeg: Double = 10
    /// Above this rotation between consecutive keyframes, overlap is likely too low for matching: warn the user.
    public var lowOverlapRotationDeg: Double = 30
    /// Hard rate limit (the high-resolution capture takes a few hundred ms).
    public var minIntervalS: Double = 0.4
    public init() {}
    enum CodingKeys: String, CodingKey {
        case minTranslationM = "min_translation_m", minRotationDeg = "min_rotation_deg",
             lowOverlapRotationDeg = "low_overlap_rotation_deg", minIntervalS = "min_interval_s"
    }
}

public struct CandidateDecision: Sendable, Equatable {
    public var isCandidate: Bool
    /// "first_frame" | "translation" | "rotation" | "stale" | "none"
    public var reason: String
    public var translationM: Double?
    public var rotationDeg: Double?
    public var lowOverlap: Bool
    public init(isCandidate: Bool, reason: String, translationM: Double?, rotationDeg: Double?, lowOverlap: Bool) {
        self.isCandidate = isCandidate; self.reason = reason; self.translationM = translationM
        self.rotationDeg = rotationDeg; self.lowOverlap = lowOverlap
    }
}

/// Decides when the current view has changed enough to be worth photographing. Quality is judged separately
/// (QualityGate); this only looks at viewpoint change and timing, per coordinate frame.
public struct KeyframeSelector: Sendable {
    public var thresholds: KeyframeThresholds
    public private(set) var lastAccepted: (pose: Pose, time: Double, frame: Int)?
    public private(set) var lastCandidateTime: Double?

    public init(thresholds: KeyframeThresholds = KeyframeThresholds()) { self.thresholds = thresholds }

    public func evaluate(pose: Pose, time: Double, coordinateFrame: Int, staleAfterS: Double) -> CandidateDecision {
        if let lc = lastCandidateTime, time - lc < thresholds.minIntervalS {
            return CandidateDecision(isCandidate: false, reason: "none", translationM: nil, rotationDeg: nil, lowOverlap: false)
        }
        guard let last = lastAccepted, last.frame == coordinateFrame else {
            return CandidateDecision(isCandidate: true, reason: "first_frame", translationM: nil, rotationDeg: nil, lowOverlap: false)
        }
        let dt = last.pose.distance(to: pose)
        let dr = last.pose.rotationDegrees(to: pose)
        let low = dr > thresholds.lowOverlapRotationDeg
        if dt >= thresholds.minTranslationM {
            return CandidateDecision(isCandidate: true, reason: "translation", translationM: dt, rotationDeg: dr, lowOverlap: low)
        }
        if dr >= thresholds.minRotationDeg {
            return CandidateDecision(isCandidate: true, reason: "rotation", translationM: dt, rotationDeg: dr, lowOverlap: low)
        }
        // nothing accepted for a while although the view moved a little: try again so the stale-gate relaxation can act
        if time - last.time >= staleAfterS && (dt >= thresholds.minTranslationM / 2 || dr >= thresholds.minRotationDeg / 2) {
            return CandidateDecision(isCandidate: true, reason: "stale", translationM: dt, rotationDeg: dr, lowOverlap: low)
        }
        return CandidateDecision(isCandidate: false, reason: "none", translationM: dt, rotationDeg: dr, lowOverlap: low)
    }

    public mutating func markCandidate(time: Double) { lastCandidateTime = time }
    public mutating func markAccepted(pose: Pose, time: Double, coordinateFrame: Int) {
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
