**Pomodoro Timer settings**

These can also be changed live from the timer window (Tools > Pomodoro Timer).

- `work_minutes`: length of a focus session.
- `short_break_minutes`: length of a short break.
- `long_break_minutes`: length of a long break.
- `sessions_before_long_break`: number of focus sessions before a long break.
- `auto_start_next`: automatically start the next phase when one ends.
- `always_on_top`: keep the timer window above other windows.
- `sound`: beep when a phase ends.
- `auto_start_on_review`: start (or resume) the timer whenever you begin studying a deck.
- `auto_reveal`: automatically show the answer after `auto_reveal_seconds`.
- While a short/long break is running, or the Pomodoro is paused, `auto_reveal` and `auto_action` are on hold; they restart when a focus session is running again.
- `auto_action`: once the answer is showing, automatically act on the card after `auto_action_seconds`.
- `auto_action_type`: `"again"` (mark the card Again) or `"bury"` (bury it until tomorrow).
- `tip_of_tongue_seconds`: seconds added by the **Tip of my tongue** button (shown in the review bottom bar while the auto-reveal countdown runs).
- `studying_seconds`: seconds added by the **Studying** button (shown once the answer is revealed, while the auto-answer countdown runs).
- `lock_in_deck`: once you start studying, block going back to the Decks screen, the deck overview or switching decks (Esc, the Decks link, `/`) until the deck's due cards are finished. Closing Anki still works.
- `extend_time_key`: keyboard shortcut that presses the **Tip of my tongue** / **Studying** button while reviewing (default `"H"`; leave empty to disable).
- `language`: `"auto"` follows Anki's language, then the system language (falls back to English). Or set one of: `en`, `es`, `pt`, `fr`, `de`, `zh_CN`, `zh_TW`, `ja`, `ko`.
- `focus_color`, `short_break_color`, `long_break_color`: progress bar / timer colour for each phase (hex, e.g. `"#e74c3c"`).
- `show_bottom_bar`: show a progress bar along the bottom of Anki's main window (click it to open the timer).
