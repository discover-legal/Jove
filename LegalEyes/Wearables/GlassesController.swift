import Foundation
import Observation
import SwiftUI
import MWDATCore
import MWDATCamera

/// Single owner of the DAT lifecycle: configure → register → session → camera → stream.
/// Mirrors the SDK's own state enums so the UI shows the real state machine.
@Observable
@MainActor
final class GlassesController {

    // MARK: - Published state

    private(set) var configured = false
    private(set) var registrationState: RegistrationState
    private(set) var devices: [DeviceIdentifier]
    private(set) var hasActiveDevice = false
    private(set) var sessionState: DeviceSessionState = .idle
    private(set) var streamState: StreamState = .stopped
    private(set) var cameraPermission: PermissionStatus? = nil
    private(set) var latestFrame: UIImage? = nil
    private(set) var lastCapture: CapturedPhoto? = nil
    private(set) var isCapturingPhoto = false
    var lastError: String? = nil

    struct CapturedPhoto: Identifiable {
        let id = UUID()
        let data: Data
        let takenAt: Date
        let sha256: String
        var image: UIImage? { UIImage(data: data) }
    }

    // MARK: - Private

    @ObservationIgnored private let wearables: WearablesInterface
    @ObservationIgnored private let deviceSelector: AutoDeviceSelector
    @ObservationIgnored private var session: DeviceSession? = nil
    @ObservationIgnored private var camera: MWDATCamera.Camera? = nil
    @ObservationIgnored private let sessionTokens = ListenerTokenBag()
    @ObservationIgnored private let streamTokens = ListenerTokenBag()
    @ObservationIgnored private var tasks: [Task<Void, Never>] = []
    @ObservationIgnored private let ledger = CustodyLedger()

    /// `Wearables.configure()` must have been called before this init.
    init(wearables: WearablesInterface = Wearables.shared) {
        self.wearables = wearables
        self.deviceSelector = AutoDeviceSelector(wearables: wearables)
        self.registrationState = wearables.registrationState
        self.devices = wearables.devices
        self.configured = true

        tasks.append(Task { [weak self] in
            for await state in wearables.registrationStateStream() {
                self?.registrationState = state
            }
        })
        tasks.append(Task { [weak self] in
            for await list in wearables.devicesStream() {
                self?.devices = list
            }
        })
        tasks.append(Task { [weak self] in
            guard let self else { return }
            for await id in deviceSelector.activeDeviceStream() {
                self.hasActiveDevice = id != nil
            }
        })
    }

    isolated deinit {
        tasks.forEach { $0.cancel() }
        session?.stop()
    }

    // MARK: - Registration (Meta AI app round-trip)

    func register() async {
        guard registrationState != .registering else { return }
        do { try await wearables.startRegistration() }
        catch { lastError = "startRegistration: \(error.localizedDescription)" }
    }

    func unregister() async {
        endSession()
        do { try await wearables.startUnregistration() }
        catch { lastError = "startUnregistration: \(error.localizedDescription)" }
    }

    func handle(url: URL) async {
        do { _ = try await wearables.handleUrl(url) }
        catch { lastError = "handleUrl: \(error.localizedDescription)" }
    }

    // MARK: - Permissions

    func refreshCameraPermission() async {
        do { cameraPermission = try await wearables.checkPermissionStatus(.camera) }
        catch { lastError = "checkPermissionStatus: \(error.localizedDescription)" }
    }

    /// Opens the Meta AI app for the grant dialog and resumes when the user returns.
    func requestCameraPermission() async {
        do { cameraPermission = try await wearables.requestPermission(.camera) }
        catch { lastError = "requestPermission: \(error.localizedDescription)" }
    }

    // MARK: - Session

    var hasSession: Bool {
        switch sessionState {
        case .starting, .started, .paused, .stopping: return true
        case .idle, .stopped: return false
        }
    }

