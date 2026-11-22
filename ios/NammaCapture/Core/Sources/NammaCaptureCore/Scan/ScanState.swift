import Foundation
import simd

/// Rooms, doorways, loop-backs and the prompts that follow from them. Pure state: the app feeds it poses and accepted
/// keyframes and turns its prompts into on-screen guidance. Automatic doorway detection is deliberately absent:
/// doorways and room changes are marked by the user.
public struct ScanState: Sendable {
    public struct KeyframeRef: Sendable { let frameId: String; let position: SIMD3<Double>; let time: Double; let coordinateFrame: Int; let roomId: String? }
    public struct Doorway: Sendable, Equatable {
        public let id: String
        public let position: SIMD3<Double>
        /// viewing direction when marked (the direction the user was walking through)
        public let forward: SIMD3<Double>
        public let fromRoomId: String?
        public let toRoomId: String?
        public let time: Double
        public let coordinateFrame: Int
        public var lookBackFrameId: String?
    }

    public private(set) var rooms: [Room] = []
    public private(set) var currentRoomId: String?
    public private(set) var doorways: [Doorway] = []
    public private(set) var keyframes: [KeyframeRef] = []
    public private(set) var roomStartPosition: SIMD3<Double>?
    public private(set) var roomStartTime: Double?
    public private(set) var pathLengthInRoom: Double = 0
    private var lastPosition: SIMD3<Double>?
    public private(set) var loopBackSuggested = false
    public private(set) var loopBacksInRoom = 0
    /// Heuristic, not detection: the camera is far from every place photographed earlier in this room, so the user
    /// may have walked into another area. Only prompts; a doorway is never marked automatically.
    public private(set) var newAreaSuggested = false
    private var newAreaDismissedAt: SIMD3<Double>?

    public var lookBackRadiusM = 2.5
    /// distance (horizontal) from all earlier keyframes of the current room that triggers the new-area prompt
    public var newAreaDistanceM = 3.0
    /// keyframes younger than this do not count as "earlier" (they follow the user)
    public var newAreaMinAgeS = 20.0
    public var loopBackRadiusM = 1.5
    public var loopBackMinAgeS = 20.0
    public var suggestLoopBackAfterPathM = 8.0

    public init() {}

    public var currentRoom: Room? { rooms.first { $0.id == currentRoomId } }
    public var pendingLookBack: Doorway? { doorways.last.flatMap { $0.lookBackFrameId == nil ? $0 : nil } }

    @discardableResult
    public mutating func startRoom(label: String, at position: SIMD3<Double>?, time: Double?, wallclock: String = ISOTime.string()) -> Room {
        let id = "room_\(rooms.count + 1)"
        let clean = label.trimmingCharacters(in: .whitespacesAndNewlines)
        let room = Room(id: id, label: clean.isEmpty ? "Room \(rooms.count + 1)" : String(clean.prefix(60)), createdAt: wallclock)
        rooms.append(room)
        currentRoomId = id
        roomStartPosition = position; roomStartTime = time
        pathLengthInRoom = 0; loopBackSuggested = false; loopBacksInRoom = 0
        newAreaSuggested = false; newAreaDismissedAt = nil
        return room
    }

