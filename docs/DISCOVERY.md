# LegalEyes — Discovery (2026-09-20)

## Purpose
iOS companion app for lawyers running client intake interviews while wearing
Meta smart glasses. The glasses capture document pages hands-free; the phone
app captures interview audio and basic client data, builds a tamper-evident
evidence bundle, and syncs it to the firm's internal system for case-file
setup and automatic conflict checks. Optional: ID capture for KYC.

## Confirmed facts
| Topic | Decision |
|---|---|
| Users | Lawyers, commercial use inside their own territory. Consent handled by the lawyer, not the app. |
| Hardware | Oakley Meta glasses; iPhone 17 Pro for testing. |
| Capture | Live video stream from glasses (for document detection), still photo capture (JPEG), phone-mic audio for the interview. |
| Audio from glasses | Not in the documented DAT API (v0.9.0). Phone microphone is the accepted fallback. Revisit when DAT exposes microphone. |
| Data flow | Device → firm servers or an approved third-party provider. Nothing is sent to Meta. |
| Chain of custody | Required end to end: per-item SHA-256, hash-chained ledger, timestamps, device identity, operator identity. |
| Identity | Standalone app, Google Sign-In for the lawyer. |
| Distribution | Developer Mode / Wearables release channels are acceptable for now. App Store is not possible with DAT (ExternalAccessory). |
| Team | Solo developer. Linux workstation, occasional Mac access. CI on GitHub Actions macOS runners. |
| Milestone 1 | Run an interview: capture basic client data, auto-capture photos of documents held up to the glasses. |

## What "nothing to Meta" means in practice
- Registration and permission grants go through the Meta AI app on the phone.
  That is unavoidable with DAT; it is app-level attestation, not content.
- Per the DAT docs, video frames and photos travel glasses → phone over
  Bluetooth Classic. They do not pass through Meta servers.
- We cannot audit the closed SDK for telemetry. Mitigations: no analytics
  SDKs, network egress limited to our own endpoints, and a release-build
  network review before any client use.
- Audio never touches the glasses in milestone 1 (phone mic only).

## DAT constraints that shape the design
- SDK 0.9.0, iOS 15.2+, Xcode 14+, Meta AI app V282+, glasses firmware V126.
- Only one Developer Mode app registered at a time per Meta AI install.
- Stream max 720x1280 @ 30 fps, adaptive quality ladder over Bluetooth.
  Photo capture is the path to legible document pages, not the video stream.
- Session states: started / paused / stopped. Folding the glasses forces
  stopped; a new session must be created afterward.
- MockDeviceKit runs in the Simulator and on CI, so the whole flow is
  testable without hardware.

## Milestone plan
1. **Slice 0 (this commit)** — project skeleton, Info.plist, SDK package,
   `Wearables.configure()`, registration, session lifecycle, camera stream
   preview, manual photo capture, MockDeviceKit debug toggle, CI build.
2. **Slice 1** — document detection on the video stream (Vision
   `VNDetectDocumentSegmentationRequest`), stability gate, auto `capturePhoto`,
   perspective-corrected page image.
3. **Slice 2** — interview model (client fields), phone-mic recording,
   custody ledger for every artifact, local encrypted store.
4. **Slice 3** — Google Sign-In, upload to firm endpoint (multipart + ledger
   manifest), conflict-check trigger, KYC ID capture.

## Open questions
- Firm backend API shape (REST? existing case-management vendor?).
- Which third-party AI/OCR provider is approved, if any, and its data-residency terms.
- Whether interview audio needs live transcription or only recording.
- Retention and deletion policy on the phone after successful sync.
