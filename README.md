# LegalEyes

iOS companion app for lawyer intake interviews using Meta smart glasses
(Meta Wearables Device Access Toolkit). See `docs/DISCOVERY.md` for scope.

## Setup (Mac)
1. `brew install xcodegen`
2. `cp LegalEyes/Config/Secrets.xcconfig.example LegalEyes/Config/Secrets.xcconfig` and fill in
   your Apple Team ID plus the Meta App ID and Client Token from the
   Wearables Developer Center project (bundle ID `legal.discover.legaleyes`).
3. `xcodegen generate` then open `LegalEyes.xcodeproj`.
4. Run on a physical iPhone with the Meta AI app in Developer Mode
   (Settings > App Info, tap version 5×, enable Developer Mode), or run in the
   Simulator and use the MockDeviceKit section in the app.

## Layout
- `project.yml` — XcodeGen spec (the `.xcodeproj` is generated, not committed)
- `LegalEyes/App` — entry point, URL callback
- `LegalEyes/Wearables` — DAT lifecycle (`GlassesController`), mock support
- `LegalEyes/Evidence` — hash-chained custody ledger
- `LegalEyes/Views` — SwiftUI
- `.github/workflows/ios.yml` — build + tests on GitHub's macOS runners

## AI-assisted docs
`.mcp.json` registers Meta's Wearables MCP server (`search_dat_docs`) for Claude Code.
