---
name: proof-i18n
description: >-
  Checks internationalisation and localisation before a release. Compares every
  translation file (JSON, .properties, gettext .po) with the base locale for
  missing keys, extra keys, empty values, strings left identical to the base, and
  placeholders that differ ({name}, {{count}}, %s, %(x)s), which crash or garble
  text at runtime. Writes a pseudo-locale for testing text expansion and finds
  likely hardcoded strings in .jsx, .tsx, .vue and .html. Offline, results in the
  ledger with a completeness score per locale. Use when the user says "check my
  translations", "are we ready to localise", "missing translations", "i18n audit",
  "pseudo-localisation", "find hardcoded strings", "placeholder mismatch", or
  "does the French version work".
argument-hint: "[locales folder] [--base en] [--src source folder]"
---

# proof-i18n

A translated product fails in quiet ways: a key nobody translated falls back to
English, a translator drops `{name}` and the screen shows a raw token or throws, a
German label runs off the end of a button, a string typed straight into a component
never reaches the translators at all. This skill checks the translation files and
the source for those problems and records what it measured. Use it before shipping
a new locale, after a big batch of new strings, or when a user reports a screen in
the wrong language.

What the user typed after `/proof-i18n`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/i18n.py" keys locales/ --base en
python3 "${CLAUDE_SKILL_DIR}/i18n.py" keys locales/ --base en --json
python3 "${CLAUDE_SKILL_DIR}/i18n.py" pseudo locales/en.json --out locales/pseudo.json --expand 0.35
python3 "${CLAUDE_SKILL_DIR}/i18n.py" hardcoded src/
python3 "${CLAUDE_SKILL_DIR}/i18n.py" record locales/ --base en --src src/ --min 95
python3 "${CLAUDE_SKILL_DIR}/i18n.py" status
```

`--dir` (before the command) points at the `proof/` folder; the default is `proof`.

## What it reads

One translation file per locale, found anywhere under the locales folder. The
locale comes from the file name (`en.json`, `fr.json`, `pt_BR.json`,
`messages_fr.properties`, `fr.po`) or, when the name is not a locale, from the
folder above it (`en/common.json`, `fr/LC_MESSAGES/messages.po`; the file name then
becomes a key namespace such as `common:`).

- **JSON:** nested objects are flattened to dotted keys (`cart.items`).
- **.properties:** `key=value`, `key: value`, comments, line continuations and
  `\uXXXX` escapes.
- **.po:** `msgid` is the key and `msgstr` the value; fuzzy entries count as empty,
  and in the base locale an empty `msgstr` falls back to the `msgid`.

## Step 1: compare the locales

```bash
python3 "${CLAUDE_SKILL_DIR}/i18n.py" keys locales/ --base en
```

For each locale it reports:

- **Missing** keys that exist in the base and not in the locale.
- **Extra** keys in the locale that the base no longer has (often dead strings).
- **Empty** values, which render as blank text.
- **Identical to base**: likely untranslated. Strings under 4 characters, numbers,
  URLs and single words that look like brand names (`GitHub`, `NASA`) are skipped.
- **Placeholder mismatches**: `{name}`, `{{count}}`, `${x}`, `%s`, `%d`, `%1$s`,
  `%(x)s` present in the base but missing, renamed or of another type in the
  translation. These break at runtime, so they matter most.

## Step 2: run the pseudo-locale

```bash
python3 "${CLAUDE_SKILL_DIR}/i18n.py" pseudo locales/en.json --out locales/pseudo.json
```

Every string becomes accented, bracketed and padded (35% longer by default), with
placeholders and HTML tags left untouched. Load it as a locale in the app and walk
the main screens. Text with no brackets is hardcoded. Text cut off, wrapped badly
or overflowing a button will break in longer languages. Brackets missing at one end
mean the string was truncated or concatenated from pieces.

## Step 3: find hardcoded strings

```bash
python3 "${CLAUDE_SKILL_DIR}/i18n.py" hardcoded src/
```

Lists `file:line` for text between tags with two or more words that is not a
template expression, and literal `placeholder`, `title`, `alt` and `aria-label`
values. `{t('key')}` and `{{ $t('key') }}` are not reported. Confirm each one; some
are deliberate (a product name, a code sample).

## Step 4: walk the checklist a tool cannot judge

- **Right-to-left layout** (Arabic, Hebrew, Persian, Urdu): the whole layout
  mirrors, including icons that imply direction, progress bars and carousels;
  numbers and Latin product names inside RTL text still read correctly.
- **Dates, numbers and currency** use the locale's format (day and month order,
  decimal and thousands marks, currency symbol position), produced by a formatting
  library, not by string concatenation.
- **Text expansion**: with the pseudo-locale on, no label is clipped, no button
  overflows, no line wraps into something it covers.
- **Plural rules**: counts use the locale's plural forms (some languages have one,
  some have six), not `count == 1 ? "item" : "items"`.
- **Fonts** cover every script you ship, with no empty boxes for missing glyphs,
  and line height suits scripts with tall marks.
- **Concatenation**: no sentence is built from separately translated pieces;
  word order changes between languages.
- **Locale switching** keeps the user's choice and falls back sensibly.

## Step 5: record

```bash
python3 "${CLAUDE_SKILL_DIR}/i18n.py" record locales/ --base en --src src/ --min 95
```

Writes one coverage check per locale (plus `hardcoded` when `--src` is given),
each marked covered with the report as evidence. Findings, all category `i18n` and
verified by the report file:

- Missing or empty keys in a locale: **medium** when more than 5% of the base keys,
  otherwise **low**.
- Placeholder mismatches in a locale: **high**, because they break at runtime.
- Likely hardcoded strings: **low**, one finding listing every candidate.

A score `i18n_completeness_<locale>` per locale: the percent of base keys with a
non-empty translation, against the `--min` budget (default 95) with `>=`. A re-run
clears the findings this tool wrote before, so a fixed locale stops being reported.

## What it writes

- `proof/coverage/proof-i18n.json`
- `proof/findings/proof-i18n/i18n-*.json`
- `proof/scores/proof-i18n.json`
- `proof/evidence/proof-i18n/i18n-report.txt` with every key and candidate listed

## Honest limits

It checks structure, not meaning: a key can be present, non-empty and wrong, and
only a fluent reader will see it. The identical-value check guesses; some words
really are the same in two languages. The hardcoded scan is a pattern match over
markup, so it misses strings built in plain JavaScript and can flag text that is
not user-facing. It reads JSON, .properties and .po only (not XLIFF, YAML, ARB or
.strings), and one locale per file name or folder. Placeholder checks know the
common syntaxes listed above, not every library's own. RTL, formatting, plurals
and fonts need the checklist and a person looking at the screens.

## Output / next

`proof/coverage/proof-i18n.json`, findings under `proof/findings/proof-i18n/`,
`proof/scores/proof-i18n.json` and the report under `proof/evidence/proof-i18n/`.
`/proof-report` reads them into the verdict. Pair the pseudo-locale walk with the
UI and accessibility skills, which look at the same screens for other problems.
