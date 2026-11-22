import Foundation
import simd
import Testing
@testable import NammaCaptureCore

@Suite struct QualityTests {
    func checker(_ w: Int, _ h: Int, _ cell: Int, lo: UInt8 = 40, hi: UInt8 = 200) -> LumaImage {
        var px = [UInt8](repeating: 0, count: w * h)
        for y in 0..<h { for x in 0..<w { px[y * w + x] = ((x / cell + y / cell) % 2 == 0) ? lo : hi } }
        return LumaImage(width: w, height: h, pixels: px)
    }

    @Test func sharpImageScoresHigherThanFlatImage() {
        let sharp = QualityMetrics.measure(checker(320, 240, 4))
        let flat = QualityMetrics.measure(LumaImage(width: 320, height: 240, pixels: .init(repeating: 120, count: 320 * 240)))
        #expect(sharp.sharpness > 1000)
        #expect(flat.sharpness == 0)
        #expect(abs(flat.meanLuma - 120) < 0.001)
    }

    @Test func gateRejectsBlurAndDarknessAndExplainsWhy() {
        let t = QualityThresholds()
        let blurry = QualityMetrics(sharpness: 2, meanLuma: 10, darkFraction: 0.9, brightFraction: 0)
        let d = QualityGate.evaluate(GateInput(metrics: blurry, tracking: .normal, trackingReason: nil,
                                               secondsSinceLastAccept: 1, override: false), t)
        #expect(!d.accept)
        #expect(d.rejectReasons.contains("blurry") && d.rejectReasons.contains("underexposed"))
    }

    @Test func gateRelaxesAfterStaleWindowAndFlagsIt() {
        let t = QualityThresholds()
        let marginal = QualityMetrics(sharpness: t.minSharpness * 0.7, meanLuma: 100, darkFraction: 0.1, brightFraction: 0)
        let strict = QualityGate.evaluate(GateInput(metrics: marginal, tracking: .normal, trackingReason: nil, secondsSinceLastAccept: 1, override: false), t)
        let relaxed = QualityGate.evaluate(GateInput(metrics: marginal, tracking: .normal, trackingReason: nil, secondsSinceLastAccept: 10, override: false), t)
        #expect(!strict.accept)
        #expect(relaxed.accept && relaxed.relaxed && relaxed.warnings.contains("relaxed_gate"))
    }

    @Test func overrideKeepsFrameButRecordsReasonExceptWithoutTracking() {
        let t = QualityThresholds()
        let m = QualityMetrics(sharpness: 1, meanLuma: 100, darkFraction: 0, brightFraction: 0)
        let o = QualityGate.evaluate(GateInput(metrics: m, tracking: .normal, trackingReason: nil, secondsSinceLastAccept: 0, override: true), t)
        #expect(o.accept && o.overridden && o.warnings.contains("overridden:blurry"))
        let lost = QualityGate.evaluate(GateInput(metrics: m, tracking: .notAvailable, trackingReason: nil, secondsSinceLastAccept: 0, override: true), t)
        #expect(!lost.accept)
    }

    @Test func limitedTrackingInsufficientFeaturesIsAWarningNotARejection() {
        let m = QualityMetrics(sharpness: 100, meanLuma: 120, darkFraction: 0, brightFraction: 0)
        let d = QualityGate.evaluate(GateInput(metrics: m, tracking: .limited, trackingReason: "insufficient_features", secondsSinceLastAccept: 0, override: false), QualityThresholds())
        #expect(d.accept && d.warnings.contains("tracking_limited_insufficient_features"))
        let r = QualityGate.evaluate(GateInput(metrics: m, tracking: .limited, trackingReason: "relocalizing", secondsSinceLastAccept: 0, override: false), QualityThresholds())
        #expect(!r.accept)
    }

    @Test func downsampleRespectsStride() {
        var plane = [UInt8](repeating: 0, count: 64 * 10)   // 40 px wide image in a 64-byte stride
        for y in 0..<10 { for x in 0..<40 { plane[y * 64 + x] = 100 } ; for x in 40..<64 { plane[y * 64 + x] = 255 } }
        let img = plane.withUnsafeBytes { LumaImage.downsample(base: $0.baseAddress!, width: 40, height: 10, bytesPerRow: 64, maxWidth: 20) }
        #expect(img.width == 20 && img.height == 5)
        #expect(img.pixels.allSatisfy { $0 == 100 })
    }
}

