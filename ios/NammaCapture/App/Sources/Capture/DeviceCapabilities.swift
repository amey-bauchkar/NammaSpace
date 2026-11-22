import ARKit
import AVFoundation
import NammaCaptureCore
import UIKit

/// What this device can do, asked of ARKit at runtime. Nothing is assumed from the model name.
struct DeviceCapabilities {
    var worldTracking: Bool
    var sceneDepth: Bool
    var smoothedSceneDepth: Bool
    var sceneReconstruction: Bool
    var highResFormat: ARConfiguration.VideoFormat?
    var configurableDevice: Bool
    var modelIdentifier: String

    static func probe() -> DeviceCapabilities {
        let wt = ARWorldTrackingConfiguration.isSupported
        return DeviceCapabilities(
            worldTracking: wt,
            sceneDepth: wt && ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth),
            smoothedSceneDepth: wt && ARWorldTrackingConfiguration.supportsFrameSemantics(.smoothedSceneDepth),
            sceneReconstruction: wt && ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh),
            highResFormat: wt ? ARWorldTrackingConfiguration.recommendedVideoFormatForHighResolutionFrameCapturing : nil,
            configurableDevice: wt && ARWorldTrackingConfiguration.configurableCaptureDeviceForPrimaryCamera != nil,
            modelIdentifier: Self.machine())
    }

    var hasLiDAR: Bool { sceneDepth }

    func manifestCapabilities(using format: ARConfiguration.VideoFormat?) -> Capabilities {
        Capabilities(
            worldTracking: worldTracking, sceneDepth: sceneDepth, smoothedSceneDepth: smoothedSceneDepth,
            sceneReconstructionMesh: sceneReconstruction, highResolutionFrameCapture: highResFormat != nil,
            configurableCaptureDevice: configurableDevice,
            videoFormat: format.map {
                VideoFormatInfo(width: Int($0.imageResolution.width), height: Int($0.imageResolution.height),
                                framesPerSecond: $0.framesPerSecond,
                                recommendedForHighResolutionCapture: $0.isRecommendedForHighResolutionFrameCapturing,
                                hdrSupported: $0.isVideoHDRSupported)
            })
    }

    var deviceInfo: DeviceInfo {
        DeviceInfo(modelIdentifier: modelIdentifier, systemName: UIDevice.current.systemName,
                   systemVersion: UIDevice.current.systemVersion)
    }

    /// Human-readable lines for the start screen.
    var summaryLines: [(String, Bool, String)] {
        [("Motion tracking (ARKit world tracking)", worldTracking, worldTracking ? "Available" : "Not supported: scanning is not possible on this device"),
         ("LiDAR depth", sceneDepth, sceneDepth ? "Available: measured depth is saved with each photo" : "Not on this device: photos and poses only (depth is never estimated)"),
         ("High-resolution stills", highResFormat != nil, highResFormat.map { "\(Int($0.imageResolution.width))x\(Int($0.imageResolution.height)) stream; full-resolution stills" } ?? "Not available: video-stream frames are saved"),
         ("Exposure / white-balance lock", configurableDevice, configurableDevice ? "Supported via ARKit's camera device" : "Not supported while ARKit runs: exposure is recorded, not locked")]
    }

    private static func machine() -> String {
        #if targetEnvironment(simulator)
        return ProcessInfo.processInfo.environment["SIMULATOR_MODEL_IDENTIFIER"].map { "simulator:\($0)" } ?? "simulator"
        #else
        var s = utsname()
        uname(&s)
        return withUnsafeBytes(of: &s.machine) { String(decoding: $0.prefix { $0 != 0 }, as: UTF8.self) }
        #endif
    }
}
