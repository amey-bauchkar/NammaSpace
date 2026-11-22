import Foundation
import simd
import Testing
@testable import NammaCaptureCore

/// Tests driven by the first real capture (Testing Room, 2026-11-22, iPhone18,1): mostly turning on the spot.
@Suite struct FieldCaptureTests {
    static func pose(x: Double, z: Double, yawDeg: Double, pitchDeg: Double = 0) -> Pose {
        let q = simd_quatd(angle: yawDeg * .pi / 180, axis: SIMD3(0, 1, 0)) * simd_quatd(angle: pitchDeg * .pi / 180, axis: SIMD3(1, 0, 0))
        var m = simd_double4x4(q)
        m.columns.3 = SIMD4(x, 1.4, z, 1)
        return Pose(m)
    }

    /// Camera held `arm` m in front of a body pivot at (px, pz), looking along yaw.
    static func held(px: Double, pz: Double, yawDeg: Double, pitchDeg: Double = 0, arm: Double = 0.3) -> Pose {
        let a = yawDeg * .pi / 180
        // ARKit forward is -Z rotated by yaw about +Y: (-sin a, 0, -cos a)
        return pose(x: px - arm * sin(a), z: pz - arm * cos(a), yawDeg: yawDeg, pitchDeg: pitchDeg)
    }

    /// ~225 s at 5 Hz, like the Testing Room: stand at a spot and turn a full circle in ~16 s with the phone held
    /// 30 cm out (so the camera itself circles), swaying a few cm; shuffle 0.3 m; repeat, back and forth along a
    /// 3.4 m strip. Long camera path, small extent.
    static func spinHeavyTrajectory() -> [(time: Double, pose: Pose)] {
        var out: [(Double, Pose)] = []
        var t = 0.0, yaw = 0.0, z = 0.0, dir = 1.0
        while t < 225 {
            for i in 0..<80 {                                   // 16 s turning: 4.5 deg / sample
                let sway = 0.03 * sin(Double(i) * 0.9)
                out.append((t, held(px: sway, pz: z + 0.02 * cos(Double(i) * 1.3), yawDeg: yaw, pitchDeg: 15 * sin(Double(i) * 0.2))))
                yaw += 4.5; t += 0.2
            }
            for _ in 0..<5 {                                    // 1 s: shuffle 0.3 m
                z += dir * 0.06
                out.append((t, held(px: 0, pz: z, yawDeg: yaw))); t += 0.2
            }
            if z > 3.0 || z < 0.05 { dir = -dir }
        }
        return out
    }

    static func pathAndExtent(_ s: [(time: Double, pose: Pose)]) -> (Double, [Double]) {
        var path = 0.0
        for i in 1..<s.count { path += s[i - 1].pose.distance(to: s[i].pose) }
        let ps = s.map(\.pose.position)
        let ext = (0..<3).map { k in (ps.map { $0[k] }.max() ?? 0) - (ps.map { $0[k] }.min() ?? 0) }
        return (path, ext)
    }

    @Test func rotationTriggeredShareFallsOnASpinHeavyTrajectory() {
        let traj = Self.spinHeavyTrajectory()
        let (path, ext) = Self.pathAndExtent(traj)
        #expect(path > 20 && path < 40, "path \(path)")
        #expect(ext.allSatisfy { $0 < 4 }, "extent \(ext)")

        let old = KeyframeReplay.run(traj, thresholds: .v1_0)
        let new = KeyframeReplay.run(traj, thresholds: KeyframeThresholds())
        // the 1.0 policy reproduces the field behaviour: most keyframes come from turning
        #expect(old.rotationShare > 0.7, "\(old)")
        // the new policy: far fewer turn-triggered photos, movement-triggered photos kept
        // turn-triggered keyframes fall by over 90 % and their share falls (this path is almost pure spinning, so few
        // movement frames exist to take their place; the real Testing Room replay drops from 0.86 to 0.29)
        let turned = { (r: KeyframeReplay.Result) in (r.byReason["rotation"] ?? 0) + (r.byReason["rotation_only"] ?? 0) }
        #expect(turned(new) * 10 <= turned(old), "old \(old) new \(new)")
        #expect(new.rotationShare < old.rotationShare - 0.2, "old \(old) new \(new)")
        #expect((new.byReason["translation"] ?? 0) >= (old.byReason["translation"] ?? 0))
        #expect(new.spinSamples > 0)        // turning on the spot was noticed (drives the prompt)
        print("replay synthetic: old \(old.byReason) share \(old.rotationShare); new \(new.byReason) share \(new.rotationShare)")
    }

    @Test func walkingStillProducesKeyframes() {
        var traj: [(time: Double, pose: Pose)] = []
        for i in 0..<300 { traj.append((Double(i) * 0.2, Self.pose(x: Double(i) * 0.05, z: 0, yawDeg: 0))) }   // 15 m walk
        let r = KeyframeReplay.run(traj, thresholds: KeyframeThresholds())
        #expect((r.byReason["translation"] ?? 0) >= 60, "\(r)")
    }

    @Test func turningACornerOnTheSpotKeepsAFewFrames() {
        var s = KeyframeSelector()
        s.markCandidate(time: 0); s.markAccepted(pose: Self.pose(x: 0, z: 0, yawDeg: 0), time: 0, coordinateFrame: 0)
        var accepted = 0
        for i in 1...60 {                                         // turn 180 deg without moving
            let t = Double(i) * 0.2
            let d = s.evaluate(pose: Self.pose(x: 0, z: 0, yawDeg: Double(i) * 3), time: t, coordinateFrame: 0, staleAfterS: 4)
            if d.isCandidate { accepted += 1; s.markCandidate(time: t); s.markAccepted(pose: Self.pose(x: 0, z: 0, yawDeg: Double(i) * 3), time: t, coordinateFrame: 0, reason: d.reason) }
        }
        #expect(accepted == 2)                                   // 30 deg steps, at most 2 in a row
    }

