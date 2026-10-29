# Accessibility review: the manual checklist

`proof-a11y` does the contrast arithmetic for you and, when a rule engine is
installed, records what the engine reports. Everything on this page is different: it
needs a person at the keyboard, and for the last item a person listening to a screen
reader. Automated tools catch only a fraction of real barriers. A missing label is
easy for a tool to detect; a wrong label, a focus order that jumps around, an alt
text that says "image" or a form error nobody is told about is not. A clean engine
run is where the review starts, not where it ends.

Each item below says what good looks like, how to check it, what to save as
evidence, and the WCAG 2.2 success criterion it rests on. Record each one as you go:

```bash
python3 contrast.py cover <id> --note "what you did and what you saw" --evidence evidence/proof-a11y/<file>
python3 contrast.py na <id> --note "why it does not apply to this app"
python3 contrast.py finding add <id> --title "..." --severity <severity> --location "..." --evidence evidence/proof-a11y/<file>
```

The machine-readable list `contrast.py` reads:

```json
[
  {"id": "focus-order", "title": "Keyboard reaches everything, in reading order, with no trap", "wcag": "2.4.3"},
  {"id": "focus-visible", "title": "Focus is visible on every focusable element", "wcag": "2.4.7"},
  {"id": "names-labels", "title": "Every control has an accessible name that says what it does", "wcag": "4.1.2"},
  {"id": "alt-text", "title": "Images have text alternatives that carry their meaning", "wcag": "1.1.1"},
  {"id": "headings", "title": "Headings form a true outline with no skipped levels", "wcag": "1.3.1"},
  {"id": "landmarks", "title": "Landmark regions and a skip link structure the page", "wcag": "2.4.1"},
  {"id": "form-errors", "title": "Form errors are identified in text and easy to recover from", "wcag": "3.3.1"},
  {"id": "motion", "title": "Motion can be paused, nothing flashes, reduced motion is honoured", "wcag": "2.2.2"},
  {"id": "target-size", "title": "Tap and click targets are at least 24 by 24 CSS pixels, or spaced", "wcag": "2.5.8"},
  {"id": "zoom-200", "title": "Content survives 200% zoom and reflows at 320 CSS pixels wide", "wcag": "1.4.4"},
  {"id": "screen-reader", "title": "The main flows can be completed with a screen reader", "wcag": "4.1.2"}
]
```

## Before you start

- Pick the flows that matter: the ones `proof-web-e2e` planned, or at least sign
  in, the core task, and whatever submits money or data. Every item below is checked
  on those flows first, then on the rest of the site.
- Put the mouse out of reach for the keyboard items. Use a phone-width viewport for
  target size and reflow. Have the OS screen reader ready for the last item.
- Save evidence under `proof/evidence/proof-a11y/`: a screenshot for anything
  visual, a short text file of what you did and heard for the keyboard and
  screen-reader passes. A note alone is weaker than a file; say so when that is all
  you have.

## Severity guide

- critical: a core flow (sign in, pay, submit) cannot be completed at all without a
  mouse or without sight.
- high: a keyboard or screen-reader user cannot complete a main flow, or a required
  field, error or control is unreachable or unnamed.
- medium: the flow can be completed but with real difficulty: a confusing order, a
  weak focus ring, missing landmarks, a label that misleads.
- low: cosmetic, or an AAA-level criterion.

## The items

### focus-order

**Keyboard reaches everything, in reading order, with no trap** (WCAG 2.4.3 Focus
Order; also 2.1.1 Keyboard and 2.1.2 No Keyboard Trap)

What good looks like: every action a mouse can do, Tab and Enter or Space can do.
Focus moves in the order a sighted reader would read, not in DOM-accident order.
Nothing swallows focus: a dialog can be closed with Escape and focus returns to what
opened it; a date picker, a map, an embedded editor can all be left again.

How: with the mouse away, Tab and Shift+Tab through each page of the main flows,
activating controls as you go. Watch for focus landing on hidden or off-screen
elements, skipping a visible control, or jumping to the top after an action.

Evidence: a text file listing the pages tabbed through and any point where the
order broke, with a screenshot of the broken spot.

