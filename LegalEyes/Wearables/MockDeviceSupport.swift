import Foundation
#if DEBUG
import Observation
import MWDATMockDevice

/// Debug-only: drive the SDK without hardware. A mock device must be powered on,
/// donned, and unfolded before a session will start and stream.
@Observable
@MainActor
final class MockDeviceSupport {
    private(set) var enabled: Bool
    private(set) var device: MockGlasses? = nil
    private(set) var isPoweredOn = false
    private(set) var isDonned = false
    var lastError: String? = nil

    @ObservationIgnored private let kit: MockDeviceKitInterface

    init(kit: MockDeviceKitInterface = MockDeviceKit.shared) {
        self.kit = kit
        self.enabled = kit.isEnabled
        self.device = kit.pairedDevices.compactMap { $0 as? MockGlasses }.first
    }

    func setEnabled(_ on: Bool) {
        if on { kit.enable() } else { kit.disable(); device = nil; isPoweredOn = false; isDonned = false }
        enabled = on
    }

    func pairGlasses() {
        do { device = try kit.pairGlasses(model: .rayBanMeta) }
        catch { lastError = "pairGlasses: \(error.localizedDescription)" }
    }

    /// Power on, don, unfold in one step: the state a real user is in when they start an interview.
    func wear() {
        guard let device else { lastError = "Pair first"; return }
        device.powerOn(); isPoweredOn = true
        device.don(); isDonned = true
        device.unfold()
    }

    func takeOff() {
        device?.doff(); isDonned = false
    }

    /// Point the mock camera at a bundled H.265 clip (`sample-doc.mp4` in Resources).
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
