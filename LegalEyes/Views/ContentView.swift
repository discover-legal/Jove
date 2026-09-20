import SwiftUI

struct ContentView: View {
    @Bindable var glasses: GlassesController
    let configureError: String?
    #if DEBUG
    @State private var mock = MockDeviceSupport()
    #endif

    var body: some View {
        NavigationStack {
            List {
                if let configureError {
                    Section { Text(configureError).foregroundStyle(.red) }
                }
                statusSection
                registrationSection
                sessionSection
                captureSection
                #if DEBUG
                mockSection
                #endif
                if let err = glasses.lastError {
                    Section("Last error") {
                        Text(err).font(.footnote).foregroundStyle(.red)
                        Button("Clear") { glasses.lastError = nil }
                    }
                }
            }
            .navigationTitle("LegalEyes")
        }
    }

    private var statusSection: some View {
        Section("Toolkit") {
            LabeledContent("Registration", value: "\(glasses.registrationState)")
            LabeledContent("Devices", value: glasses.devices.isEmpty ? "none" : "\(glasses.devices.count)")
            LabeledContent("Active device", value: glasses.hasActiveDevice ? "yes" : "no")
            LabeledContent("Camera permission", value: glasses.cameraPermission.map { "\($0)" } ?? "—")
        }
    }

    private var registrationSection: some View {
        Section("Registration") {
            Button("Register with Meta AI app") { Task { await glasses.register() } }
            Button("Unregister", role: .destructive) { Task { await glasses.unregister() } }
            Button("Check camera permission") { Task { await glasses.refreshCameraPermission() } }
            Button("Request camera permission") { Task { await glasses.requestCameraPermission() } }
        }
    }

    private var sessionSection: some View {
        Section("Session and stream") {
            LabeledContent("Session", value: "\(glasses.sessionState)")
            LabeledContent("Stream", value: "\(glasses.streamState)")
            Button("Start session") { glasses.startSession() }.disabled(glasses.hasSession)
            Button("Start streaming") { Task { await glasses.startStreaming() } }
                .disabled(glasses.sessionState != .started || glasses.streamState != .stopped)
            Button("Stop streaming") { glasses.stopStreaming() }.disabled(!glasses.isStreaming)
            Button("End session", role: .destructive) { glasses.endSession() }.disabled(!glasses.hasSession)
        }
    }

    private var captureSection: some View {
        Section("Capture") {
            if let frame = glasses.latestFrame {
                Image(uiImage: frame).resizable().scaledToFit().frame(maxHeight: 240)
            } else {
                Text("No live frame").foregroundStyle(.secondary)
            }
            Button("Capture page photo") { glasses.capturePhoto() }.disabled(!glasses.isStreaming)
            if let shot = glasses.lastCapture {
                if let img = shot.image {
                    Image(uiImage: img).resizable().scaledToFit().frame(maxHeight: 240)
                }
                Text("SHA-256 \(shot.sha256.prefix(16))…  \(shot.takenAt.formatted(date: .omitted, time: .standard))")
                    .font(.caption.monospaced())
            }
            NavigationLink("Custody ledger (\(glasses.ledgerEntries.count))") {
                LedgerView(entries: glasses.ledgerEntries)
            }
        }
    }

    #if DEBUG
    private var mockSection: some View {
        Section("MockDeviceKit (debug)") {
            Toggle("Enabled", isOn: Binding(get: { mock.enabled }, set: { mock.setEnabled($0) }))
            Button("Pair mock glasses") { mock.pairGlasses() }.disabled(!mock.enabled)
            Button("Power on + wear + unfold") { mock.wear() }.disabled(mock.device == nil)
            Button("Take off") { mock.takeOff() }.disabled(!mock.isDonned)
            Button("Use sample video feed") { mock.useSampleVideo() }.disabled(mock.device == nil)
            Button("Use sample capture image") { mock.useSampleCapture() }.disabled(mock.device == nil)
            if let err = mock.lastError { Text(err).font(.footnote).foregroundStyle(.red) }
        }
    }
    #endif
}

struct LedgerView: View {
    let entries: [CustodyLedger.Entry]
    var body: some View {
        List(entries) { e in
            VStack(alignment: .leading, spacing: 2) {
                Text("#\(e.sequence)  \(e.kind)  \(e.byteCount) B").font(.subheadline)
                Text(e.timestamp.formatted()).font(.caption).foregroundStyle(.secondary)
                Text("payload \(e.sha256.prefix(20))…").font(.caption.monospaced())
                Text("chain   \(e.chainHash.prefix(20))…").font(.caption.monospaced())
            }
        }
        .navigationTitle("Custody ledger")
    }
}
