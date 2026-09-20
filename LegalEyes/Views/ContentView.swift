import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var glasses: GlassesController
    #if DEBUG
    @StateObject private var mock = MockDeviceSupport()
    #endif

    var body: some View {
        NavigationStack {
            List {
                statusSection
                registrationSection
                sessionSection
                captureSection
                #if DEBUG
                mockSection
                #endif
                if let err = glasses.lastError {
                    Section("Last error") { Text(err).font(.footnote).foregroundStyle(.red) }
                }
            }
            .navigationTitle("LegalEyes")
        }
    }

    private var statusSection: some View {
        Section("Toolkit") {
            LabeledContent("SDK", value: "\(glasses.phase)")
            LabeledContent("Registration", value: glasses.registrationState.map { "\($0)" } ?? "—")
            LabeledContent("Devices", value: glasses.devices.isEmpty ? "none" : "\(glasses.devices.count)")
            LabeledContent("Camera permission", value: glasses.cameraPermission.map { "\($0)" } ?? "—")
        }
    }

    private var registrationSection: some View {
        Section("Registration") {
            Button("Register with Meta AI app") { glasses.register() }
            Button("Unregister", role: .destructive) { glasses.unregister() }
            Button("Check camera permission") { Task { await glasses.refreshCameraPermission() } }
            Button("Request camera permission") { Task { await glasses.requestCameraPermission() } }
        }
    }

    private var sessionSection: some View {
        Section("Session") {
            LabeledContent("Session", value: glasses.sessionState.map { "\($0)" } ?? "none")
            LabeledContent("Stream", value: glasses.streamState.map { "\($0)" } ?? "none")
            Button("Start session") { glasses.startSession() }
            Button("Stop session") { glasses.stopSession() }
        }
    }

    private var captureSection: some View {
        Section("Capture") {
            if let frame = glasses.latestFrame {
                Image(uiImage: frame).resizable().scaledToFit().frame(maxHeight: 240)
            } else {
                Text("No live frame").foregroundStyle(.secondary)
            }
            Button("Capture page photo") { glasses.capturePhoto() }
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
            Toggle("Enabled", isOn: Binding(
                get: { mock.enabled },
                set: { $0 ? mock.enable() : mock.disable() }))
            Button("Pair mock glasses") { mock.pairGlasses() }.disabled(!mock.enabled)
            Button("Use sample video feed") { mock.useSampleVideo() }.disabled(!mock.enabled)
            Button("Use sample capture image") { mock.useSampleCapture() }.disabled(!mock.enabled)
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
