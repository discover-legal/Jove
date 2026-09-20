# LegalEyes — Product Requirements Document

Version 0.1 — 2026-09-20. Owner: h@discover.legal. Status: agreed decisions from
discovery; every item below was an explicit choice, not an assumption.

## 1. Summary
LegalEyes is an iPhone app for a lawyer conducting a client intake interview
while wearing Meta smart glasses. During the interview the glasses camera
detects and photographs documents the client hands over, the phone records
and transcribes the conversation, and the lawyer fills a structured intake
form. Every artifact is hashed into a tamper-evident custody ledger,
encrypted on the phone, and streamed to the firm's own REST API as it is
captured. The output is a structured intake bundle, including typed party
records, that downstream firm systems use for case-file creation and
conflict checks. LegalEyes itself does not run conflicts.

## 2. Goals and non-goals
Goals
- Hands-free document capture with automatic detection and manual override.
- Complete intake record: form, audio, transcript, document pages, ID photos.
- Chain of custody for every artifact from capture to firm server.
- No interview content ever sent to Meta. No analytics or third-party SDKs
  beyond Google Sign-In and the DAT SDK.
- Works without glasses (phone camera fallback) and without hardware
  (MockDeviceKit) for development and demos.

Non-goals (v1)
- Conflict checking, case-file creation, billing: downstream systems.
- Multi-firm tenancy, multi-user roles: single user (the owner) in v1.
- App Store distribution: impossible with DAT; Developer Mode / release
  channel only.
- Glasses audio: not in DAT 0.9.0; phone microphone only.
- Cloud AI extraction: reserved for v2 via a backend endpoint.
- Languages other than English.

## 3. Users and context
- Primary user: one lawyer (the owner), Oakley Meta glasses, iPhone 17 Pro,
  iOS 17+. Later: a few lawyers at one firm.
- Setting: in-person intake in the lawyer's office or a client site.
  Consent to record is the lawyer's responsibility and is not enforced by
  the app, but the app records that the lawyer attested consent.
- Connectivity: cellular or Wi-Fi may be intermittent; uploads must queue.

## 4. Decisions register
| # | Topic | Decision |
|---|---|---|
| D1 | Documentation | PRD and plan live as Markdown in the repo. |
| D2 | Capture trigger | Automatic detection with manual override (phone button, glasses captouch). |
| D3 | Intake form | Full form: identity, contact, address, matter type, parties, referral source, urgency, notes, firm-defined custom fields. |
| D4 | Audio | Phone-mic recording plus on-device transcription (Apple Speech). |
| D5 | Backend | New REST API built by the owner; app targets the contract in §8. |
| D6 | Document processing | On-device crop, perspective correction, and OCR (Apple Vision). |
| D7 | Local protection | App-level encryption (CryptoKit envelope, key in Secure Enclave) plus Face ID on launch and after 5 minutes in background. |
| D8 | Google | Sign-in identity plus Drive upload scope. |
| D9 | KYC | Photo of ID front and back via glasses or phone; no parsing in v1. |
| D10 | Conflicts | Out of scope. App emits typed, structured party records only. |
| D11 | Device-less | MockDeviceKit toggle in debug builds and a phone-camera fallback in production. |
| D12 | Sync | Continuous: each artifact uploads as captured; interview finalized at end. |
| D13 | Party capture | Structured typed fields, entered by lawyer, with suggestions from transcript and OCR that the lawyer confirms. |
| D14 | Transport | JSON records for all metadata; binaries as multipart parts referenced by SHA-256. |
| D15 | iOS target | iOS 17+. |
| D16 | Users v1 | Owner only. |
| D17 | Suggestion engine | On-device Apple NaturalLanguage in v1; backend extraction endpoint reserved for v2. |
| D18 | Retention | Keep 30 days after server acknowledgement, then purge. Ledger manifest kept as receipt. |
| D19 | Biometrics | Face ID on launch and after 5 minutes in background. |
| D20 | Language | English only (en-US, en-CA, en-GB selectable). |
| D21 | Flow | Home screen offers both "Start with form" and "Start recording"; lawyer chooses per interview. |

## 5. Functional requirements

### 5.1 Onboarding and identity
- FR-1 Sign in with Google (firm Workspace account). Identity (email, subject
  id) is stamped on every ledger entry as the operator.
- FR-2 Request Drive scope at sign-in; store refresh token in Keychain.
- FR-3 Register with the Meta AI app via DAT; show registration state; allow
  unregister. Show glasses firmware and Meta AI version guidance when
  registration fails.
- FR-4 Request DAT camera permission; explain that the Meta AI app shows the
  grant dialog.

### 5.2 Interview lifecycle
- FR-5 Home screen lists interviews (draft, in progress, uploading, acknowledged,
  purged) with two entry points: "Start with form" and "Start recording".
- FR-6 Starting an interview creates an interview record with a UUID, start
  time, operator, and a consent attestation toggle the lawyer must set before
  recording begins.
- FR-7 During an interview the screen shows: live glasses preview (or phone
  camera), detection status, recording timer, running transcript, captured
  pages strip, and the form.
- FR-8 Ending an interview stops audio and stream, finalizes the transcript,
  and sends the finalize record. The interview then shows upload progress.

### 5.3 Document capture
- FR-9 Glasses stream at low resolution and 15 fps feeds Vision document
  segmentation. A page is "stable" when the detected quad moves less than a
  threshold for N consecutive frames (tunable, default 8 frames) and is not
  a duplicate of the last capture (perceptual hash distance threshold).
