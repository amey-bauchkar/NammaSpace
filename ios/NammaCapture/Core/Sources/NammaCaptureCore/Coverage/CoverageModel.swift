import Foundation
import simd

/// Where an observed point came from. The coverage summary carries this so nothing estimated is shown as measured.
public enum ObservationSource: String, Codable, Sendable {
    /// LiDAR scene depth sample (measured).
    case lidarDepth = "lidar_depth"
    /// ARKit plane anchor hit by a ray from the camera (ARKit's estimate of a planar surface; not a full map).
    case planeEstimate = "plane_estimate"
}

public enum SurfaceClass: String, Codable, Sendable, CaseIterable {
    case floor, ceiling, wall
}

/// Coverage bookkeeping, kept deliberately simple so it runs during capture:
///
/// 1. Surface cells: observed 3D points are binned into 25 cm cells per surface class (floor/ceiling: x-z grid; walls:
///    x-z column + 50 cm height band). A cell counts as COVERED only when it has been seen from at least two camera
///    positions >= `minBaselineM` apart: seeing a surface once from one spot is not enough for reconstruction.
/// 2. Viewing directions: 12 heading sectors x 3 pitch bands of the camera's viewing direction. Always available,
///    labelled as an estimate: it says where the camera looked, not what geometry was seen.
///
/// The footprint used for floor/ceiling percentages is itself an estimate (cells under the walked path plus any cell
/// where floor or ceiling was observed), so those percentages are labelled "estimated".
public struct CoverageModel: Sendable {
    public var cellSize: Double = 0.25
    public var wallBand: Double = 0.5
    public var minBaselineM: Double = 0.5
    public static let headingSectors = 12
    public static let pitchBands = 3          // 0 = down (< -30 deg), 1 = level, 2 = up (> +30 deg)

    struct Key: Hashable, Sendable { let cls: SurfaceClass; let a: Int; let b: Int; let c: Int }
    struct Cell: Sendable {
        var viewpoints: [SIMD3<Double>] = []     // at most a few distinct camera positions
        var sources: Set<ObservationSource> = []
        var covered = false
    }
    struct DirBin: Sendable { var positions: [SIMD3<Double>] = []; var covered = false }

    private var cells: [Key: Cell] = [:]
    private var directions: [DirBin] = Array(repeating: DirBin(), count: headingSectors * pitchBands)
    private var footprint = Set<SIMD2<Int>>()
    /// Floor height estimate (world Y). Set from the lowest horizontal plane or the observed points.
    public private(set) var floorY: Double?
    public private(set) var ceilingY: Double?
    public private(set) var sourcesSeen = Set<ObservationSource>()

    public init() {}

    // MARK: input

    public mutating func setFloor(y: Double) { floorY = y }
    public mutating func setCeiling(y: Double) { ceilingY = y }

    /// Camera position and viewing direction for each processed frame.
    public mutating func addCameraView(pose: Pose) {
        let p = pose.position
        footprint.insert(SIMD2(Int(floor(p.x / cellSize)), Int(floor(p.z / cellSize))))
        let sector = Int(pose.headingDegrees / (360.0 / Double(Self.headingSectors))) % Self.headingSectors
        let pitch = pose.pitchDegrees
        let band = pitch < -30 ? 0 : (pitch > 30 ? 2 : 1)
        let i = band * Self.headingSectors + sector
        if !directions[i].covered {
            if directions[i].positions.allSatisfy({ simd_distance($0, p) >= minBaselineM }) {
                directions[i].positions.append(p)
            }
            if directions[i].positions.count >= 2 { directions[i].covered = true }
        }
    }

    /// Classify a world point by height. Without a floor estimate the point is ignored (we do not guess).
    public func classify(_ p: SIMD3<Double>, cameraY: Double) -> SurfaceClass? {
        guard let fy = floorY else { return nil }
        if p.y < fy + 0.12 { return .floor }
        if let cy = ceilingY, p.y > cy - 0.15 { return .ceiling }
        if ceilingY == nil && p.y > max(cameraY + 0.5, fy + 2.0) { return .ceiling }
        return .wall
    }

    public mutating func addObservation(point p: SIMD3<Double>, surface: SurfaceClass, from camera: SIMD3<Double>,
                                        source: ObservationSource) {
        guard p.x.isFinite, p.y.isFinite, p.z.isFinite else { return }
        let a = Int(floor(p.x / cellSize)), b = Int(floor(p.z / cellSize))
        let c = surface == .wall ? Int(floor(p.y / wallBand)) : 0
        let key = Key(cls: surface, a: a, b: b, c: c)
        var cell = cells[key] ?? Cell()
        cell.sources.insert(source)
        sourcesSeen.insert(source)
        if !cell.covered {
            if cell.viewpoints.allSatisfy({ simd_distance($0, camera) >= minBaselineM }) { cell.viewpoints.append(camera) }
            if cell.viewpoints.count >= 2 { cell.covered = true }
        }
        cells[key] = cell
        if surface != .wall { footprint.insert(SIMD2(a, b)) }
    }

    // MARK: output

