# LegalEyes — Product Requirements Document

Version 0.2 — 2026-09-20. Owner: h@discover.legal. Status: agreed decisions from
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
- Cloud AI extraction: reserved for v2 via a backend endpoint. v1 uses the
  on-device Apple Foundation Models LLM.
- Support for phones without Apple Intelligence hardware.
- Languages other than English.

## 3. Users and context
- Primary user: one lawyer (the owner), Oakley Meta glasses, iPhone 17 Pro,
  iOS 26+, Apple Intelligence capable (A17 Pro / A18 or later). Older
  phones are explicitly unsupported. Later: a few lawyers at one firm.
- Setting: in-person intake in the lawyer's office or a client site.
  Consent to record is the lawyer's responsibility and is not enforced by
  the app, but the app records that the lawyer attested consent.
- Connectivity: cellular or Wi-Fi may be intermittent; uploads must queue.

## 4. Decisions register
| # | Topic | Decision |
|---|---|---|
| D1 | Documentation | PRD and plan live as Markdown in the repo. |
| D2 | Capture trigger | Automatic detection with manual override (phone button, glasses captouch). |
| D3 | Intake form | Full form: identity, contact, address, matter type, parties, referral source, urgency, notes, firm-defined custom fields. Matter types and per-matter fields come from an editable JSON catalog (`Resources/MatterTypes.json`), overridable by the firm API. |
| D4 | Audio | Phone-mic recording plus on-device transcription (Apple Speech). |
| D5 | Backend | New REST API built by the owner; app targets the contract in §8. |
| D6 | Document processing | On-device crop, perspective correction, and OCR (Apple Vision). |
| D7 | Local protection | App-level encryption (CryptoKit envelope, key in Secure Enclave) plus Face ID on launch and after 5 minutes in background. |
| D8 | Google | Sign-in identity plus Drive upload. Drive mirror ships in v1 behind a settings toggle, default on, with enough to test end to end. |
| D9 | KYC | Photo of ID front and back via glasses or phone; no parsing in v1. |
| D10 | Conflicts | Out of scope. App emits typed, structured party records only. |
| D11 | Device-less | MockDeviceKit toggle in debug builds and a phone-camera fallback in production. |
| D12 | Sync | Continuous: each artifact uploads as captured; interview finalized at end. |
| D13 | Party capture | Structured typed fields, entered by lawyer, with suggestions from transcript and OCR that the lawyer confirms. |
| D14 | Transport | JSON records for all metadata; binaries as multipart parts referenced by SHA-256. |
| D15 | iOS target | iOS 26+, Apple Intelligence capable devices only. The app leverages the on-device neural engine and Foundation Models; older phones are out of scope. |
| D16 | Users v1 | Owner only. |
| D17 | Suggestion engine | On-device Apple Foundation Models (`FoundationModels` framework, `@Generable` structured output) for party and field extraction and interview summary, with NaturalLanguage tagging as a fast pre-pass. Backend Claude extraction endpoint reserved for v2. |
| D18 | Retention | Keep 30 days after server acknowledgement, then purge. Ledger manifest kept as receipt. |
| D19 | Biometrics | Face ID on launch and after 5 minutes in background. |
| D20 | Language | English only (en-US, en-CA, en-GB selectable). |
| D21 | Flow | Home screen offers both "Start with form" and "Start recording"; lawyer chooses per interview. |
| D22 | Hosting | Firm API may run locally (dev stub), on Azure (preferred), or AWS. App is host-agnostic: one base URL in firm config drives the network allow-list. |

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
  time, operator, a consent attestation the lawyer must set before recording
  begins, and a list of who is present (client, interpreter, family, other).
- FR-6a A page can be flagged "medical" before capture; medical pages are
  captured with the phone camera only and excluded from glasses-derived data
  (see `COMPLIANCE.md` C2).
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
  Matter (type from the matter-type catalog, description, urgency, plus the per-matter fields the catalog defines), Parties, Referral
  source, Notes, Custom fields (key/value schema fetched from the firm API,
  cached).
- FR-19 Party record: `{ id, role: client|adverse|related|witness|other,
  kind: person|organisation, givenNames, familyName, organisationName,
  aliases[], dob?, address?, source: typed|transcript|ocr, confirmed: bool }`.
