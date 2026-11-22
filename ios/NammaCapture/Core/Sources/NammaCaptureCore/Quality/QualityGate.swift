import Foundation

/// A small 8-bit luminance image (the camera's Y plane, downsampled) used for real-time quality metrics.
public struct LumaImage: Sendable {
    public let width: Int
    public let height: Int
    public let pixels: [UInt8]           // row-major, tightly packed

    public init(width: Int, height: Int, pixels: [UInt8]) {
        precondition(pixels.count == width * height)
        self.width = width; self.height = height; self.pixels = pixels
    }

    /// Box-downsample an 8-bit plane (any row stride) to at most `maxWidth` wide. Cost ~ source pixels / factor^2 reads.
    public static func downsample(base: UnsafeRawPointer, width: Int, height: Int, bytesPerRow: Int,
                                  maxWidth: Int = 320) -> LumaImage {
        let f = max(1, Int((Double(width) / Double(maxWidth)).rounded(.up)))
        let w = width / f, h = height / f
        var out = [UInt8](repeating: 0, count: w * h)
        let src = base.assumingMemoryBound(to: UInt8.self)
        let area = f * f
        for y in 0..<h {
            for x in 0..<w {
                var s = 0
                for dy in 0..<f {
                    let row = src + (y * f + dy) * bytesPerRow + x * f
                    for dx in 0..<f { s += Int(row[dx]) }
                }
                out[y * w + x] = UInt8(s / area)
            }
        }
        return LumaImage(width: w, height: h, pixels: out)
    }

    /// Tiny thumbnail for duplicate detection.
    public func thumbnail(width tw: Int = 32, height th: Int = 24) -> [UInt8] {
        var out = [UInt8](repeating: 0, count: tw * th)
        for y in 0..<th {
            for x in 0..<tw {
                let sx = x * width / tw, sy = y * height / th
                out[y * tw + x] = pixels[sy * width + sx]
            }
        }
        return out
    }
}

public struct QualityMetrics: Codable, Sendable, Equatable {
    /// Variance of the 4-neighbour Laplacian on the ~320 px wide luma image. Higher = sharper. Device/scene dependent.
    public var sharpness: Double
    public var meanLuma: Double
    /// fraction of pixels <= 16 and >= 245
    public var darkFraction: Double
    public var brightFraction: Double
    public var angularSpeedDegPerS: Double?
    public var linearSpeedMPerS: Double?
    /// 0...1 mean absolute difference of thumbnails against the last accepted keyframe (null for the first)
    public var differenceFromLastKeyframe: Double?

    public init(sharpness: Double, meanLuma: Double, darkFraction: Double, brightFraction: Double,
                angularSpeedDegPerS: Double? = nil, linearSpeedMPerS: Double? = nil, differenceFromLastKeyframe: Double? = nil) {
        self.sharpness = sharpness; self.meanLuma = meanLuma; self.darkFraction = darkFraction
        self.brightFraction = brightFraction; self.angularSpeedDegPerS = angularSpeedDegPerS
        self.linearSpeedMPerS = linearSpeedMPerS; self.differenceFromLastKeyframe = differenceFromLastKeyframe
    }
    enum CodingKeys: String, CodingKey {
        case sharpness, meanLuma = "mean_luma", darkFraction = "dark_fraction", brightFraction = "bright_fraction",
             angularSpeedDegPerS = "angular_speed_deg_s", linearSpeedMPerS = "linear_speed_m_s",
             differenceFromLastKeyframe = "difference_from_last_keyframe"
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(finiteOrZero(sharpness), forKey: .sharpness); try c.encode(finiteOrZero(meanLuma), forKey: .meanLuma)
        try c.encode(finiteOrZero(darkFraction), forKey: .darkFraction)
        try c.encode(finiteOrZero(brightFraction), forKey: .brightFraction)
        try c.encodeNullable(finiteOrNil(angularSpeedDegPerS), forKey: .angularSpeedDegPerS)
        try c.encodeNullable(finiteOrNil(linearSpeedMPerS), forKey: .linearSpeedMPerS)
        try c.encodeNullable(finiteOrNil(differenceFromLastKeyframe), forKey: .differenceFromLastKeyframe)
    }

    public static func measure(_ img: LumaImage) -> QualityMetrics {
        let w = img.width, h = img.height
        var sum = 0.0, dark = 0, bright = 0
        for p in img.pixels {
            sum += Double(p)
            if p <= 16 { dark += 1 } else if p >= 245 { bright += 1 }
        }
        let n = Double(max(1, img.pixels.count))
        // Laplacian variance (interior pixels)
        var ls = 0.0, ls2 = 0.0, m = 0.0
        if w >= 3 && h >= 3 {
            img.pixels.withUnsafeBufferPointer { px in
                for y in 1..<(h - 1) {
                    let r = y * w
                    for x in 1..<(w - 1) {
                        let c = Double(px[r + x])
                        let l = Double(px[r + x - 1]) + Double(px[r + x + 1]) + Double(px[r - w + x]) + Double(px[r + w + x]) - 4 * c
                        ls += l; ls2 += l * l; m += 1
                    }
                }
            }
        }
        let mean = m > 0 ? ls / m : 0
        let variance = m > 0 ? max(0, ls2 / m - mean * mean) : 0
        return QualityMetrics(sharpness: variance, meanLuma: sum / n, darkFraction: Double(dark) / n,
                              brightFraction: Double(bright) / n)
    }