    public func summary(roomId: String?) -> CoverageSummary {
        func stats(_ cls: SurfaceClass) -> SurfaceStats {
            let cs = cells.filter { $0.key.cls == cls }
            let measured = cs.filter { $0.value.sources.contains(.lidarDepth) }
            return SurfaceStats(observedCells: cs.count, coveredCells: cs.values.filter(\.covered).count,
                                measuredCells: measured.count,
                                estimatedOnlyCells: cs.count - measured.count)
        }
        let floorS = stats(.floor), ceilS = stats(.ceiling), wallS = stats(.wall)
        let fp = max(1, footprint.count)
        let dirCovered = directions.map(\.covered)
        var missing: [String] = []
        for band in 0..<Self.pitchBands {
            let covered = (0..<Self.headingSectors).filter { dirCovered[band * Self.headingSectors + $0] }.count
            if covered < Self.headingSectors / 2 {
                missing.append(band == 0 ? "look_down_floor" : band == 2 ? "look_up_ceiling" : "turn_around_walls")
            }
        }
        let method: String
        if sourcesSeen.contains(.lidarDepth) { method = "lidar_depth_samples" }
        else if sourcesSeen.contains(.planeEstimate) { method = "arkit_plane_estimates" }
        else { method = "viewing_directions_only" }
        return CoverageSummary(
            roomId: roomId, method: method, measured: sourcesSeen.contains(.lidarDepth), cellSizeM: cellSize,
            floorYEstimate: floorY, ceilingYEstimate: ceilingY, footprintCellsEstimate: footprint.count,
            floor: floorS, ceiling: ceilS, walls: wallS,
            floorCoveredFractionEstimate: floorY == nil ? nil : min(1, Double(floorS.coveredCells) / Double(fp)),
            ceilingCoveredFractionEstimate: floorY == nil ? nil : min(1, Double(ceilS.coveredCells) / Double(fp)),
            directionsCovered: dirCovered, headingSectors: Self.headingSectors, pitchBands: Self.pitchBands,
            guidance: missing)
    }

    /// Unit vectors for heading sectors with no covered wall-level view (for the on-screen arrow), in world X-Z.
    public func uncoveredHeadings(band: Int = 1) -> [Double] {
        (0..<Self.headingSectors).compactMap { s in
            directions[band * Self.headingSectors + s].covered ? nil : (Double(s) + 0.5) * 360.0 / Double(Self.headingSectors)
        }
    }

    /// Covered state of floor cells, for a lightweight top-down map: (cell x, cell z, covered, measured).
    public func floorCells() -> [(Int, Int, Bool, Bool)] {
        cells.compactMap { k, v in k.cls == .floor ? (k.a, k.b, v.covered, v.sources.contains(.lidarDepth)) : nil }
    }
}

public struct SurfaceStats: Codable, Sendable, Equatable {
    public var observedCells: Int
    public var coveredCells: Int
    public var measuredCells: Int
    public var estimatedOnlyCells: Int
    enum CodingKeys: String, CodingKey {
        case observedCells = "observed_cells", coveredCells = "covered_cells", measuredCells = "measured_cells",
             estimatedOnlyCells = "estimated_only_cells"
    }
}

public struct CoverageSummary: Codable, Sendable, Equatable {
    public var roomId: String?
    /// "lidar_depth_samples" | "arkit_plane_estimates" | "viewing_directions_only"
    public var method: String
    /// true only if some cells come from measured LiDAR depth
    public var measured: Bool
    public var cellSizeM: Double
    public var floorYEstimate: Double?
    public var ceilingYEstimate: Double?
    public var footprintCellsEstimate: Int
    public var floor: SurfaceStats
    public var ceiling: SurfaceStats
    public var walls: SurfaceStats
    public var floorCoveredFractionEstimate: Double?
    public var ceilingCoveredFractionEstimate: Double?
    public var directionsCovered: [Bool]
    public var headingSectors: Int
    public var pitchBands: Int
    public var guidance: [String]
    public var note = "Coverage is a capture-time estimate for guidance. It is not a reconstruction-quality guarantee."

    enum CodingKeys: String, CodingKey {
        case roomId = "room_id", method, measured, cellSizeM = "cell_size_m", floorYEstimate = "floor_y_estimate",
             ceilingYEstimate = "ceiling_y_estimate", footprintCellsEstimate = "footprint_cells_estimate", floor, ceiling,
             walls, floorCoveredFractionEstimate = "floor_covered_fraction_estimate",
             ceilingCoveredFractionEstimate = "ceiling_covered_fraction_estimate",
             directionsCovered = "directions_covered", headingSectors = "heading_sectors", pitchBands = "pitch_bands",
             guidance, note
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeNullable(roomId, forKey: .roomId); try c.encode(method, forKey: .method)
        try c.encode(measured, forKey: .measured); try c.encode(cellSizeM, forKey: .cellSizeM)
        try c.encodeNullable(finiteOrNil(floorYEstimate), forKey: .floorYEstimate)
        try c.encodeNullable(finiteOrNil(ceilingYEstimate), forKey: .ceilingYEstimate)
        try c.encode(footprintCellsEstimate, forKey: .footprintCellsEstimate)
        try c.encode(floor, forKey: .floor); try c.encode(ceiling, forKey: .ceiling); try c.encode(walls, forKey: .walls)
        try c.encodeNullable(finiteOrNil(floorCoveredFractionEstimate), forKey: .floorCoveredFractionEstimate)
        try c.encodeNullable(finiteOrNil(ceilingCoveredFractionEstimate), forKey: .ceilingCoveredFractionEstimate)
        try c.encode(directionsCovered, forKey: .directionsCovered); try c.encode(headingSectors, forKey: .headingSectors)
        try c.encode(pitchBands, forKey: .pitchBands); try c.encode(guidance, forKey: .guidance); try c.encode(note, forKey: .note)
    }
}

/// Coverage for the whole scan: one model per room (so a new room starts empty) plus the export document.
public struct ScanCoverage: Codable, Sendable {
    public var rooms: [CoverageSummary]
    public var available = true
    public init(rooms: [CoverageSummary]) { self.rooms = rooms }
}