- FR-20 Suggestions: the on-device Foundation Models LLM is prompted with
  new transcript segments and OCR text and returns a `@Generable` list of
  candidate parties (name, kind, likely role, evidence quote) and candidate
  form values (e.g. address, DOB, matter type). NaturalLanguage entity
  tagging runs first as a cheap filter. Candidates appear as chips; lawyer
  accepts (becomes a confirmed party or field value) or dismisses.
  Dismissals are recorded. Prompts and outputs never leave the device.
- FR-20a At interview end the same model produces a short structured
  summary (issues, requested outcome, deadlines mentioned) that the lawyer
  edits before finalize. Stored as a ledger entry.
- FR-20b If Apple Intelligence is unavailable (model not downloaded, device
  unsupported), suggestions degrade to NaturalLanguage tagging and the UI
  says so. This is a degraded state, not a supported configuration.
- FR-21 Form autosaves every change; each save is a ledger entry with the
  form's hash.

### 5.6 Chain of custody
- FR-22 Every artifact and event gets a ledger entry: SHA-256 of payload,
  hash chain over previous entry, timestamp, operator, device id (glasses
  serial or phone identifierForVendor), app version.
- FR-22a Each ledger entry is signed with a Secure Enclave key whose public
  half is registered with the server at interview creation, so the server
  verifies authorship as well as ordering.
- FR-22b Records are never edited. Corrections after finalize are appended
  as amendment entries referencing the original.
- FR-22c The server records its own receipt time per entry; device time is
  informational only.
- FR-23 Ledger is verifiable on device and by the server; server rejects any
  bundle whose chain or signatures fail.
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
- FR-31 Drive mirror: after finalize, a folder per interview
  (`LegalEyes/<yyyy-mm-dd> <client name> <interview id>`) is written to the
  lawyer's firm Drive containing corrected pages, audio, transcript, form
  JSON, summary, and ledger manifest. Settings toggle, default on. Upload
  uses the same resumable queue as the firm API. Drive file ids are recorded
  as ledger entries.

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
See `COMPLIANCE.md` for the Meta policy review and the decisions it needs.
- Meta receives only registration and attestation traffic from the DAT SDK.
- Apple on-device Speech, Vision, NaturalLanguage, and Foundation Models
  are used; `requiresOnDeviceRecognition = true` where the API offers it.
  Foundation Models runs entirely on the neural engine.
- Google receives the sign-in and, if enabled, Drive writes.
- A release-build network audit (mitmproxy on a test phone) is a release
  gate, and a Slice 1 hardware test establishes whether SDK captures reach
  the Meta AI app or Meta servers.
- The app's data directory is excluded from iCloud backup.
- No client content is ever sent to Meta for support, review, or bug
  reports; Integration Review uses synthetic documents only.
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

### 8.1 Hosting options for the firm API
The app does not care where the API runs. It stores one base URL plus the
expected TLS certificate pins in firm config, and the network allow-list is
derived from it. Three supported deployments:

| Option | Shape | Notes |
|---|---|---|
| Local (dev) | Stub server in this repo on the Mac or LAN | Used for slices 1–6 and CI. HTTP allowed only in debug builds. |
| Azure (preferred) | Container Apps or App Service for the API, Blob Storage for artifacts, Azure Database for PostgreSQL for records, Key Vault for secrets | Validate the Google ID token server-side (Google JWKS), or federate Google into Entra ID. Blob SAS URLs can later replace multipart if uploads grow. |
| AWS | ECS Fargate or Lambda + API Gateway, S3, RDS Postgres, Secrets Manager | Same contract; S3 pre-signed URLs are the equivalent of Blob SAS. |

Requirements on any host: TLS 1.2+, data residency in the firm's
jurisdiction, chain verification on finalize, idempotent artifact ingest by
chainHash, and audit logging of every read.

## 9. Open items awaiting owner input
- Review and edit the default matter-type catalog in
  `LegalEyes/Resources/MatterTypes.json`.
- Choose Azure vs AWS vs local for the first real deployment when Slice 6 starts.
- Confirm the Drive folder naming and whether audio goes to Drive or only to the firm API.
