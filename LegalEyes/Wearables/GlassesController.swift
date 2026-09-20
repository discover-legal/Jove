import Foundation
import SwiftUI
import MWDATCore
import MWDATCamera

/// Single owner of the DAT lifecycle: configure → register → session → stream.
/// Everything the UI needs is published here so views stay dumb.
@MainActor
final class GlassesController: ObservableObject {

    enum Phase: Equatable {
        case notConfigured
        case configured
        case failed(String)
    }

    @Published private(set) var phase: Phase = .notConfigured
    @Published private(set) var registrationState: RegistrationState? = nil
    @Published private(set) var devices: [DeviceIdentifier] = []
    @Published private(set) var sessionState: DeviceSessionState? = nil
    @Published private(set) var streamState: StreamState? = nil
    @Published private(set) var cameraPermission: PermissionStatus? = nil
    @Published private(set) var latestFrame: UIImage? = nil
    @Published private(set) var lastCapture: CapturedPhoto? = nil
    @Published private(set) var lastError: String? = nil

    struct CapturedPhoto: Identifiable {
        let id = UUID()
        let data: Data
        let takenAt: Date
        let sha256: String
        var image: UIImage? { UIImage(data: data) }
    }

    private var session: DeviceSession? = nil
    private var stream: CameraStream? = nil
    private var listenerTokens: [AnyObject] = []
    private var observerTasks: [Task<Void, Never>] = []
    private let ledger = CustodyLedger()

    // MARK: - Lifecycle

    /// Called once from the app entry. Idempotent.
    func start() async {
        guard phase == .notConfigured else { return }
        do {
            try Wearables.configure()
            phase = .configured
            observe()
        } catch {
            phase = .failed("configure: \(error)")
        }
    }

    private func observe() {
        let w = Wearables.shared
        observerTasks.append(Task { [weak self] in
            for await state in w.registrationStateStream() {
                await MainActor.run { self?.registrationState = state }
            }
        })
        observerTasks.append(Task { [weak self] in
            for await list in w.devicesStream() {
                await MainActor.run { self?.devices = list }
            }
        })
    }

    // MARK: - Registration (Meta AI app round-trip)

    func register() {
        do { try Wearables.shared.startRegistration() }
        catch { lastError = "startRegistration: \(error)" }
    }

    func unregister() {
        stopSession()
        do { try Wearables.shared.startUnregistration() }
        catch { lastError = "startUnregistration: \(error)" }
    }

    func handle(url: URL) async {
        do { _ = try await Wearables.shared.handleUrl(url) }
        catch { lastError = "handleUrl: \(error)" }
    }

    // MARK: - Permissions

    func refreshCameraPermission() async {
        do { cameraPermission = try await Wearables.shared.checkPermissionStatus(.camera) }
        catch { lastError = "checkPermissionStatus: \(error)" }
    }

    func requestCameraPermission() async {
        do { cameraPermission = try await Wearables.shared.requestPermission(.camera) }
        catch { lastError = "requestPermission: \(error)" }
    }

    // MARK: - Session + camera stream

    func startSession() {
        guard session == nil else { return }
        let w = Wearables.shared
        do {
            let s = try w.createSession(deviceSelector: AutoDeviceSelector(wearables: w))
            session = s
            let states = s.stateStream()
            observerTasks.append(Task { [weak self] in
                for await state in states {
                    await MainActor.run { self?.onSession(state: state) }
                }
            })
            try s.start()
        } catch {
            lastError = "createSession/start: \(error)"
            session = nil
        }
    }

    private func onSession(state: DeviceSessionState) {
        sessionState = state
        switch state {
        case .started:
            attachCamera()
        case .paused:
            // Keep the session; streams pause on their own. Do not restart.
            break
        case .stopped:
            // Glasses folded, captouch hold, or remote stop. Release and let the user restart.
            tearDownStream()
            session = nil
        default:
            break
        }
    }

    private func attachCamera() {
        guard let session, stream == nil else { return }
        do {
            // Low res is enough for document detection; capturePhoto gives the full-res page.
            let config = StreamConfiguration(videoCodec: .raw, resolution: .low, frameRate: 15)
            guard let camera = try session.addCamera(config: config) else {
                lastError = "addCamera returned nil (no camera on selected device?)"
                return
            }
            let s = camera.stream
            stream = s
            listenerTokens.append(s.statePublisher.listen { [weak self] state in
                Task { @MainActor in self?.streamState = state }
            })
            listenerTokens.append(s.videoFramePublisher.listen { [weak self] frame in
                guard let image = frame.makeUIImage() else { return }
                Task { @MainActor in self?.latestFrame = image }
            })
            listenerTokens.append(s.photoDataPublisher.listen { [weak self] photo in
                Task { @MainActor in self?.onPhoto(photo.data) }
            })
            s.start()
        } catch {
            lastError = "addCamera: \(error)"
        }
    }

    func capturePhoto() {
        stream?.capturePhoto(format: .jpeg)
    }

    private func onPhoto(_ data: Data) {
        let entry = ledger.append(kind: "glasses.photo.jpeg", payload: data,
                                  deviceId: devices.first.map { "\($0)" } ?? "unknown")
        lastCapture = CapturedPhoto(data: data, takenAt: entry.timestamp, sha256: entry.sha256)
    }

    func stopSession() {
        tearDownStream()
        session = nil
        sessionState = nil
    }

    private func tearDownStream() {
        listenerTokens.removeAll()
        stream = nil
        streamState = nil
        latestFrame = nil
    }

    var ledgerEntries: [CustodyLedger.Entry] { ledger.entries }
}
