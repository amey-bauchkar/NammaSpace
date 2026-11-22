import ARKit
import CoreImage
import ImageIO
import NammaCaptureCore

/// Conversions from ARKit/CoreVideo objects to archive records. Called on the capture queue; JPEG encoding runs on the
/// encoder queue with the pixel buffer retained only until it is written (bounded by the pending-write limit).
enum FrameEncoding {
    static let ciContext = CIContext(options: [.cacheIntermediates: false])

    /// Longest stored image side. Larger stills (e.g. 48 MP) are downscaled; intrinsics are scaled to match.
    static let maxStoredLongSide = 4032

    /// Stored size for a source image (aspect preserved, never upscaled).
    static func storedSize(width w: Int, height h: Int) -> (Int, Int) {
        let s = min(1.0, Double(maxStoredLongSide) / Double(max(w, h)))
        return s >= 1 ? (w, h) : (Int((Double(w) * s).rounded()), Int((Double(h) * s).rounded()))
    }

    /// JPEG with EXIF that photogrammetry tools read: Make/Model and FocalLenIn35mmFilm derived from ARKit's fx
    /// (COLMAP uses it as the initial focal length), plus exposure values when the frame reported them.
    static func jpeg(_ pb: CVPixelBuffer, quality: Double, size: (Int, Int), intrinsics k: Intrinsics,
                     model: String, exposure: ExposureRecord) -> Data? {
        var img = CIImage(cvPixelBuffer: pb)
        let sw = Double(size.0) / img.extent.width, sh = Double(size.1) / img.extent.height
        if sw < 0.999 || sh < 0.999 {
            img = img.applyingFilter("CILanczosScaleTransform", parameters: [kCIInputScaleKey: sh, kCIInputAspectRatioKey: sw / sh])
        }
        guard let cs = CGColorSpace(name: CGColorSpace.sRGB),
              let cg = ciContext.createCGImage(img, from: CGRect(x: 0, y: 0, width: size.0, height: size.1), format: .RGBA8, colorSpace: cs)
        else { return nil }
        var exif: [CFString: Any] = [
            kCGImagePropertyExifFocalLenIn35mmFilm: Int((k.fx * 36.0 / Double(k.width)).rounded()),
            kCGImagePropertyExifPixelXDimension: size.0, kCGImagePropertyExifPixelYDimension: size.1,
        ]
        if let e = exposure.exposureDurationS { exif[kCGImagePropertyExifExposureTime] = e }
        if let i = exposure.iso { exif[kCGImagePropertyExifISOSpeedRatings] = [Int(i)] }
        let props: [CFString: Any] = [
            kCGImageDestinationLossyCompressionQuality: quality,
            kCGImagePropertyOrientation: 1,
            kCGImagePropertyTIFFDictionary: [kCGImagePropertyTIFFMake: "Apple", kCGImagePropertyTIFFModel: model,
                                             kCGImagePropertyTIFFSoftware: "Namma Capture"],
            kCGImagePropertyExifDictionary: exif,
        ]
        let out = NSMutableData()
        guard let dst = CGImageDestinationCreateWithData(out, "public.jpeg" as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(dst, cg, props as CFDictionary)
        return CGImageDestinationFinalize(dst) ? out as Data : nil
    }

    /// ARKit intrinsics (column-major 3x3) for an image of `resolution`.
    static func intrinsics(_ camera: ARCamera) -> Intrinsics {
        let k = camera.intrinsics
        return Intrinsics(fx: Double(k[0][0]), fy: Double(k[1][1]), cx: Double(k[2][0]), cy: Double(k[2][1]),
                          width: Int(camera.imageResolution.width), height: Int(camera.imageResolution.height))
    }

    static func tracking(_ camera: ARCamera, mapping: ARFrame.WorldMappingStatus) -> TrackingRecord {
        let (state, reason) = trackingStrings(camera.trackingState)
        return TrackingRecord(state: state, reason: reason, worldMapping: mappingString(mapping))
    }

    static func trackingStrings(_ t: ARCamera.TrackingState) -> (String, String?) {
        switch t {
        case .normal: return ("normal", nil)
        case .notAvailable: return ("not_available", nil)
        case .limited(let r):
            switch r {
            case .initializing: return ("limited", "initializing")
            case .excessiveMotion: return ("limited", "excessive_motion")
            case .insufficientFeatures: return ("limited", "insufficient_features")
            case .relocalizing: return ("limited", "relocalizing")
            @unknown default: return ("limited", "unknown")
            }
        }
    }

    static func trackingQuality(_ t: ARCamera.TrackingState) -> TrackingQuality {
        switch t {
        case .normal: return .normal
        case .notAvailable: return .notAvailable
        case .limited: return .limited
        }
    }

    static func mappingString(_ m: ARFrame.WorldMappingStatus) -> String {
        switch m {
        case .notAvailable: return "not_available"
        case .limited: return "limited"
        case .extending: return "extending"
        case .mapped: return "mapped"
        @unknown default: return "unknown"
        }
    }

    /// Exposure values the frame reports. Missing keys stay nil (never filled with defaults).
    static func exposure(_ frame: ARFrame) -> ExposureRecord {
        var e = ExposureRecord()
        let exif = frame.exifData
        func num(_ k: String) -> Double? {
            if let n = exif[k] as? NSNumber { return n.doubleValue }
            if let a = exif[k] as? [NSNumber], let f = a.first { return f.doubleValue }
            return nil
        }
        e.exposureDurationS = finiteOrNil(num("ExposureTime"))
        e.iso = finiteOrNil(num("ISOSpeedRatings"))
        e.brightnessValue = finiteOrNil(num("BrightnessValue"))
        e.exposureBiasEV = finiteOrNil(num("ExposureBiasValue"))
        if let l = frame.lightEstimate {
            e.ambientIntensityLumen = finiteOrNil(Double(l.ambientIntensity))
            e.ambientColorTemperatureK = finiteOrNil(Double(l.ambientColorTemperature))
        }
        return e
    }

    /// Copy the camera's luma plane into a small image for quality metrics (no retained reference to the frame).
    static func luma(_ pb: CVPixelBuffer) -> LumaImage? {
        guard CVPixelBufferGetPlaneCount(pb) >= 1 else { return nil }
        CVPixelBufferLockBaseAddress(pb, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pb, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddressOfPlane(pb, 0) else { return nil }
        return LumaImage.downsample(base: UnsafeRawPointer(base), width: CVPixelBufferGetWidthOfPlane(pb, 0),
                                    height: CVPixelBufferGetHeightOfPlane(pb, 0),
                                    bytesPerRow: CVPixelBufferGetBytesPerRowOfPlane(pb, 0))
    }

    struct DepthCopy {
        let depth: Data
        let confidence: Data?
        let width: Int
        let height: Int
        let validFraction: Double
    }

    /// Tightly packed float32 depth (and uint8 confidence) copied row by row (CoreVideo rows may be padded).
    static func copyDepth(_ d: ARDepthData) -> DepthCopy? {
        let dm = d.depthMap
        guard CVPixelBufferGetPixelFormatType(dm) == kCVPixelFormatType_DepthFloat32 else { return nil }
        let w = CVPixelBufferGetWidth(dm), h = CVPixelBufferGetHeight(dm)
        var out = Data(count: w * h * 4)
        var valid = 0
        CVPixelBufferLockBaseAddress(dm, .readOnly)
        if let base = CVPixelBufferGetBaseAddress(dm) {
            let stride = CVPixelBufferGetBytesPerRow(dm)
            out.withUnsafeMutableBytes { dst in
                for y in 0..<h {
                    let row = base.advanced(by: y * stride).assumingMemoryBound(to: Float32.self)
                    for x in 0..<w {
                        let v = row[x]
                        if v.isFinite && v > 0 { valid += 1 }
                        dst.storeBytes(of: v.bitPattern.littleEndian, toByteOffset: (y * w + x) * 4, as: UInt32.self)
                    }
                }
            }
        }
        CVPixelBufferUnlockBaseAddress(dm, .readOnly)
        var conf: Data? = nil
        if let cm = d.confidenceMap, CVPixelBufferGetWidth(cm) == w, CVPixelBufferGetHeight(cm) == h,
           CVPixelBufferGetPixelFormatType(cm) == kCVPixelFormatType_OneComponent8 {
            var c = Data(count: w * h)
            CVPixelBufferLockBaseAddress(cm, .readOnly)
            if let base = CVPixelBufferGetBaseAddress(cm) {
                let stride = CVPixelBufferGetBytesPerRow(cm)
                c.withUnsafeMutableBytes { dst in
                    for y in 0..<h {
                        dst.baseAddress!.advanced(by: y * w).copyMemory(from: base.advanced(by: y * stride), byteCount: w)
                    }
                }
            }
            CVPixelBufferUnlockBaseAddress(cm, .readOnly)
            conf = c
        }
        return DepthCopy(depth: out, confidence: conf, width: w, height: h, validFraction: Double(valid) / Double(max(1, w * h)))
    }

    /// Sparse world points from LiDAR depth for coverage (stride grid, medium/high confidence only).
    static func depthSamples(_ d: ARDepthData, camera: ARCamera, step: Int = 16) -> [SIMD3<Double>] {
        let dm = d.depthMap
        let w = CVPixelBufferGetWidth(dm), h = CVPixelBufferGetHeight(dm)
        let k = intrinsics(camera).scaled(toWidth: w, height: h)
        let T = Pose(camera.transform).matrix
        var pts: [SIMD3<Double>] = []
        CVPixelBufferLockBaseAddress(dm, .readOnly)
        let cm = d.confidenceMap
        if let cm { CVPixelBufferLockBaseAddress(cm, .readOnly) }
        defer {
            CVPixelBufferUnlockBaseAddress(dm, .readOnly)
            if let cm { CVPixelBufferUnlockBaseAddress(cm, .readOnly) }
        }
        guard let base = CVPixelBufferGetBaseAddress(dm) else { return [] }
        let stride = CVPixelBufferGetBytesPerRow(dm)
        let cbase = cm.flatMap { CVPixelBufferGetBaseAddress($0) }
        let cstride = cm.map { CVPixelBufferGetBytesPerRow($0) } ?? 0
        var y = step / 2
        while y < h {
            var x = step / 2
            while x < w {
                let z = Double(base.advanced(by: y * stride).assumingMemoryBound(to: Float32.self)[x])
                let conf = cbase.map { $0.advanced(by: y * cstride).assumingMemoryBound(to: UInt8.self)[x] } ?? 2
                if z.isFinite, z > 0.1, z < 8, conf >= 1 {
                    // pixel -> ARKit camera coordinates (+X right, +Y up, -Z forward; image v grows downward)
                    let pc = SIMD4((Double(x) + 0.5 - k.cx) / k.fx * z, -(Double(y) + 0.5 - k.cy) / k.fy * z, -z, 1)
                    let pw = T * pc
                    pts.append(SIMD3(pw.x, pw.y, pw.z))
                }
                x += step
            }
            y += step
        }
        return pts
    }
}