    func startSession() {
        guard !hasSession else { return }
        do throws(DeviceSessionError) {
            let s = try wearables.createSession(deviceSelector: deviceSelector)
            session = s
            s.statePublisher.listen { [weak self] state in
                Task { @MainActor in self?.onSession(state: state) }
            }.store(in: sessionTokens)
            s.errorPublisher.listen { [weak self] error in
                Task { @MainActor in self?.lastError = "session: \(error.localizedDescription)" }
            }.store(in: sessionTokens)
            sessionState = .starting
            try s.start()
        } catch {
            lastError = "createSession/start: \(error.localizedDescription)"
            sessionState = .idle
            cleanupSession()
        }
    }

    /// Terminal. The SDK cascades the stop to the camera and stream.
    func endSession() {
        guard let session else { return }
        sessionState = .stopping
        session.stop()
    }

    private func onSession(state: DeviceSessionState) {
        sessionState = state
        if state == .stopped { cleanupSession() }
    }

    private func cleanupSession() {
        sessionTokens.clear()
        session = nil
    }

    // MARK: - Camera stream

    var isStreaming: Bool { streamState == .streaming }

    /// Requires a started session and granted camera permission.
    func startStreaming() async {
        guard let session, session.state == .started else {
            lastError = "Start a session before streaming."; return
        }
        guard camera == nil else { return }
        do {
            if try await wearables.checkPermissionStatus(.camera) != .granted {
                guard try await wearables.requestPermission(.camera) == .granted else {
                    lastError = "Camera permission denied"; return
                }
            }
        } catch {
            lastError = "permission: \(error.localizedDescription)"; return
        }
        beginStream(on: session)
    }

    private func beginStream(on session: DeviceSession) {
        // Raw frames so `makeUIImage()` works without a decoder. Low res is enough
        // for document detection; `capturePhoto` gives the full-resolution page.
        let config = StreamConfiguration(videoCodec: .raw, resolution: .low, frameRate: 15)
        do {
            guard let cam = try session.addCamera(config: config) else {
                lastError = "addCamera returned nil"; return
            }
            camera = cam
            let stream = cam.stream
            stream.statePublisher.listen { [weak self] state in
                Task { @MainActor in self?.onStream(state: state) }
            }.store(in: streamTokens)
            stream.videoFramePublisher.listen { [weak self] frame in
                guard let image = frame.makeUIImage() else { return }
                Task { @MainActor in
                    guard let self, !self.isCapturingPhoto else { return }
                    self.latestFrame = image
                }
            }.store(in: streamTokens)
            stream.photoDataPublisher.listen { [weak self] photo in
                Task { @MainActor in self?.onPhoto(photo) }
            }.store(in: streamTokens)
            stream.errorPublisher.listen { [weak self] error in
                Task { @MainActor in self?.lastError = "stream: \(error.localizedDescription)" }
            }.store(in: streamTokens)
            streamState = .starting
            stream.start()
        } catch {
            camera = nil
            lastError = "addCamera: \(error.localizedDescription)"
        }
    }

    /// Stops the camera but keeps the session, so streaming can restart cheaply.
    func stopStreaming() {
        guard let camera else { return }
        streamState = .stopping
        camera.stop()
    }

    private func onStream(state: StreamState) {
        streamState = state
        if state == .stopped {
            streamTokens.clear()
            camera?.stop()
            camera = nil
            latestFrame = nil
            isCapturingPhoto = false
        }
    }

    // MARK: - Photo capture

    func capturePhoto() {
        guard isStreaming, !isCapturingPhoto, let camera else {
            lastError = "Not streaming"; return
        }
        isCapturingPhoto = true
        if !camera.stream.capturePhoto(format: .jpeg) {
            isCapturingPhoto = false
            lastError = "capturePhoto refused (another capture in progress?)"
        }
    }

    private func onPhoto(_ photo: PhotoData) {
        isCapturingPhoto = false
        let deviceId = devices.first.map { wearables.deviceForIdentifier($0)?.nameOrId() ?? "\($0)" } ?? "unknown"
        let entry = ledger.append(kind: "page.raw", payload: photo.data, deviceId: deviceId)
        lastCapture = CapturedPhoto(data: photo.data, takenAt: entry.timestamp, sha256: entry.sha256)
    }

    var ledgerEntries: [CustodyLedger.Entry] { ledger.entries }
}
