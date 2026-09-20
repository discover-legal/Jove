import SwiftUI
import MWDATCore
#if DEBUG
import MWDATMockDevice
#endif

@main
struct LegalEyesApp: App {
    @State private var glasses: GlassesController
    @State private var configureError: String? = nil

    init() {
        var configureError: String? = nil
        do {
            try Wearables.configure()
        } catch {
            configureError = "Wearables.configure failed: \(error.localizedDescription)"
        }

        #if DEBUG
        // XCUITests launch with --ui-testing and drive MockDeviceKit over HTTP.
        if ProcessInfo.processInfo.arguments.contains("--ui-testing") {
            MockDeviceKit.shared.enable(config: MockDeviceKitConfig(initiallyRegistered: false))
            let portFile = ProcessInfo.processInfo.environment["MWDAT_TEST_SERVER_PORT_FILE"]
            Task { _ = try? await MockDeviceKit.shared.startTestServer(portFilePath: portFile) }
        }
        #endif

        _glasses = State(wrappedValue: GlassesController(wearables: Wearables.shared))
        _configureError = State(wrappedValue: configureError)
    }

    var body: some Scene {
        WindowGroup {
            ContentView(glasses: glasses, configureError: configureError)
                // The Meta AI app returns to us on legaleyes:// after registration.
                .onOpenURL { url in Task { await glasses.handle(url: url) } }
        }
    }
}