### focus-visible

**Focus is visible on every focusable element** (WCAG 2.4.7 Focus Visible; 2.4.11
Focus Not Obscured (Minimum); indicator contrast under 1.4.11 Non-text Contrast)

What good looks like: at every stop in the Tab order you can see where you are.
The indicator stands out from its surroundings (3:1 or better against the adjacent
colours), and a sticky header, cookie bar or chat widget never sits on top of it.

How: while doing the focus-order pass, pause at each control and ask "could I point
to the focused element from across the room?". Search the stylesheets for
`outline: none` or `outline: 0` and check each one has a replacement. For the
indicator's contrast, run `contrast.py ratio` on the ring colour against the
background and read the 3:1 line.

Evidence: screenshots of a focused button, link and form field; a screenshot of any
control with no visible focus.

### names-labels

**Every control has an accessible name that says what it does** (WCAG 4.1.2 Name,
Role, Value; 3.3.2 Labels or Instructions; 2.4.6 Headings and Labels; 2.5.3 Label
in Name)

What good looks like: each form field has a label a screen reader announces (a
`label` element tied to it, or an aria attribute pointing at visible text). An
icon-only button has a name ("Close", "Search", not "button"). Links say where they
go; a page with twelve "Read more" links fails in practice even if each has a name.
Placeholder text is not a label: it disappears when typing starts. The visible text
of a control is part of its accessible name, so voice-control users can say it.

How: open the browser's accessibility inspector on each form and toolbar and read
the name of every control. A tool can tell you a name exists; only you can tell
whether it is the right name.

Evidence: a screenshot of the inspector showing a control with a missing or wrong
name, or a short list of the controls checked.

### alt-text

**Images have text alternatives that carry their meaning** (WCAG 1.1.1 Non-text
Content; 1.4.5 Images of Text)

What good looks like: every meaningful image has an alt text that does the image's
job in context ("Revenue by quarter: doubled from Q1 to Q4", not "chart" or
"chart.png"). A decorative image has an empty alt (`alt=""`) so it is skipped, not
a missing one so its file name is read out. Text is real text, not a picture of
text, unless it is a logo. A complex chart or diagram has a longer description
nearby.

How: list the images on the main pages with the inspector or by reading the
markup. For each one, cover the image and read only its alt: would you know what
you missed? An engine flags a missing alt; it cannot flag a useless one.

Evidence: the list with a verdict per image; screenshots of the wrong ones.

### headings

**Headings form a true outline with no skipped levels** (WCAG 1.3.1 Info and
Relationships; 2.4.6 Headings and Labels; 2.4.10 Section Headings is AAA)

What good looks like: one `h1` that says what the page is, then levels that step
down one at a time. Headings are heading elements, not large bold `div`s, and
large bold text that is not a heading is not marked up as one. Read alone, the
outline tells the page's story, because screen-reader users jump between headings
to find their way.

How: use a headings outline view (most browser accessibility inspectors have one)
on each main page. Look for skipped levels, several `h1`s, styled-but-not-marked
headings, and headings used only to make text big.

Evidence: a screenshot or text dump of the outline per page.

### landmarks

**Landmark regions and a skip link structure the page** (WCAG 2.4.1 Bypass Blocks;
1.3.1 Info and Relationships)

What good looks like: exactly one `main`; `header`, `nav` and `footer` where those
things are; several `nav`s each labelled (main, breadcrumb, footer). The first Tab
press reveals a "Skip to content" link that works. Repeated blocks (the menu, the
sidebar) can be jumped past.

How: press Tab once on each page and look for the skip link; follow it. List the
landmarks with the inspector or a screen reader's landmark menu.

Evidence: a screenshot of the skip link visible, and the landmark list.

### form-errors

**Form errors are identified in text and easy to recover from** (WCAG 3.3.1 Error
Identification; 3.3.3 Error Suggestion; 3.3.4 Error Prevention (Legal, Financial,
Data); 4.1.3 Status Messages)