- FR-10 On stable, the app calls capturePhoto on the glasses and shows a
  haptic plus on-screen flash. Cooldown 2 seconds.
- FR-11 Manual override: phone "Capture" button, and glasses captouch tap
  mapped to capture if the DAT SDK exposes the gesture; otherwise phone only.
- FR-12 Each captured photo is cropped to the detected quad, perspective
  corrected, and OCR'd on device. The raw JPEG, the corrected image, and the
  OCR text are three separate ledger entries linked by a page id.
- FR-13 Lawyer can mark a page as "ID front" or "ID back" (KYC) or delete
  it. Deletion is a ledger event; the bytes are removed but the hash remains.
- FR-14 Phone camera fallback offers the same detection and capture using
  the iPhone rear camera when glasses are not connected.

### 5.4 Audio and transcript
- FR-15 Record phone microphone to AAC in an encrypted file; chunk every 60
  seconds so partial uploads and crash recovery are possible.
- FR-16 On-device Speech transcription with timestamps per segment. Segments
  are ledger entries. Final transcript assembled at end.
- FR-17 Selected language per interview from en-US, en-CA, en-GB.

### 5.5 Intake form and parties
- FR-18 Form sections: Client identity (name, DOB, phone, email), Address,
  Matter (type from firm list, description, urgency), Parties, Referral
  source, Notes, Custom fields (key/value schema fetched from the firm API,
  cached).
- FR-19 Party record: `{ id, role: client|adverse|related|witness|other,
  kind: person|organisation, givenNames, familyName, organisationName,
  aliases[], dob?, address?, source: typed|transcript|ocr, confirmed: bool }`.
- FR-20 Suggestions: NaturalLanguage name and organisation entities from
  transcript segments and OCR text appear as chips; lawyer accepts (becomes a
  confirmed party) or dismisses. Dismissals are recorded.
- FR-21 Form autosaves every change; each save is a ledger entry with the
  form's hash.

### 5.6 Chain of custody
- FR-22 Every artifact and event gets a ledger entry: SHA-256 of payload,
  hash chain over previous entry, timestamp, operator, device id (glasses
  serial or phone identifierForVendor), app version.
- FR-23 Ledger is verifiable on device and by the server; server rejects any
  bundle whose chain fails.
- FR-24 Manifest export: the ledger for an interview can be exported as JSON
  and as a human-readable PDF receipt.

### 5.7 Storage and security
- FR-25 All files encrypted with per-interview AES-GCM keys wrapped by a
  Secure Enclave key. Metadata in SQLite (GRDB) with the same envelope.
- FR-26 Face ID on launch and after 5 minutes in background; passcode
  fallback. Interview capture continues in background for audio only.
- FR-27 No content leaves the device except to the firm API, Google Drive
  (if enabled per firm), and Apple's on-device frameworks. Network egress is
  allow-listed in code.
- FR-28 Purge 30 days after server acknowledgement; ledger manifest retained.

### 5.8 Sync
- FR-29 Upload queue persists across launches; each artifact uploads as soon
  as its ledger entry exists. Exponential backoff, resumable per artifact.
- FR-30 Server acknowledges each artifact by hash; finalize is accepted only
  when all artifacts are acknowledged.
- FR-31 Optional Drive mirror: bundle folder per interview written after
  finalize (feature flag, off by default).

### 5.9 Development and demo
- FR-32 Debug builds expose MockDeviceKit controls and a bundled sample
  document video.
- FR-33 XCUITest suite drives the mock test server on CI.

## 6. Non-functional requirements
- Capture-to-preview latency under 300 ms on the phone.
- Detection false-capture rate under 1 in 20 pages in office lighting.
- Audio never lost on crash: at most the last 60-second chunk unflushed.
- Cold start to interview screen under 3 seconds after Face ID.
- Battery: one hour interview under 25 percent phone battery drain.
- Accessibility: Dynamic Type, VoiceOver labels on all controls.

## 7. Privacy and compliance
- Meta receives only registration and attestation traffic from the DAT SDK.
- Apple on-device Speech, Vision, and NaturalLanguage are used with
  `requiresOnDeviceRecognition = true` where the API offers it.
- Google receives the sign-in and, if enabled, Drive writes.
- A release-build network audit (mitmproxy on a test phone) is a release
  gate.
- Consent attestation recorded per interview; the app does not enforce
  jurisdiction rules.

## 8. Firm API contract (v1 draft, to be built by owner)
Base URL from firm config. Auth: Google ID token as Bearer.

| Method | Path | Body | Purpose |
|---|---|---|---|
| POST | /v1/interviews | JSON interview header | Create; returns interview id |
| POST | /v1/interviews/{id}/artifacts | multipart: `record` (JSON ledger entry + metadata), `payload` (bytes, optional for events) | One per ledger entry; idempotent by chainHash |
| GET | /v1/interviews/{id}/artifacts/{chainHash} | — | Ack check / resume |
| POST | /v1/interviews/{id}/finalize | JSON: full ledger, form, parties, transcript | Server verifies chain, returns ack or list of missing hashes |
| GET | /v1/config | — | Matter types, custom field schema, feature flags |

Record kinds: `interview.created`, `consent.attested`, `form.saved`,
`party.added|confirmed|dismissed`, `page.raw`, `page.corrected`, `page.ocr`,
`page.deleted`, `page.tagged`, `audio.chunk`, `transcript.segment`,
`transcript.final`, `interview.finalized`.

## 9. Open items awaiting owner input
- Firm matter-type list and custom field schema (or accept a placeholder).
- Whether Drive mirror is wanted in v1 or only reserved.
- Server hosting and where the API will run (affects allow-list).