@Suite struct KeyframeTests {
    @Test func selectsOnTranslationRotationAndRateLimits() {
        var s = KeyframeSelector()
        let p0 = Fixtures.pose(0)
        #expect(s.evaluate(pose: p0, time: 0, coordinateFrame: 0, staleAfterS: 4).reason == "first_frame")
        s.markCandidate(time: 0); s.markAccepted(pose: p0, time: 0, coordinateFrame: 0)
        // too soon
        #expect(!s.evaluate(pose: Fixtures.pose(3), time: 0.1, coordinateFrame: 0, staleAfterS: 4).isCandidate)
        // moved 0.3 m
        var moved = p0; moved.matrix.columns.3.x += 0.3
        #expect(s.evaluate(pose: moved, time: 1, coordinateFrame: 0, staleAfterS: 4).reason == "translation")
        // rotated 15 degrees in place
        let r = simd_double3x3(simd_quatd(angle: 15 * .pi / 180, axis: SIMD3(0, 1, 0)))
        var rot = p0.matrix
        rot.columns.0 = SIMD4(r.columns.0, 0); rot.columns.1 = SIMD4(r.columns.1, 0); rot.columns.2 = SIMD4(r.columns.2, 0)
        #expect(s.evaluate(pose: Pose(rot), time: 1, coordinateFrame: 0, staleAfterS: 4).reason == "rotation")
        // standing still
        #expect(!s.evaluate(pose: p0, time: 1, coordinateFrame: 0, staleAfterS: 4).isCandidate)
        // a new coordinate frame always starts with a first frame
        #expect(s.evaluate(pose: p0, time: 1, coordinateFrame: 1, staleAfterS: 4).reason == "first_frame")
    }

    @Test func motionEstimatorMeasuresSpeed() {
        var m = MotionEstimator()
        m.smoothing = 1
        m.update(pose: Fixtures.pose(0), time: 0)
        var p = Fixtures.pose(0); p.matrix.columns.3.x += 0.5
        m.update(pose: p, time: 0.5)
        #expect(abs((m.linearMPerS ?? 0) - 1.0) < 1e-9)
    }
}

@Suite struct GeometryTests {
    @Test func rowMajorRoundTripsAndColmapCameraCenterIsRecovered() {
        let p = Fixtures.pose(7)
        let back = Pose(rowMajor: p.rowMajor)!
        #expect(simd_distance(back.position, p.position) < 1e-12)
        let e = p.colmapExtrinsics
        // COLMAP camera centre C = -R^T t must equal the ARKit camera position
        let q = simd_quatd(real: e.qw, imag: SIMD3(e.qx, e.qy, e.qz))
        let R = simd_double3x3(q)
        let C = -simd_mul(R.transpose, e.t)
        #expect(simd_distance(C, p.position) < 1e-9)
        // COLMAP +Z (viewing direction) in world equals ARKit -Z (forward)
        let viewWorld = simd_mul(R.transpose, SIMD3<Double>(0, 0, 1))
        #expect(simd_distance(viewWorld, p.forward) < 1e-9)
        #expect(e.qw >= 0)
    }

    @Test func intrinsicsScaleWithImageSize() {
        let k = Intrinsics(fx: 1500, fy: 1500, cx: 960, cy: 720, width: 1920, height: 1440).scaled(toWidth: 256, height: 192)
        #expect(abs(k.fx - 200) < 1e-9 && abs(k.cx - 128) < 1e-9 && abs(k.cy - 96) < 1e-9)
    }
}

@Suite struct CoverageTests {
    @Test func oneViewpointIsObservedButNotCovered() {
        var c = CoverageModel()
        c.setFloor(y: 0)
        let cam = SIMD3<Double>(0, 1.4, 0)
        c.addObservation(point: SIMD3(1, 0, 1), surface: .floor, from: cam, source: .planeEstimate)
        var s = c.summary(roomId: nil)
        #expect(s.floor.observedCells == 1 && s.floor.coveredCells == 0)
        #expect(s.method == "arkit_plane_estimates" && !s.measured)
        c.addObservation(point: SIMD3(1.05, 0, 1.05), surface: .floor, from: SIMD3(0.2, 1.4, 0), source: .planeEstimate)
        #expect(c.summary(roomId: nil).floor.coveredCells == 0)          // second view too close to the first
        c.addObservation(point: SIMD3(1.05, 0, 1.05), surface: .floor, from: SIMD3(1.0, 1.4, 0), source: .lidarDepth)
        s = c.summary(roomId: nil)
        #expect(s.floor.coveredCells == 1 && s.floor.measuredCells == 1 && s.measured)
    }

