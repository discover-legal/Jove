import Foundation
#if DEBUG
import MWDATCore
import MWDATMockDevice

/// Debug-only helper so the whole flow can be exercised in the Simulator
/// and on CI without glasses. Production builds compile this out.
@MainActor
final class MockDeviceSupport: ObservableObject {
    @Published private(set) var enabled = false
    @Published private(set) var lastError: String? = nil
    private var device: MockDevice? = nil

    func enable() {
        MockDeviceKit.shared.enable()
        enabled = true
    }

    func disable() {
        MockDeviceKit.shared.disable()
        device = nil
        enabled = false
    }

    func pairGlasses() {
        do {
            device = try MockDeviceKit.shared.pairGlasses(model: .rayBanMeta)
        } catch {
            lastError = "pairGlasses: \(error)"
        }
    }

    /// Point the mock camera at a bundled H.265 clip (add `sample-doc.mp4` to the target).
    func useSampleVideo() {
        guard let url = Bundle.main.url(forResource: "sample-doc", withExtension: "mp4") else {
            lastError = "sample-doc.mp4 not in bundle"; return
        }
        device?.services.camera.setCameraFeed(fileURL: url)
    }

    func useSampleCapture() {
        guard let url = Bundle.main.url(forResource: "sample-page", withExtension: "jpg") else {
            lastError = "sample-page.jpg not in bundle"; return
        }
        device?.services.camera.setCapturedImage(fileURL: url)
    }
}
#endif