    public mutating func renameRoom(_ id: String, to label: String) -> Bool {
        guard let i = rooms.firstIndex(where: { $0.id == id }) else { return false }
        let clean = label.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return false }
        rooms[i].label = String(clean.prefix(60))
        return true
    }

    /// Mark a doorway at the current pose. If `nextRoomLabel` is given, a new room starts there.
    @discardableResult
    public mutating func markDoorway(pose: Pose, time: Double, coordinateFrame: Int, nextRoomLabel: String?) -> Doorway {
        let from = currentRoomId
        var to: String? = nil
        if let label = nextRoomLabel { to = startRoom(label: label, at: pose.position, time: time).id }
        let d = Doorway(id: "door_\(doorways.count + 1)", position: pose.position, forward: pose.forward, fromRoomId: from,
                        toRoomId: to, time: time, coordinateFrame: coordinateFrame, lookBackFrameId: nil)
        doorways.append(d)
        newAreaSuggested = false
        newAreaDismissedAt = pose.position
        return d
    }

    /// Feed every processed pose (cheap). `time` and `coordinateFrame` enable the new-area prompt.
    public mutating func updatePose(_ pose: Pose, time: Double? = nil, coordinateFrame: Int? = nil) {
        let p = pose.position
        if let l = lastPosition { pathLengthInRoom += simd_distance(l, p) }
        lastPosition = p
        if !loopBackSuggested, loopBacksInRoom == 0, pathLengthInRoom > suggestLoopBackAfterPathM { loopBackSuggested = true }
        if let time, let coordinateFrame { updateNewArea(p, time: time, coordinateFrame: coordinateFrame) }
    }

    private static func flat(_ a: SIMD3<Double>, _ b: SIMD3<Double>) -> Double { simd_length(SIMD2(a.x - b.x, a.z - b.z)) }

    private mutating func updateNewArea(_ p: SIMD3<Double>, time: Double, coordinateFrame: Int) {
        if let a = newAreaDismissedAt, Self.flat(a, p) < newAreaDistanceM { newAreaSuggested = false; return }
        let earlier = keyframes.filter { $0.roomId == currentRoomId && $0.coordinateFrame == coordinateFrame && time - $0.time >= newAreaMinAgeS }
        guard earlier.count >= 5 else { newAreaSuggested = false; return }
        newAreaSuggested = earlier.allSatisfy { Self.flat($0.position, p) >= newAreaDistanceM }
    }

    /// The user answered the prompt without marking a doorway ("same room"): ask again only after another
    /// `newAreaDistanceM` of travel from here.
    public mutating func dismissNewArea(at position: SIMD3<Double>) {
        newAreaSuggested = false
        newAreaDismissedAt = position
    }

    /// Record an accepted keyframe. Returns the doorway whose look-back this keyframe satisfied, if any.
    public mutating func keyframeAccepted(frameId: String, pose: Pose, time: Double, coordinateFrame: Int) -> Doorway? {
        keyframes.append(KeyframeRef(frameId: frameId, position: pose.position, time: time, coordinateFrame: coordinateFrame, roomId: currentRoomId))
        guard let i = doorways.indices.last, doorways[i].lookBackFrameId == nil,
              doorways[i].coordinateFrame == coordinateFrame else { return nil }
        let d = doorways[i]
        let near = simd_distance(SIMD3(d.position.x, 0, d.position.z), SIMD3(pose.position.x, 0, pose.position.z)) <= lookBackRadiusM
        let back = simd_dot(simd_normalize(SIMD3(pose.forward.x, 0, pose.forward.z)),
                            simd_normalize(SIMD3(d.forward.x, 0, d.forward.z))) < -0.5
        if near && back && time > d.time {
            doorways[i].lookBackFrameId = frameId
            return doorways[i]
        }
        return nil
    }

    /// Earlier keyframes near the current position (same coordinate frame, older than `loopBackMinAgeS`):
    /// candidate loop-closure pairs for the reconstruction pipeline.
    public func loopCandidates(near pose: Pose, time: Double, coordinateFrame: Int, limit: Int = 8) -> [String] {
        keyframes
            .filter { $0.coordinateFrame == coordinateFrame && time - $0.time >= loopBackMinAgeS &&
                      simd_distance($0.position, pose.position) <= loopBackRadiusM }
            .sorted { simd_distance($0.position, pose.position) < simd_distance($1.position, pose.position) }
            .prefix(limit).map(\.frameId)
    }

    public mutating func loopBackMarked() { loopBacksInRoom += 1; loopBackSuggested = false }
}

/// One line of guidance for the capture screen, highest priority first.
public struct Guidance: Sendable, Equatable, Identifiable {
    public enum Level: Int, Sendable, Comparable {
        case info = 0, suggestion = 1, warning = 2, blocking = 3
        public static func < (a: Level, b: Level) -> Bool { a.rawValue < b.rawValue }
    }
    public var id: String
    public var level: Level
    public var message: String
    public init(id: String, level: Level, message: String) { self.id = id; self.level = level; self.message = message }
}