    @Test func spinningInPlaceDoesNotCoverDirections() {
        var c = CoverageModel()
        for i in 0..<36 {
            let r = simd_double3x3(simd_quatd(angle: Double(i) * 10 * .pi / 180, axis: SIMD3(0, 1, 0)))
            var m = matrix_identity_double4x4
            m.columns.0 = SIMD4(r.columns.0, 0); m.columns.1 = SIMD4(r.columns.1, 0); m.columns.2 = SIMD4(r.columns.2, 0)
            m.columns.3 = SIMD4(0, 1.4, 0, 1)
            c.addCameraView(pose: Pose(m))
        }
        let s = c.summary(roomId: nil)
        #expect(s.directionsCovered.allSatisfy { !$0 })
        #expect(s.guidance.contains("turn_around_walls"))
        #expect(s.method == "viewing_directions_only")
    }

    @Test func pointsAreNotClassifiedWithoutAFloorEstimate() {
        let c = CoverageModel()
        #expect(c.classify(SIMD3(0, 0, 0), cameraY: 1.4) == nil)
        var d = CoverageModel(); d.setFloor(y: -1.4)
        #expect(d.classify(SIMD3(0, -1.35, 0), cameraY: 0) == .floor)
        #expect(d.classify(SIMD3(0, 0, 2), cameraY: 0) == .wall)
        #expect(d.classify(SIMD3(0, 1.2, 0), cameraY: 0) == .ceiling)
    }
}

@Suite struct ScanStateTests {
    @Test func doorwayLookBackIsSatisfiedOnlyByABackwardViewNearTheDoor() {
        var s = ScanState()
        s.startRoom(label: "Hall", at: .zero, time: 0)
        let door = Fixtures.pose(0)                              // looking -Z
        s.markDoorway(pose: door, time: 1, coordinateFrame: 0, nextRoomLabel: "Bedroom")
        #expect(s.rooms.count == 2 && s.currentRoom?.label == "Bedroom")
        #expect(s.pendingLookBack != nil)
        // same direction: not a look-back
        #expect(s.keyframeAccepted(frameId: "f1", pose: door, time: 2, coordinateFrame: 0) == nil)
        // turned around (looking +Z) near the door
        let r = simd_double3x3(simd_quatd(angle: .pi, axis: SIMD3(0, 1, 0)))
        var m = door.matrix
        m.columns.0 = SIMD4(r.columns.0, 0); m.columns.1 = SIMD4(r.columns.1, 0); m.columns.2 = SIMD4(r.columns.2, 0)
        m.columns.3.z += 1.0
        #expect(s.keyframeAccepted(frameId: "f2", pose: Pose(m), time: 3, coordinateFrame: 0)?.lookBackFrameId == "f2")
        #expect(s.pendingLookBack == nil)
    }

    @Test func loopCandidatesAreOldNearbyFramesInTheSameCoordinateFrame() {
        var s = ScanState()
        s.startRoom(label: "", at: .zero, time: 0)
        #expect(s.currentRoom?.label == "Room 1")
        _ = s.keyframeAccepted(frameId: "old", pose: Fixtures.pose(0), time: 0, coordinateFrame: 0)
        _ = s.keyframeAccepted(frameId: "other-frame", pose: Fixtures.pose(0), time: 0, coordinateFrame: 1)
        _ = s.keyframeAccepted(frameId: "recent", pose: Fixtures.pose(0), time: 50, coordinateFrame: 0)
        #expect(s.loopCandidates(near: Fixtures.pose(0), time: 60, coordinateFrame: 0) == ["old"])
    }

    @Test func guidanceIsOrderedBySeverity() {
        let g = GuidanceBuilder.build(tracking: .limited, trackingReason: "excessive_motion", interrupted: false,
                                      angularSpeed: 200, linearSpeed: nil, thresholds: QualityThresholds(),
                                      lastRejectReasons: ["blurry"], lowOverlap: true, coverage: nil, pendingLookBack: true,
                                      suggestLoopBack: false, storageLow: true, thermalSerious: false, relocalizedRecently: false)
        #expect(g.first?.id == "storage")
        #expect(g.contains { $0.id == "lookback" })
        #expect(zip(g, g.dropFirst()).allSatisfy { $0.level >= $1.level })
    }
}
