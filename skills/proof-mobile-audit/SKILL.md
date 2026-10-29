---
name: proof-mobile-audit
description: >-
  Inspects a mobile app build for configuration and data-handling problems, offline.
  For Android it reads an APK for the permissions it requests, cleartext-traffic
  settings and plain-HTTP URLs baked in; for iOS it reads Info.plist for App
  Transport Security, URL schemes and permission-use descriptions. Findings go in
  the ledger. Use when the user says "audit the apk", "check the app build",
  "mobile security", "what permissions does my app ask for", "is the app leaking
  http", or "ios app transport security". Defers to a dedicated mobile security tool
  for a deep inspection.
argument-hint: "[path to an .apk or .ipa]"
---

# proof-mobile-audit

A quick, repeatable look at what a built app asks for and how it talks to the
network, without unpacking it by hand. It reads what the package exposes and records
the things worth a second look.

What the user typed after `/proof-mobile-audit`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/apkscan.py" apk app-release.apk
python3 "${CLAUDE_SKILL_DIR}/apkscan.py" ios MyApp.ipa
python3 "${CLAUDE_SKILL_DIR}/apkscan.py" record apk app-release.apk
python3 "${CLAUDE_SKILL_DIR}/apkscan.py" status
```

## Android

```bash
python3 "${CLAUDE_SKILL_DIR}/apkscan.py" apk app-release.apk
```

Reports the permissions the app requests (flagging the sensitive ones), whether the
manifest mentions cleartext traffic or a debuggable flag, and the plain-HTTP and
Firebase URLs it can find in the resources.

## iOS

```bash
python3 "${CLAUDE_SKILL_DIR}/apkscan.py" ios MyApp.ipa
```

Reads `Info.plist` and reports whether App Transport Security allows arbitrary
(plain-HTTP) loads, the custom URL schemes, and the permission-use descriptions.

## Record and act

```bash
python3 "${CLAUDE_SKILL_DIR}/apkscan.py" record apk app-release.apk
```

Each notable item becomes a finding: trim permissions the app does not need, move
off plain HTTP, and turn off debuggable and arbitrary loads in release builds.

## Honest limits

Android compiles its manifest to a binary format, so this reads the strings it can
see reliably (permissions, URLs) and cannot fully parse component export flags or
every attribute; it is honest about that and points you at a dedicated mobile
security tool for depth. It inspects the package as built, not the app running on a
device. A clean pass means nothing obvious stood out in the parts it can read.

## Output / next

`proof/coverage/proof-mobile-audit.json`, findings under
`proof/findings/proof-mobile-audit/`, a report under
`proof/evidence/proof-mobile-audit/`. Then `/proof-report`.