public enum GuidanceBuilder {
    public static func build(tracking: TrackingQuality, trackingReason: String?, interrupted: Bool,
                             angularSpeed: Double?, linearSpeed: Double?, thresholds: QualityThresholds,
                             lastRejectReasons: [String], lowOverlap: Bool, coverage: CoverageSummary?,
                             pendingLookBack: Bool, suggestLoopBack: Bool, storageLow: Bool, thermalSerious: Bool,
                             relocalizedRecently: Bool, spinning: Bool = false,
                             newAreaSuggested: Bool = false) -> [Guidance] {
        var g: [Guidance] = []
        if interrupted { g.append(.init(id: "interrupted", level: .blocking, message: "Camera interrupted. Return to the app to continue.")) }
        if storageLow { g.append(.init(id: "storage", level: .blocking, message: "Phone storage is almost full. Finish the scan now.")) }
        switch tracking {
        case .notAvailable:
            g.append(.init(id: "tracking", level: .blocking, message: "Tracking lost. Point at a part of the room you already scanned."))
        case .limited:
            switch trackingReason {
            case "initializing": g.append(.init(id: "tracking", level: .warning, message: "Starting tracking. Move the phone slowly side to side."))
            case "excessive_motion": g.append(.init(id: "tracking", level: .warning, message: "Slow down. Tracking needs slower movement."))
            case "insufficient_features": g.append(.init(id: "tracking", level: .warning, message: "Too little detail here. Include furniture, edges or texture in view."))
            case "relocalizing": g.append(.init(id: "tracking", level: .warning, message: "Relocating. Point at an area you scanned before."))
            default: g.append(.init(id: "tracking", level: .warning, message: "Tracking is limited."))
            }
        case .normal: break
        }
        if thermalSerious { g.append(.init(id: "thermal", level: .warning, message: "Phone is hot. Capture rate reduced; consider a short pause.")) }
        if let a = angularSpeed, a > thresholds.warnAngularSpeedDegPerS {
            g.append(.init(id: "speed", level: .warning, message: "Turn more slowly."))
        } else if let v = linearSpeed, v > thresholds.warnLinearSpeedMPerS {
            g.append(.init(id: "speed", level: .warning, message: "Walk more slowly."))
        }
        if lastRejectReasons.contains("blurry") { g.append(.init(id: "blur", level: .warning, message: "Images are blurry. Hold steadier or add light.")) }
        if lastRejectReasons.contains("underexposed") { g.append(.init(id: "dark", level: .warning, message: "Too dark. Turn on lights if you can.")) }
        if lastRejectReasons.contains("overexposed") { g.append(.init(id: "bright", level: .warning, message: "Too bright. Avoid pointing at windows or lamps.")) }
        if spinning {
            g.append(.init(id: "spin", level: .warning,
                           message: "Move, don't just turn: take a few steps, then look around. Turning on the spot adds little 3D detail."))
        }
        if newAreaSuggested {
            g.append(.init(id: "newarea", level: .suggestion,
                           message: "New area? If you went through a doorway, tap the room name and mark the doorway."))
        }
        if lowOverlap { g.append(.init(id: "overlap", level: .suggestion, message: "Big jump between photos. Turn more gradually.")) }
        if relocalizedRecently { g.append(.init(id: "revisit", level: .suggestion, message: "Tracking recovered. Sweep over an area you already scanned.")) }
        if pendingLookBack { g.append(.init(id: "lookback", level: .suggestion, message: "Turn around and capture the previous room through the doorway.")) }
        if suggestLoopBack { g.append(.init(id: "loopback", level: .suggestion, message: "Walk back to where you started this room, then tap Loop-back.")) }
        if let c = coverage {
            if c.guidance.contains("look_down_floor") { g.append(.init(id: "floor", level: .suggestion, message: "Tilt down: capture more of the floor.")) }
            if c.guidance.contains("look_up_ceiling") { g.append(.init(id: "ceiling", level: .suggestion, message: "Tilt up: capture more of the ceiling.")) }
            if c.guidance.contains("turn_around_walls") { g.append(.init(id: "walls", level: .suggestion, message: "Turn around: some walls are not covered yet.")) }
        }
        return g.sorted { $0.level > $1.level }
    }
}

/// End-of-scan check of coverage, shown before the scan is finished. It warns; it never blocks finishing.
/// Thresholds are guidance values chosen for this app, not calibrated against reconstruction quality.
public enum CoverageAssessment {
    public static let minDirections = 24          // of 36 viewing-direction bins
    public static let minFloor = 0.6
    public static let minCeiling = 0.5

    public struct Result: Sendable, Equatable {
        public var directionsCovered: Int
        public var directionsTotal: Int
        public var floor: Double?
        public var ceiling: Double?
        /// human-readable issues; empty when nothing is low
        public var issues: [String]
        public var isLow: Bool { !issues.isEmpty }
    }

    /// `rooms`: one summary per room / tracking segment. Each room is judged on its own.
    public static func assess(_ rooms: [CoverageSummary], roomLabel: (String?) -> String = { $0 ?? "Room" }) -> Result {
        var issues: [String] = []
        var dc = 0, dt = 0
        var floors: [Double] = [], ceilings: [Double] = []
        if rooms.isEmpty { issues.append("No coverage was measured (tracking never became normal).") }
        for r in rooms {
            let n = r.directionsCovered.filter { $0 }.count
            dc += n; dt += r.directionsCovered.count
            let name = roomLabel(r.roomId)
            if n < minDirections * r.directionsCovered.count / 36 {
                issues.append("\(name): only \(n) of \(r.directionsCovered.count) viewing directions seen from two places.")
            }
            if let f = r.floorCoveredFractionEstimate {
                floors.append(f)
                if f < minFloor { issues.append("\(name): floor about \(Int(f * 100))% covered.") }
            } else { issues.append("\(name): floor coverage unknown (no floor plane found).") }
            if let c = r.ceilingCoveredFractionEstimate {
                ceilings.append(c)
                if c < minCeiling { issues.append("\(name): ceiling about \(Int(c * 100))% covered.") }
            }
        }
        return Result(directionsCovered: dc, directionsTotal: dt,
                      floor: floors.isEmpty ? nil : floors.reduce(0, +) / Double(floors.count),
                      ceiling: ceilings.isEmpty ? nil : ceilings.reduce(0, +) / Double(ceilings.count), issues: issues)
    }
}