    public static func thumbnailDifference(_ a: [UInt8], _ b: [UInt8]) -> Double {
        guard a.count == b.count, !a.isEmpty else { return 1 }
        var s = 0
        for i in 0..<a.count { s += abs(Int(a[i]) - Int(b[i])) }
        return Double(s) / Double(a.count) / 255.0
    }
}

/// Thresholds. Initial values are conservative starting points, not calibrated constants: they are recorded in every
/// manifest so captures can be compared and the values tuned on real devices.
public struct QualityThresholds: Codable, Sendable, Equatable {
    public var minSharpness: Double = 18
    public var warnSharpness: Double = 45
    public var minMeanLuma: Double = 22
    public var maxMeanLuma: Double = 235
    public var maxDarkFraction: Double = 0.65
    public var maxBrightFraction: Double = 0.45
    public var warnAngularSpeedDegPerS: Double = 60
    public var maxAngularSpeedDegPerS: Double = 140
    public var warnLinearSpeedMPerS: Double = 0.9
    public var maxLinearSpeedMPerS: Double = 2.0
    /// thumbnails closer than this to the last keyframe are duplicates
    public var minDifferenceFromLastKeyframe: Double = 0.02
    /// After this many seconds without an accepted keyframe while the user keeps moving, blur and exposure limits
    /// are relaxed by `relaxFactor` so a dim or low-texture area is still covered (frames are flagged "relaxed_gate").
    public var staleAfterS: Double = 4
    public var relaxFactor: Double = 0.5
    public init() {}
    enum CodingKeys: String, CodingKey {
        case minSharpness = "min_sharpness", warnSharpness = "warn_sharpness", minMeanLuma = "min_mean_luma",
             maxMeanLuma = "max_mean_luma", maxDarkFraction = "max_dark_fraction", maxBrightFraction = "max_bright_fraction",
             warnAngularSpeedDegPerS = "warn_angular_speed_deg_s", maxAngularSpeedDegPerS = "max_angular_speed_deg_s",
             warnLinearSpeedMPerS = "warn_linear_speed_m_s", maxLinearSpeedMPerS = "max_linear_speed_m_s",
             minDifferenceFromLastKeyframe = "min_difference_from_last_keyframe", staleAfterS = "stale_after_s",
             relaxFactor = "relax_factor"
    }
}

public enum TrackingQuality: String, Sendable, Codable {
    case normal, limited, notAvailable = "not_available"
}

public struct GateInput: Sendable {
    public var metrics: QualityMetrics
    public var tracking: TrackingQuality
    public var trackingReason: String?
    public var secondsSinceLastAccept: Double?
    public var override: Bool
    public init(metrics: QualityMetrics, tracking: TrackingQuality, trackingReason: String?,
                secondsSinceLastAccept: Double?, override: Bool) {
        self.metrics = metrics; self.tracking = tracking; self.trackingReason = trackingReason
        self.secondsSinceLastAccept = secondsSinceLastAccept; self.override = override
    }
}

public struct GateDecision: Sendable, Equatable {
    public var accept: Bool
    public var rejectReasons: [String]
    public var warnings: [String]
    public var relaxed: Bool
    public var overridden: Bool
}

public enum QualityGate {
    public static func evaluate(_ i: GateInput, _ t: QualityThresholds) -> GateDecision {
        var reject: [String] = [], warn: [String] = []
        let relaxed = (i.secondsSinceLastAccept ?? 0) >= t.staleAfterS
        let k = relaxed ? t.relaxFactor : 1.0
        let m = i.metrics

        switch i.tracking {
        case .notAvailable: reject.append("tracking_not_available")
        case .limited:
            // A limited-tracking pose is not trustworthy. Initialization, fast motion and relocalization are rejected;
            // "insufficient features" keeps the frame (blank walls must still be photographed) with a warning.
            if i.trackingReason == "insufficient_features" { warn.append("tracking_limited_insufficient_features") }
            else { reject.append("tracking_limited_\(i.trackingReason ?? "unknown")") }
        case .normal: break
        }
        if m.sharpness < t.minSharpness * k { reject.append("blurry") }
        else if m.sharpness < t.warnSharpness { warn.append("soft_focus_or_motion_blur") }
        if m.meanLuma < t.minMeanLuma * k || m.darkFraction > min(0.95, t.maxDarkFraction / k) { reject.append("underexposed") }
        if m.meanLuma > t.maxMeanLuma || m.brightFraction > t.maxBrightFraction { reject.append("overexposed") }
        if let a = m.angularSpeedDegPerS {
            if a > t.maxAngularSpeedDegPerS { reject.append("turning_too_fast") }
            else if a > t.warnAngularSpeedDegPerS { warn.append("turning_fast") }
        }
        if let v = m.linearSpeedMPerS {
            if v > t.maxLinearSpeedMPerS { reject.append("moving_too_fast") }
            else if v > t.warnLinearSpeedMPerS { warn.append("moving_fast") }
        }
        if let d = m.differenceFromLastKeyframe, d < t.minDifferenceFromLastKeyframe { reject.append("duplicate_of_last_keyframe") }
        if relaxed { warn.append("relaxed_gate") }

        if i.override && !reject.isEmpty {
            // Manual override keeps the frame but never hides why it would have been rejected.
            let fatal = reject.contains("tracking_not_available")
            if !fatal {
                return GateDecision(accept: true, rejectReasons: [], warnings: warn + reject.map { "overridden:\($0)" },
                                    relaxed: relaxed, overridden: true)
            }
        }
        return GateDecision(accept: reject.isEmpty, rejectReasons: reject, warnings: warn, relaxed: relaxed, overridden: false)
    }
}
