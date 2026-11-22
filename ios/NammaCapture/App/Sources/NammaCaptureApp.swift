import NammaCaptureCore
import SwiftUI

@main
struct NammaCaptureApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            LibraryView()
                .environment(model)
        }
    }
}

/// App-wide state: the scan library on disk and which screen is showing.
@Observable @MainActor
final class AppModel {
    let store: SessionStore
    private(set) var sessions: [SessionSummary] = []
    var startupError: String?
    let capabilities = DeviceCapabilities.probe()

    init() {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        do {
            store = try SessionStore(root: docs.appendingPathComponent("Scans", isDirectory: true))
        } catch {
            // Fall back to a temporary folder so the app can still explain the problem instead of crashing.
            store = try! SessionStore(root: FileManager.default.temporaryDirectory.appendingPathComponent("Scans"))
            startupError = "Could not open the scan library: \(error.localizedDescription)"
        }
        refresh()
    }

    func refresh() { sessions = store.list() }

    var appVersion: String { Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0" }
    var appBuild: String { Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0" }
}