What good looks like: submitting a form empty, or with a wrong value, produces an
error in words, not only a red border. The message says which field and what to do.
Focus moves to the first error or the error is announced (a live region or alert
role). The message is tied to its field so a screen reader reads it with the field.
Required fields are marked in text, not only by colour or an asterisk with no
explanation. Before a payment or a legal commitment the user can review and correct.

How: on every form in the main flows, submit it empty, then with one wrong field,
with the keyboard and with a screen reader running.

Evidence: screenshots of the error state; a note of what the screen reader said.

### motion

**Motion can be paused, nothing flashes, reduced motion is honoured** (WCAG 2.2.2
Pause, Stop, Hide; 2.3.1 Three Flashes or Below Threshold; 1.4.2 Audio Control;
2.3.3 Animation from Interactions is AAA)

What good looks like: anything that moves, scrolls or updates by itself for more
than five seconds (carousels, tickers, background video) has a pause or stop
control. Nothing flashes more than three times a second. Audio that starts on its
own and lasts more than three seconds can be stopped. With the operating system's
reduce-motion setting on, parallax, large transitions and auto-play calm down.

How: turn reduce-motion on in the OS, reload, and watch the home page and any
animated step of the flows. Then turn it off and look for anything auto-moving
without a pause control.

Evidence: a short screen recording or before/after screenshots, with a note of
the setting used.

### target-size

**Tap and click targets are at least 24 by 24 CSS pixels, or spaced** (WCAG 2.5.8
Target Size (Minimum); 2.5.5 Target Size (Enhanced) at 44 by 44 is AAA)

What good looks like: the smallest targets (icon buttons, the close cross on a
dialog, pagination numbers, row actions in tables, checkboxes) are 24 by 24 CSS
pixels or more, or small targets are spaced so a 24-pixel circle on each does not
overlap another. Links inside a sentence are exempt.

How: at a phone-width viewport, measure the smallest targets with the inspector's
box model. Try tapping them on a real phone if one is at hand.

Evidence: a screenshot with the measured box of the smallest target on each main
page.

### zoom-200

**Content survives 200% zoom and reflows at 320 CSS pixels wide** (WCAG 1.4.4
Resize Text; 1.4.10 Reflow; 1.4.12 Text Spacing)

What good looks like: at 200% browser zoom nothing is lost, clipped or overlapping
and every control still works. At 400% zoom on a 1280-pixel window (the same as a
320 CSS pixel wide screen) content flows into one column with no sideways
scrolling, except for things that need two dimensions such as data tables and maps.
With text spacing widened (line height 1.5, paragraph spacing 2 times the font size,
letter spacing 0.12 em, word spacing 0.16 em) nothing is cut off.

How: zoom to 200%, then 400%, and walk the main flows at each. Apply the text
spacing with a user stylesheet or the inspector and look at the dense pages
(tables, cards, navigation).

Evidence: screenshots at 200% and 400% of each main page, especially any that
break.

### screen-reader

**The main flows can be completed with a screen reader** (WCAG 4.1.2 Name, Role,
Value; 1.3.1 Info and Relationships; 4.1.3 Status Messages; and whether the whole
thing makes sense when heard)

What good looks like: with the screen off, or your eyes closed, you can sign in, do
the core task and submit. Controls announce a role, a name and a state ("checkbox,
Remember me, not checked"). Changes the page makes by itself (a loading spinner, a
cart count, a toast, an error) are announced. A dialog announces itself, keeps
focus inside, and gives it back on close. Tables read with their headers. Nothing
reads out raw icon-font glyphs or says "clickable, clickable, clickable".

How: use the screen reader that comes with the platform (Narrator or NVDA on
Windows, VoiceOver on macOS and iOS, TalkBack on Android). Learn its five basic
moves (next item, next heading, next landmark, activate, read from here) and do the
main flows end to end. This single item reveals more than everything else on the
page combined, and no tool replaces it.

Evidence: a text file of the flow as heard, step by step, noting each point where
you were lost; a screenshot wherever a finding is recorded.

## When you are done

Every item is either covered (with a note and, where possible, a file), not
applicable (with a reason), or has findings against it. Then run `/proof-report`.
A page that passes all of this is not "accessible" in the abstract; it is a page on
which these checks found no barrier, on this build, for the flows you walked.