    /// Optional: replay a real capture. NAMMACAP_TRAJECTORY=/path/to/trajectory.jsonl swift test
    @Test func realTrajectoryReplay() throws {
        guard let p = ProcessInfo.processInfo.environment["NAMMACAP_TRAJECTORY"] else { return }
        let traj = try KeyframeReplay.samples(fromTrajectoryJSONL: Data(contentsOf: URL(fileURLWithPath: p)))
        let old = KeyframeReplay.run(traj, thresholds: .v1_0)
        let new = KeyframeReplay.run(traj, thresholds: KeyframeThresholds())
        print("replay real: samples \(traj.count); old \(old.byReason) share \(String(format: "%.2f", old.rotationShare)); new \(new.byReason) share \(String(format: "%.2f", new.rotationShare)); spin samples \(new.spinSamples)")
        #expect(new.rotationShare < old.rotationShare / 2)
    }

    @Test func spinDetectorFiresOnlyWhenTurningInPlace() {
        var d = SpinDetector()
        for i in 0..<50 { d.update(pose: Self.pose(x: 0.02 * sin(Double(i)), z: 0, yawDeg: Double(i) * 6), time: Double(i) * 0.1) }
        #expect(d.spinning)
        var w = SpinDetector()
        for i in 0..<50 { w.update(pose: Self.pose(x: Double(i) * 0.05, z: 0, yawDeg: Double(i) * 6), time: Double(i) * 0.1) }
        #expect(!w.spinning)                                      // turning while walking 2.5 m is fine
    }

    @Test func sessionsSavedByApp10StillDecode() throws {
        // keyframe settings as written by app 1.0 (no rotation-only fields)
        let json = #"{"min_translation_m":0.2,"min_rotation_deg":10,"low_overlap_rotation_deg":30,"min_interval_s":0.4}"#
        let t = try JSONDecoder().decode(KeyframeThresholds.self, from: Data(json.utf8))
        #expect(t == .v1_0)
        let round = try JSONDecoder().decode(KeyframeThresholds.self, from: NammaJSON.encoder().encode(KeyframeThresholds()))
        #expect(round == KeyframeThresholds())
    }

    @Test func newAreaPromptAfterWalkingAwayAndDismissal() {
        var s = ScanState()
        s.startRoom(label: "Hall", at: .zero, time: 0)
        for i in 0..<8 { _ = s.keyframeAccepted(frameId: "f\(i)", pose: Self.pose(x: Double(i) * 0.2, z: 0, yawDeg: 0), time: Double(i), coordinateFrame: 0) }
        s.updatePose(Self.pose(x: 2.0, z: 0, yawDeg: 0), time: 40, coordinateFrame: 0)
        #expect(!s.newAreaSuggested)
        s.updatePose(Self.pose(x: 5.0, z: 0, yawDeg: 0), time: 45, coordinateFrame: 0)
        #expect(s.newAreaSuggested)
        s.dismissNewArea(at: Self.pose(x: 5.0, z: 0, yawDeg: 0).position)
        s.updatePose(Self.pose(x: 6.5, z: 0, yawDeg: 0), time: 46, coordinateFrame: 0)
        #expect(!s.newAreaSuggested)                              // not again until another 3 m
        s.markDoorway(pose: Self.pose(x: 6.5, z: 0, yawDeg: 0), time: 47, coordinateFrame: 0, nextRoomLabel: "Kitchen")
        #expect(!s.newAreaSuggested && s.currentRoom?.label == "Kitchen")
    }

    @Test func coverageAssessmentFlagsTheTestingRoomNumbers() {
        // Testing Room: 19/36 directions, floor ~71 %, ceiling ~48 %
        var dirs = [Bool](repeating: false, count: 36)
        for i in 0..<19 { dirs[i] = true }
        var c = CoverageModel().summary(roomId: "room_1")
        c.directionsCovered = dirs
        c.floorCoveredFractionEstimate = 0.713
        c.ceilingCoveredFractionEstimate = 0.478
        let a = CoverageAssessment.assess([c]) { _ in "Room 1" }
        #expect(a.isLow)
        #expect(a.issues.contains { $0.contains("19 of 36") })
        #expect(a.issues.contains { $0.contains("ceiling about 47%") })
        #expect(!a.issues.contains { $0.contains("floor about") })
        var good = c
        good.directionsCovered = [Bool](repeating: true, count: 36)
        good.ceilingCoveredFractionEstimate = 0.8
        #expect(!CoverageAssessment.assess([good]).isLow)
    }

    @Test func guidanceShowsMoveDontTurn() {
        let g = GuidanceBuilder.build(tracking: .normal, trackingReason: nil, interrupted: false, angularSpeed: nil,
                                      linearSpeed: nil, thresholds: QualityThresholds(), lastRejectReasons: [],
                                      lowOverlap: false, coverage: nil, pendingLookBack: false, suggestLoopBack: false,
                                      storageLow: false, thermalSerious: false, relocalizedRecently: false,
                                      spinning: true, newAreaSuggested: true)
        #expect(g.first?.id == "spin")
        #expect(g.contains { $0.id == "newarea" })
    }
}
