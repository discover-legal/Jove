import SwiftUI

@main
struct LegalEyesApp: App {
    @StateObject private var glasses = GlassesController()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(glasses)
                .task { await glasses.start() }
                // The Meta AI app calls back on legaleyes:// after registration.
                .onOpenURL { url in Task { await glasses.handle(url: url) } }
        }
    }
}
