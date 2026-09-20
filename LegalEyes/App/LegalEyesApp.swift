import SwiftUI
import MWDATCore
#if DEBUG
import MWDATMockDevice
#endif

@main
struct LegalEyesApp: App {
    /// nil when `Wearables.configure()` threw (e.g. placeholder MWDAT plist values).
    @State private var glasses: GlassesController?
    @State private var configureError: String?

    init() {
        var configureError: String? = nil
        var configured = false
        do {
            try Wearables.configure()
            configured = true
        } catch {
            configureError = "Wearables.configure failed: \(error)"
            print("[LegalEyes] \(configureError!)")
        }

        #if DEBUG
        // XCUITests launch with --ui-testing and drive MockDeviceKit over HTTP.
        if ProcessInfo.processInfo.arguments.contains("--ui-testing") {
            MockDeviceKit.shared.enable(config: MockDeviceKitConfig(initiallyRegistered: false))
            let portFile = ProcessInfo.processInfo.environment["MWDAT_TEST_SERVER_PORT_FILE"]
            Task { _ = try? await MockDeviceKit.shared.startTestServer(portFilePath: portFile) }
        }
        #endif

        _glasses = State(wrappedValue: configured ? GlassesController(wearables: Wearables.shared) : nil)
        _configureError = State(wrappedValue: configureError)
    }

    var body: some Scene {
        WindowGroup {
            if let glasses {
                ContentView(glasses: glasses, configureError: nil)
                    // The Meta AI app returns to us on legaleyes:// after registration.
                    .onOpenURL { url in Task { await glasses.handle(url: url) } }
            } else {
                NavigationStack {
                    List {
                        Section("Toolkit not configured") {
                            Text(configureError ?? "Unknown error").foregroundStyle(.red)
                            Text("Fill LegalEyes/Config/Secrets.xcconfig with real MetaAppID, ClientToken and Team ID, then rebuild.")
                                .font(.footnote)
                        }
                    }
                    .navigationTitle("LegalEyes")
                }
            }
        }
    }
}
