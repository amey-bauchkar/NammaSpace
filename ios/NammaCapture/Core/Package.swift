// swift-tools-version:6.0
// NammaCaptureCore: everything in Namma Capture that does not need a camera (format, storage, export,
// validation, quality gate, keyframe choice, coverage bookkeeping, room/doorway state). Builds and tests on macOS
// with `swift test`, and is linked into the iOS app.
import PackageDescription

let package = Package(
    name: "NammaCaptureCore",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "NammaCaptureCore", targets: ["NammaCaptureCore"]),
               .executable(name: "nammacap-validate", targets: ["nammacap-validate"])],
    targets: [
        .target(name: "NammaCaptureCore"),
        .executableTarget(name: "nammacap-validate", dependencies: ["NammaCaptureCore"]),
        .testTarget(name: "NammaCaptureCoreTests", dependencies: ["NammaCaptureCore"]),
    ]
)
