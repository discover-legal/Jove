# LegalEyes — Meta Policy Review and Compliance Notes

Reviewed 2026-09-20 against the Meta Wearables Acceptable Use Policy (last
updated 2025-09-22) and Developer Terms (last updated 2025-10-06). Sections
13–18 of the Terms were only available in summary form; verify those clauses
directly before relying on them. This is an engineering read, not legal advice.

## Findings that need a decision from the owner

| # | Finding | Source | Why it matters | Recommended action |
|---|---|---|---|---|
| C1 | **Meta audit rights.** On a "Necessary Condition" Meta may audit with 10 business days' notice, demanding physical and remote access to IT systems, records, and personnel, surviving one year after deletion. | Terms s.4(c) | Could collide with solicitor-client privilege if the firm's systems hold intake material derived from glasses data. | Keep glasses-derived data (Technology Data) segregated and minimal. Document a position that privileged material is outside audit scope. Get a legal read before firm-wide rollout. |
| C2 | **Health information.** Processing HIPAA Protected Health Information via the Technology requires a signed Healthcare Addendum and Business Associate Agreement with Meta first. | Terms s.2(f), s.5(b)(vii) | Personal injury, malpractice, and disability intakes routinely include medical records. HIPAA is US law, but the clause is contractual and not limited by jurisdiction on its face. | Decide whether to exclude medical documents from glasses capture (use phone camera fallback for those) or pursue the addendum. Add a "medical document" flag that routes capture away from the glasses. |
| C3 | **Service provider agreements.** Sharing Technology Data with providers (hosting, Google Drive, any cloud AI) requires a written agreement limiting use, requiring deletion, and confidentiality. Meta may request the provider list, data volumes, and proof of agreements. | Terms s.5(c), s.5(d) | Google Workspace and Azure standard terms likely qualify, but must be on file. | Keep a `docs/service-providers.md` register with each provider, the agreement reference, and data types shared. |
| C4 | **Deletion obligations.** Must delete Technology Data when the user requests, when Meta requests, or when the account ends, unless law requires retention, in which case proof of the legal requirement must be retained and given to Meta on request. | Terms s.5(e) | Law society file retention rules are the retention basis. | Keep a citation of the applicable retention rule in the repo and in the privacy policy. Implement a deletion path for glasses-derived artifacts distinct from lawyer-authored records. |
| C5 | **Sensitive locations.** "Encouraging the use of device sensors or recording in sensitive locations" is not permitted; the term is undefined. | AUP | A client meeting room is arguably not sensitive, but a hospital, courthouse, or detention facility might be. | App guidance: intended for the firm's own offices. Do not market it for use in courts, hospitals, or custody settings. |
| C6 | **Organization required.** Developer accounts must belong to a registered Organization via a Managed Meta Account; the flow assumes a company. | Terms s.3, onboarding docs | A sole practitioner registers the firm as the Organization. | Register Discover Legal (or the practising entity) as the Organization. Owner's note: use own ID now and petition for more later. |
| C7 | **Meta monitoring and licence.** Meta "will conduct regular monitoring" of the Integration's processing of Technology Data, and the developer grants Meta a broad licence over the Integration and "data or materials provided in connection with" it. | Terms s.4(b)(i), s.2(c) | The licence wording could be read to reach captured content if it is ever "provided" to Meta. | Never upload client content to Meta for review, support, or bug reports. Use synthetic documents for any Integration Review submission. |
| C8 | **Testers need personal Meta Accounts.** Release channels are invite-only and each tester must have an existing Meta Account linked in the Meta AI app. | Release channel docs | Every lawyer using the app needs a personal Meta Account and the Meta AI app. | Document this in onboarding. Consider firm-issued Meta Accounts. |
| C9 | **Privacy policy and vulnerability reporting.** A privacy policy describing processing and deletion, an accessible security-vulnerability reporting channel, and breach reporting to Meta are required. | Terms s.6, s.7 | Required before Integration Review. | Draft a privacy policy and a `SECURITY.md` with a reporting address before Slice 7. |
| C10 | **90-day inactivity.** Meta may revoke API permissions not used within 90 days. | Terms s.11(c) | A stalled project loses camera permission. | Note in plan; keep a monthly smoke test. |

## Findings that are fine

- No prohibition on commercial or professional use, identity documents,
  biometrics, or recording of third parties in either document. General
  legal compliance is imported by reference (Terms s.2(b)(vi)).
- The surveillance prohibition is defined around law enforcement,
  national security, and intelligence purposes. Private legal intake is
  outside that wording.
- No requirement for the app to show a recording indicator. The glasses'
  own capture LED must not be interfered with (Terms s.2(b)(iv)); the app
  does not touch it.
- No restriction on enterprise or sideloaded distribution appears in the
  docs. Release channels only gate glasses-side registration.
- Liability cap is USD 1,000 and disputes go to arbitration; noted, no
  action.

## Unresolved technical question (test on hardware, Slice 1)

Neither document says whether photos captured via the DAT SDK are stored in
the Meta AI app or synced to Meta. This is the single most important fact
for the "nothing to Meta" requirement and must be established empirically.
See the Slice 1 hardware checklist in `PLAN.md`.

## Law-side items outside Meta's terms (owner to confirm)

- Law society client identification and verification rules for ID capture
  and retention.
- PIPEDA and provincial privacy law; Quebec Law 25 if applicable. Azure
  Canada region for data residency.
- Recording-consent practice and who was present in the room.
- Data processing terms with Google Workspace and the cloud host.
