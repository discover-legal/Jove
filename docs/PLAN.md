# LegalEyes — Delivery Plan

Companion to `PRD.md`. Each slice ends with a CI-green build, a demo path
that works in the Simulator with MockDeviceKit, and a commit on `main`.
Decisions referenced as D-numbers come from the PRD decisions register.

## Working agreements
- Linux workstation authors code; GitHub Actions macOS runner is the
  compiler until a Mac is at hand. Expect the first slice to need a
  compile-fix pass.
- The `wearables` MCP server (`search_dat_docs`) is consulted before touching
  any DAT API.
- No new dependency without a line in this file explaining why.
- Every artifact type gets a ledger entry the moment it exists (FR-22).

## Slice 0 — Scaffold (done, commit b26fdc6)
XcodeGen project, DAT package, Info.plist, `GlassesController`,
`CustodyLedger`, mock support, CI workflow, discovery doc.

Exit criteria met 2026-09-20: CI builds on macOS 26 / Xcode 26.6 against an
iPhone 17 Pro simulator and all tests pass. DAT API names were verified
against Meta's CameraAccess sample (async registration, `ListenerTokenBag`,
`Camera`/`Stream`, six-state `DeviceSessionState`, `MockGlasses`).

## Slice 0.5 — Matter-type catalog (done)
`Resources/MatterTypes.json` with 32 defaults across 10 categories and
per-matter custom fields; `MatterTypeCatalog` loader with remote override.

## Slice 1 — Compile, register, stream (est. 2–3 sessions)
1. ~~Push, read CI errors, correct DAT type names.~~ Done in slice 0.
2. Deployment target iOS 26 (D15). Adopt `@Observable`. Add an
   Apple-Intelligence availability check on launch.
3. Add bundled `sample-doc.mp4` (H.265) and `sample-page.jpg` for mock.
4. XCUITest: launch with `--ui-testing`, start mock test server, pair, power
   on, don, verify stream state reaches `streaming`.
5. On the iPhone 17 Pro: Developer Mode registration with Oakley Meta,
   camera permission, live preview, manual capture. Record firmware and Meta
   AI versions in README.
6. **Hardware checklist** (results go in `docs/hardware-findings.md`):
   - [ ] Capture 5 photos via the app. Then open the Meta AI app gallery and
         the glasses' own media: do any of the 5 appear? Wait 10 minutes on
         Wi-Fi and check again. Check iOS Photos too.
   - [ ] With the phone on a monitored Wi-Fi network (mitmproxy or router
         logs), capture 5 photos and note every host contacted by the app
         and by the Meta AI app during the session.
   - [ ] Legibility: photograph a dense single-spaced letter, a two-column
         contract page, a driver's licence, and a passport data page at
         30 cm, 45 cm, and 60 cm while seated. Run Vision OCR on each and
         record character accuracy against the known text.
   - [ ] Glasses battery: stream continuously and log battery every 10
         minutes until 20 percent. Repeat with capture-on-demand only.
   - [ ] Backgrounding: while streaming, take a phone call, lock the phone,
         and switch apps. Record what state the session and stream land in
         and whether they recover on return.
   - [ ] Bluetooth audio: check whether the glasses appear as an audio
         input route on the phone and record 60 seconds through it. Compare
         quality against the phone mic.
   - [ ] Captouch: tap and tap-and-hold while streaming; record what the
         SDK reports.

Exit: manual page capture from real glasses lands in the ledger, and every
checklist item has a recorded answer.

## Slice 2 — Document detection and processing (est. 3 sessions)
1. `DocumentDetector`: `VNDetectDocumentSegmentationRequest` on each frame,
   stability gate (FR-9), perceptual hash de-duplication, cooldown.
2. Auto-capture wiring to `capturePhoto` with haptic and flash (FR-10).
3. Manual override button; investigate captouch exposure in DAT (FR-11).
4. `PageProcessor`: crop to quad, `CIPerspectiveCorrection`, JPEG, then
   `VNRecognizeTextRequest` on device (FR-12). Three ledger entries per page.
5. Phone camera fallback using `AVCaptureSession` behind the same
   `FrameSource` protocol as the glasses stream (FR-14, D11).
6. Page strip UI with tag (ID front/back) and delete (FR-13).

Exit: hold three different pages up to the glasses; three corrected pages
with OCR text appear, no duplicates, no false captures on the desk.

## Slice 3 — Audio and transcript (est. 2 sessions)
1. `AudioRecorder`: `AVAudioEngine` to AAC, 60-second chunk rotation, chunk
   ledger entries (FR-15). Background audio mode.
2. `Transcriber`: `SFSpeechRecognizer` with on-device recognition, per-segment
   timestamps, language picker en-US/en-CA/en-GB (FR-16, FR-17, D20).
3. Running transcript view.

Exit: 10-minute test interview yields 10 chunks and a segmented transcript.

