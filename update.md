# Dictator — update log

## Late July 2026 update — patch

- **Fixed the streak calendar** — future days were being skipped entirely
  instead of rendered as blank, which desynced the grid's row alignment and
  made it look scrambled. Now every cell (past or future) gets a slot, and
  it has real GitHub-style month labels and Mon/Wed/Fri row labels.
- **README** — added a logo header and tagline; no more bare text wall.

## Late July 2026 update

Plain-English summary of what changed. No code details.

### New

- **Full UI rework** — sidebar + 4 tabs (Home, Insights, Settings, About),
  replacing the old cramped multi-tab layout. New black-and-white look with
  a single accent color plus a separate green "highlight" color for streaks
  and positive states — both pick-your-own-color in Settings → Appearance.
- **New logo** — a smooth waveform mark, black and white, used everywhere
  (tray, window icon, sidebar).
- **Insights tab** — one page: WPM gauge, desktop-usage breakdown, a
  GitHub-style streak calendar, tone distribution, hourly activity.
- **About tab** — what the app is, who built it, links to GitHub/LinkedIn/Instagram.
- Language selection (12 languages + auto-detect), voice commands ("make it
  formal/casual" as a spoken override), silence auto-stop for hands-free
  mode, start/stop sound cues, redaction list for sensitive words before
  they're logged, named hotkey profiles.
- **macOS support** — best-effort, not yet run on real Mac hardware. See
  `info.md` for exactly what's platform-branched and what to check first if
  something misbehaves.
- One-line install for both platforms (see README) that also detects
  whether a local cleanup model is already pulled and offers to download one
  if not.

### Fixed

- A real crash that could blank the entire dashboard on a fresh install
  (one section's JS error was taking down every later section).
- Window not properly adapting to fullscreen/maximize.
- Vocabulary list not accepting Enter to add words, and not showing long
  lists (now a proper chip list with overflow).
- Periodic UI flicker from unconditional re-renders on the live poll.

### Removed

- An in-progress "Rewrite Studio" tab and the old 5-tab layout — replaced by
  the 4-tab structure above.
- Stale duplicate project copy (`Shipping/`) and a dead old history log
  (`Logs/`) that the app had stopped reading from.

## July 2026 update

Plain-English summary of what changed in the app. No code details.

### New features

- **Custom vocabulary** — you can now type a list of names and brand words
  (like "Uidea" or client names) into a box in the dashboard. The app uses
  this list so those words are spelled correctly instead of being guessed.

- **Spoken commands** — saying "new line", "new paragraph", or "bullet point"
  while dictating now creates real formatting in the text instead of typing
  those words out literally.

- **Tone awareness** — the app now looks at which program you're dictating
  into and adjusts the polish:
  - Slack, Discord, Telegram, WhatsApp → keeps it casual.
  - Outlook, Word, Gmail → polishes it professional.
  - VS Code, terminals, IDEs → types exactly what you said, no rewording.

- **Hands-free mode** — besides holding Ctrl+Win to talk, you can now
  double-tap Ctrl+Win to start recording hands-free. Tap Ctrl+Win once more
  to stop. Better for long dictations.

- **Instant mode** — short phrases (under 6 words) skip the AI cleanup step
  and land almost immediately. They just get a capital letter and punctuation.

- **History search** — new search box in the dashboard. Type anything and it
  finds every past dictation containing it, across your whole history.

- **Copy last dictation** — new button in the dashboard and new option in the
  tray menu. Copies your most recent dictation to the clipboard, useful when
  an app rejected the typed text.

- **Day streak** — new fifth stat card in the dashboard showing how many days
  in a row you've dictated.

### Changed

- **Status pill** — made smaller and moved up off the bottom edge of the
  screen so it no longer sticks to the very bottom.
- **Dashboard window** — slightly larger to fit the new vocabulary field,
  search box, and streak card.
- Dashboard subtitle now mentions the double-tap hands-free shortcut.

### Removed

- Nothing removed. All existing features work as before: push-to-talk,
  local transcription, AI cleanup, history logging, tray menu, and the
  everything-stays-on-this-PC guarantee are unchanged.

### Not included (from the suggestion list)

- Activity graph (suggestion was cut off; only the streak counter was built).
- Suggestions 9 and 10 (never received).