## Slice 4 — Intake form and parties (est. 3 sessions)
1. Data model (GRDB): Interview, FormSnapshot, Party, Page, AudioChunk,
   TranscriptSegment, LedgerEntry.
2. Form UI per FR-18 with autosave and hash-per-save ledger entries.
3. Party editor per FR-19 with role and kind.
4. `Suggester`: Foundation Models `LanguageModelSession` with `@Generable`
   `PartyCandidate` and `FieldCandidate` types, fed incrementally with
   transcript segments and OCR text; NaturalLanguage `NLTagger` pre-filter;
   chips UI; accept/dismiss events (FR-20, D17). Degraded path when the
   model is unavailable (FR-20b).
4a. Interview summary generation at end (FR-20a), editable before finalize.
5. `MatterTypeCatalog` (done in slice 0.5): bundled JSON plus `/v1/config`
   override and cache (FR-18).
6. Consent attestation gate with "who is present" list (FR-6). Home screen
   with both entry points (D21).
7. Interview templates per matter type: the catalog's `fields` drive extra
   questions; add a `questions` array to the JSON schema.
8. "Medical document" flag on a page that routes capture to the phone
   camera and excludes it from glasses data (compliance C2).
9. Manual "client speaking" toggle to tag transcript segments, since
   on-device Speech has no diarization.

Exit: complete an interview end to end offline; export ledger JSON verifies.

## Slice 5 — Security (est. 3 sessions)
1. Secure Enclave key, per-interview AES-GCM keys, encrypted file store and
   encrypted SQLite (FR-25). Exclude the data directory from iCloud backup.
1a. Sign every ledger entry with a Secure Enclave P-256 key; publish the
   public key at interview creation so the server can verify authorship,
   not just ordering (FR-22a).
1b. App Attest on the firm API to bind uploads to a genuine app instance.
2. Face ID gate on launch and after 5 minutes background (FR-26, D19).
3. Network allow-list wrapper around `URLSession` (FR-27).
4. Retention job: purge 30 days after ack (FR-28, D18).

Exit: files on disk are unreadable without the app; Face ID prompts as specified.

## Slice 6 — Identity and sync (est. 3 sessions)
1. Google Sign-In with Drive scope; tokens in Keychain (FR-1, FR-2, D8).
2. `UploadQueue`: persistent, per-artifact, resumable, backoff (FR-29).
3. API client per PRD §8: create, artifact multipart, ack check, finalize,
   config (D14).
4. Finalize flow and upload progress UI (FR-8, FR-30). Server-issued
   timestamps recorded against each acknowledged entry.
4a. Amendments after finalize as new ledger entries (FR-22b).
5. Drive mirror: folder per interview, resumable, settings toggle default on,
   Drive file ids into the ledger (FR-31, D8). Test with a personal Drive
   before the firm Workspace.
6. Stub server in repo (small Node or Python) implementing §8 for local and
   CI testing until the firm API exists.

Exit: airplane-mode interview uploads fully once connectivity returns.

## Slice 7 — Hardening and release gate (est. 3 sessions)
0. Compliance deliverables: privacy policy, `SECURITY.md` with a reporting
   address, service-provider register complete, retention-rule citation
   (compliance C3, C4, C9).
1. XCUITest coverage of the full mock interview on CI (FR-33).
2. Release-build network audit with mitmproxy; document results (§7).
3. Ledger PDF receipt (FR-24).
4. Battery and latency measurements against §6.
5. Wearables Developer Center release channel build; install on the phone
   without Developer Mode. Confirm Apple-side distribution path for
   colleagues (internal TestFlight vs ad hoc) before inviting anyone.
6. Monthly smoke test reminder so camera permission is not revoked for
   inactivity (compliance C10).

Exit: all PRD functional requirements demonstrable; audit shows egress only
to firm API, Google, Apple, and Meta registration.

## v2 backlog (not scheduled)
- Glasses battery indicator and capture-on-demand mode if hardware tests
  show streaming drains the glasses too fast.
- Glasses microphone via Bluetooth headset route if the Slice 1 test shows
  acceptable quality.
- Client-facing consent screen and e-signature.
- Post-intake actions: engagement letter, retainer, conflict result
  feedback loop.
- Backend Claude extraction endpoint for parties and fields when on-device
  quality is insufficient (D17).
- KYC parsing (MRZ/OCR) and third-party verification (D9).
- Glasses audio when DAT exposes it (D4).
- Multi-lawyer, multi-firm (D16).
- French and other languages (D20).

## Dependencies to add and why
| Package | Slice | Why |
|---|---|---|
| meta-wearables-dat-ios | 0 | Glasses access |
| GRDB.swift | 4 | SQLite with encryption support and migrations |
| GoogleSignIn-iOS | 6 | Google identity and Drive scope |
| GoogleAPIClientForREST/Drive | 6 | Drive folder and resumable file upload |
| Nothing for OCR, speech, NLP, crypto | — | Apple frameworks only, per privacy posture |
