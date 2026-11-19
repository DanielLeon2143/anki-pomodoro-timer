"""Pomodoro Timer add-on for Anki.

Adds Tools > Pomodoro Timer (Ctrl+Shift+P), a small floating window with
adjustable work / short break / long break durations.
"""

import json

from aqt import gui_hooks, mw
from aqt.qt import (
    QAction,
    QDialog,
    QFont,
    QFormLayout,
    QHBoxLayout,
    QKeySequence,
    QKeySequenceEdit,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QCheckBox,
    QColor,
    QColorDialog,
    QComboBox,
    QTimer,
    QVBoxLayout,
    QGroupBox,
    Qt,
    QApplication,
)
from aqt.utils import tooltip

from .i18n import LANGUAGE_NAMES, set_language, tr

ADDON = __name__

DEFAULTS = {
    "work_minutes": 25,
    "short_break_minutes": 5,
    "long_break_minutes": 15,
    "sessions_before_long_break": 4,
    "auto_start_next": False,
    "always_on_top": True,
    "sound": True,
    "show_bottom_bar": True,
    "auto_start_on_review": True,
    "auto_reveal": False,
    "auto_reveal_seconds": 15,
    "auto_action": False,
    "auto_action_seconds": 10,
    "auto_action_type": "again",
    "tip_of_tongue_seconds": 5,
    "studying_seconds": 10,
    "lock_in_deck": False,
    "extend_time_key": "H",
    "language": "auto",
    "focus_color": "#e74c3c",
    "short_break_color": "#27ae60",
    "long_break_color": "#2980b9",
}

WORK, SHORT, LONG = "work", "short", "long"
COLOR_KEYS = {WORK: "focus_color", SHORT: "short_break_color", LONG: "long_break_color"}
ACTIONS = ("again", "bury")


def phase_label(phase):
    return tr({WORK: "phase_work", SHORT: "phase_short", LONG: "phase_long"}[phase])


def action_label(action):
    return tr("action_bury" if action == "bury" else "action_again")

# Shortcuts Anki itself uses in the reviewer / main window (lower-cased
# QKeySequence strings), used to warn about clashes with the add-time hotkey.
ANKI_KEYS = {
    k.lower()
    for k in (
        "Space", "Return", "Enter", "1", "2", "3", "4", "5", "6", "7",
        "E", "I", "M", "O", "R", "U", "V", "A", "B", "D", "S", "T", "Y",
        "/", "*", "-", "=", "!", "@", "Shift+V", "Shift+A", "Shift+T",
        "Ctrl+Z", "Ctrl+Delete", "Ctrl+Shift+D", "Ctrl+Shift+P",
        "Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5", "Ctrl+6", "Ctrl+7",
        "Ctrl+Alt+E", "Ctrl+Alt+I", "Ctrl+Alt+N",
    )
}


def get_config():
    conf = dict(DEFAULTS)
    conf.update(mw.addonManager.getConfig(ADDON) or {})
    return conf


def save_config(conf):
    mw.addonManager.writeConfig(ADDON, conf)


class BottomBar(QProgressBar):
    """Thin progress bar pinned to the bottom of Anki's main window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextVisible(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(16)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr("bar_tooltip"))
        self.hide()

    def update_state(self, phase, remaining, total, running, visible, color):
        if not visible:
            self.hide()
            return
        m, s = divmod(max(remaining, 0), 60)
        if running:
            status = ""
        elif remaining == total:
            status = "  –  " + tr("bar_click")
        else:
            status = f"  ({tr('bar_paused')})"
        self.setMaximum(total or 1)
        self.setValue((total or 1) - remaining)
        self.setFormat(f"{phase_label(phase)}  {m:02d}:{s:02d}{status}")
        self.setStyleSheet(
            "QProgressBar { border: none; background: rgba(128,128,128,0.25);"
            " color: palette(text); font-size: 11px; }"
            f"QProgressBar::chunk {{ background-color: {color}; }}"
        )
        self.show()

    def mousePressEvent(self, event):
        show_pomodoro()


_bottom_bar = None


def get_bottom_bar():
    global _bottom_bar
    if _bottom_bar is None:
        _bottom_bar = BottomBar(mw)
        mw.mainLayout.addWidget(_bottom_bar)
    return _bottom_bar


class PomodoroDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("title"))
        self.conf = get_config()

        self.phase = WORK
        self.completed_work = 0
        self.running = False
        self.paused = False  # paused with the Pause button (not just stopped)
        self.remaining = self._phase_seconds(WORK)
        self._phase_total = self.remaining

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

        self._build_ui()
        self._settings_changed()
        self._apply_on_top()
        self._refresh()

    # ---------- UI ----------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.phase_label = QLabel()
        self.phase_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = QFont()
        f.setPointSize(14)
        f.setBold(True)
        self.phase_label.setFont(f)
        layout.addWidget(self.phase_label)

        self.time_label = QLabel()
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = QFont()
        f.setPointSize(40)
        f.setBold(True)
        self.time_label.setFont(f)
        layout.addWidget(self.time_label)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        layout.addWidget(self.progress)

        self.session_label = QLabel()
        self.session_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.session_label)

        buttons = QHBoxLayout()
        self.start_btn = QPushButton(tr("start"))
        self.start_btn.clicked.connect(self.toggle)
        reset_btn = QPushButton(tr("reset"))
        reset_btn.clicked.connect(self.reset)
        skip_btn = QPushButton(tr("skip"))
        skip_btn.clicked.connect(self.skip)
        for b in (self.start_btn, reset_btn, skip_btn):
            buttons.addWidget(b)
        layout.addLayout(buttons)

        # Settings
        box = QGroupBox(tr("settings"))
        form = QFormLayout(box)

        self.work_spin = self._spin(1, 180, self.conf["work_minutes"], tr("unit_min"))
        self.short_spin = self._spin(1, 60, self.conf["short_break_minutes"], tr("unit_min"))
        self.long_spin = self._spin(1, 120, self.conf["long_break_minutes"], tr("unit_min"))
        self.sessions_spin = self._spin(1, 12, self.conf["sessions_before_long_break"], "")

        form.addRow(tr("focus_len"), self.work_spin)
        form.addRow(tr("short_len"), self.short_spin)
        form.addRow(tr("long_len"), self.long_spin)
        form.addRow(tr("long_every"), self.sessions_spin)

        self.auto_cb = QCheckBox(tr("auto_next"))
        self.auto_cb.setChecked(self.conf["auto_start_next"])
        self.top_cb = QCheckBox(tr("on_top"))
        self.top_cb.setChecked(self.conf["always_on_top"])
        self.sound_cb = QCheckBox(tr("beep"))
        self.sound_cb.setChecked(self.conf["sound"])
        self.bar_cb = QCheckBox(tr("show_bar"))
        self.bar_cb.setChecked(self.conf["show_bottom_bar"])
        self.review_cb = QCheckBox(tr("auto_review"))
        self.review_cb.setChecked(self.conf["auto_start_on_review"])
        for cb in (self.review_cb, self.auto_cb, self.top_cb, self.sound_cb, self.bar_cb):
            form.addRow(cb)
            cb.toggled.connect(self._settings_changed)

        # Progress bar colours, one per phase.
        self.color_btns = {}
        for phase, key in COLOR_KEYS.items():
            btn = QPushButton()
            btn.setFixedWidth(80)
            btn.clicked.connect(lambda _=False, p=phase: self._pick_color(p))
            self.color_btns[phase] = btn
            form.addRow(tr("colour", phase=phase_label(phase)), btn)
        self._update_color_buttons()

        for s in (self.work_spin, self.short_spin, self.long_spin, self.sessions_spin):
            s.valueChanged.connect(self._settings_changed)

        # Language: automatic (Anki / system) or a fixed one.
        self.lang_combo = QComboBox()
        self.lang_combo.addItem(tr("lang_auto"), "auto")
        for code, name in LANGUAGE_NAMES.items():
            self.lang_combo.addItem(name, code)
        idx = self.lang_combo.findData(self.conf["language"])
        self.lang_combo.setCurrentIndex(max(idx, 0))
        self.lang_combo.setToolTip(tr("lang_restart"))
        self.lang_combo.currentIndexChanged.connect(self._language_changed)
        form.addRow(tr("language"), self.lang_combo)

        layout.addWidget(box)

        # Focus Options
        focus_box = QGroupBox(tr("focus_options"))
        fform = QFormLayout(focus_box)

        self.reveal_cb = QCheckBox(tr("reveal_after"))
        self.reveal_cb.setChecked(self.conf["auto_reveal"])
        self.reveal_spin = self._spin(1, 600, self.conf["auto_reveal_seconds"], tr("unit_sec"))
        fform.addRow(self.reveal_cb, self.reveal_spin)

        self.action_cb = QCheckBox(tr("answer_after"))
        self.action_cb.setChecked(self.conf["auto_action"])
        self.action_spin = self._spin(1, 600, self.conf["auto_action_seconds"], tr("unit_sec"))
        fform.addRow(self.action_cb, self.action_spin)

        self.action_combo = QComboBox()
        for key in ACTIONS:
            self.action_combo.addItem(action_label(key), key)
        idx = self.action_combo.findData(self.conf["auto_action_type"])
        self.action_combo.setCurrentIndex(max(idx, 0))
        fform.addRow(tr("mark_as"), self.action_combo)

        self.tip_spin = self._spin(1, 120, self.conf["tip_of_tongue_seconds"], tr("unit_sec"))
        self.tip_spin.setToolTip(tr("tip_tooltip"))
        fform.addRow(tr("tip_adds"), self.tip_spin)

        self.study_spin = self._spin(1, 300, self.conf["studying_seconds"], tr("unit_sec"))
        self.study_spin.setToolTip(tr("study_tooltip"))
        fform.addRow(tr("study_adds"), self.study_spin)

        for cb in (self.reveal_cb, self.action_cb):
            cb.toggled.connect(self._settings_changed)
        for s in (self.reveal_spin, self.action_spin, self.tip_spin, self.study_spin):
            s.valueChanged.connect(self._settings_changed)
        self.action_combo.currentIndexChanged.connect(self._settings_changed)

        self.lock_cb = QCheckBox(tr("lock"))
        self.lock_cb.setToolTip(tr("lock_tooltip"))
        self.lock_cb.setChecked(self.conf["lock_in_deck"])
        self.lock_cb.toggled.connect(self._settings_changed)
        fform.addRow(self.lock_cb)

        self.key_edit = QKeySequenceEdit(QKeySequence(self.conf["extend_time_key"] or ""))
        if hasattr(self.key_edit, "setMaximumSequenceLength"):
            self.key_edit.setMaximumSequenceLength(1)
        self.key_edit.setToolTip(tr("hotkey_tooltip"))
        self.key_edit.keySequenceChanged.connect(self._settings_changed)
        self.key_edit.keySequenceChanged.connect(self._update_key_note)
        fform.addRow(tr("hotkey"), self.key_edit)

        self.key_note = QLabel()
        self.key_note.setWordWrap(True)
        fform.addRow(self.key_note)
        self._update_key_note()

        layout.addWidget(focus_box)
        self.setMinimumWidth(280)

    def _update_key_note(self, *_):
        key = self.key_edit.keySequence().toString()
        if key and key.lower() in ANKI_KEYS:
            self.key_note.setText(tr("key_clash", key=key))
            self.key_note.setStyleSheet("color: #e74c3c; font-size: 11px;")
        else:
            self.key_note.setText(tr("key_note"))
            self.key_note.setStyleSheet("color: gray; font-size: 11px;")

    def _language_changed(self, *_):
        self.conf["language"] = self.lang_combo.currentData()
        save_config(self.conf)
        set_language(self.conf["language"])
        # Labels built on the fly (bottom bar, buttons, messages) switch now;
        # the rest of this window switches after a restart.
        self._refresh()
        tooltip(tr("lang_restart"), period=4000, parent=self)

    def _pick_color(self, phase):
        key = COLOR_KEYS[phase]
        color = QColorDialog.getColor(
            QColor(self.conf[key]), self, tr("colour_title", phase=phase_label(phase))
        )
        if color.isValid():
            self.conf[key] = color.name()
            save_config(self.conf)
            self._update_color_buttons()
            self._refresh()

    def _update_color_buttons(self):
        for phase, btn in self.color_btns.items():
            c = self.conf[COLOR_KEYS[phase]]
            btn.setText(c)
            btn.setStyleSheet(
                f"background-color: {c}; color: white; border-radius: 3px; padding: 3px;"
            )

    def _spin(self, lo, hi, val, suffix):
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(int(val))
        s.setSuffix(suffix)
        return s

    def _apply_on_top(self):
        on_top = self.conf["always_on_top"]
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on_top)
        if self.isVisible():
            self.show()

    # ---------- Logic ----------

    def _phase_seconds(self, phase):
        key = {
            WORK: "work_minutes",
            SHORT: "short_break_minutes",
            LONG: "long_break_minutes",
        }[phase]
        return int(self.conf[key]) * 60

    def _settings_changed(self, *_):
        old_on_top = self.conf["always_on_top"]
        self.conf.update(
            work_minutes=self.work_spin.value(),
            short_break_minutes=self.short_spin.value(),
            long_break_minutes=self.long_spin.value(),
            sessions_before_long_break=self.sessions_spin.value(),
            auto_start_next=self.auto_cb.isChecked(),
            always_on_top=self.top_cb.isChecked(),
            sound=self.sound_cb.isChecked(),
            show_bottom_bar=self.bar_cb.isChecked(),
            auto_start_on_review=self.review_cb.isChecked(),
            auto_reveal=self.reveal_cb.isChecked(),
            auto_reveal_seconds=self.reveal_spin.value(),
            auto_action=self.action_cb.isChecked(),
            auto_action_seconds=self.action_spin.value(),
            auto_action_type=self.action_combo.currentData(),
            tip_of_tongue_seconds=self.tip_spin.value(),
            studying_seconds=self.study_spin.value(),
            lock_in_deck=self.lock_cb.isChecked(),
            extend_time_key=self.key_edit.keySequence().toString(),
        )
        self.reveal_spin.setEnabled(self.conf["auto_reveal"])
        self.tip_spin.setEnabled(self.conf["auto_reveal"])
        self.action_spin.setEnabled(self.conf["auto_action"])
        self.action_combo.setEnabled(self.conf["auto_action"])
        self.study_spin.setEnabled(self.conf["auto_action"])
        save_config(self.conf)
        if old_on_top != self.conf["always_on_top"]:
            self._apply_on_top()
        # If the timer hasn't started for this phase yet, pick up the new length.
        if not self.running and self.remaining == self._phase_total:
            self.remaining = self._phase_seconds(self.phase)
            self._phase_total = self.remaining
        self._refresh()

    def toggle(self):
        if self.running:
            self.timer.stop()
            self.running = False
            self.paused = True
        else:
            self.timer.start()
            self.running = True
            self.paused = False
        self._refresh()

    def start_for_review(self):
        """Called when the user starts studying a deck."""
        if self.running or not self.conf["auto_start_on_review"]:
            return
        # A break that hasn't begun yet makes no sense while studying: go to focus.
        if self.phase != WORK and self.remaining == self._phase_total:
            self.phase = WORK
            self.remaining = self._phase_seconds(WORK)
            self._phase_total = self.remaining
        self.timer.start()
        self.running = True
        self.paused = False
        self._refresh()
        tooltip(tr("started", phase=phase_label(self.phase)), parent=mw)

    def reset(self):
        self.timer.stop()
        self.running = False
        self.paused = False
        self.remaining = self._phase_seconds(self.phase)
        self._phase_total = self.remaining
        self._refresh()

    def skip(self):
        self._advance(notify=False)

    def _tick(self):
        self.remaining -= 1
        if self.remaining <= 0:
            self._advance(notify=True)
        else:
            self._refresh()

    def _advance(self, notify):
        finished = self.phase
        self.paused = False
        if finished == WORK:
            self.completed_work += 1
            n = self.conf["sessions_before_long_break"]
            self.phase = LONG if self.completed_work % n == 0 else SHORT
        else:
            self.phase = WORK

        self.remaining = self._phase_seconds(self.phase)
        self._phase_total = self.remaining

        if notify:
            if self.conf["sound"]:
                QApplication.beep()
            msg = tr("focus_done") if finished == WORK else tr("break_over")
            tooltip(msg, period=5000, parent=mw)
            self.raise_()
            self.activateWindow()

        if self.conf["auto_start_next"] and (self.running or notify):
            self.timer.start()
            self.running = True
        else:
            self.timer.stop()
            self.running = False
        self._refresh()

    def _refresh(self):
        m, s = divmod(max(self.remaining, 0), 60)
        self.time_label.setText(f"{m:02d}:{s:02d}")
        color = self.conf[COLOR_KEYS[self.phase]]
        self.phase_label.setText(phase_label(self.phase))
        self.phase_label.setStyleSheet(f"color: {color};")
        self.time_label.setStyleSheet(f"color: {color};")

        total = self._phase_total or 1
        self.progress.setMaximum(total)
        self.progress.setValue(total - self.remaining)
        self.progress.setStyleSheet(
            f"QProgressBar::chunk {{ background-color: {color}; }}"
        )

        n = self.conf["sessions_before_long_break"]
        in_cycle = self.completed_work % n
        dots = "●" * in_cycle + "○" * (n - in_cycle)
        self.session_label.setText(
            f"{dots}   " + tr("completed", n=self.completed_work)
        )

        self.start_btn.setText(tr("pause") if self.running else tr("start"))
        title = f"{m:02d}:{s:02d} – {phase_label(self.phase)}" if self.running else tr("title")
        self.setWindowTitle(title)

        get_bottom_bar().update_state(
            self.phase,
            self.remaining,
            self._phase_total,
            self.running,
            self.conf["show_bottom_bar"],
            color,
        )

        # Stop / restart the card timers when a break starts or ends, or the
        # Pomodoro is paused / resumed.
        reason = self.hold_reason()
        old = getattr(self, "_last_hold", None)
        if reason != old:
            self._last_hold = reason
            _on_hold_changed(reason, old)

    def hold_reason(self):
        """Why auto-reveal / auto-answer are on hold, or None if they may run.

        "break"  - a short / long break is counting down.
        "paused" - the timer was paused with the Pause button.
        """
        if self.running:
            return "break" if self.phase != WORK else None
        return "paused" if self.paused else None

    def closeEvent(self, event):
        # Hide instead of destroying so the timer keeps running in the background.
        event.ignore()
        self.hide()


_dialog = None


def get_dialog():
    global _dialog
    if _dialog is None:
        _dialog = PomodoroDialog(mw)
    return _dialog


def show_pomodoro():
    _dialog = get_dialog()
    _dialog.show()
    _dialog.raise_()
    _dialog.activateWindow()


set_language(get_config().get("language", "auto"))

action = QAction(tr("title"), mw)
action.setShortcut(QKeySequence("Ctrl+Shift+P"))
action.triggered.connect(show_pomodoro)
mw.form.menuTools.addAction(action)

# Create the (hidden) timer at startup so the bottom bar is visible right away.
gui_hooks.profile_did_open.append(get_dialog)


def on_state_change(new_state, old_state):
    if new_state == "review" and old_state != "review":
        get_dialog().start_for_review()


gui_hooks.state_did_change.append(on_state_change)


# ---------- Focus Options: auto-reveal / auto-answer ----------

_card_timer = QTimer(mw)
_card_timer.setSingleShot(True)
_card_timer_target = None  # (card id, "question" | "answer")

# Bottom-bar button that buys more time on the current card.
EXTEND_INFO = {
    # side: (button label key, config key, countdown text key)
    "question": ("tip_btn", "tip_of_tongue_seconds", "reveal_in"),
    "answer": ("study_btn", "studying_seconds", "action_in"),
}

# Injected into the reviewer's bottom bar. The widget floats (position: fixed)
# just left of the "More" button, so it doesn't widen the table cells and push
# "Show Answer" / the ease buttons off-centre.
_EXTEND_JS = r"""
(function () {
  if (!window.pomoExtend) {
    var st = { timer: null, deadline: 0, prefix: "" };
    function anchor() {
      // The "More" button in the right-hand cell of the bottom bar.
      var tds = document.querySelectorAll("#innertable td");
      if (!tds.length) tds = document.querySelectorAll("td.stat");
      if (!tds.length) return null;
      var td = tds[tds.length - 1];
      return td.querySelector("button") || td;
    }
    function place() {
      var el = document.getElementById("pomo-extend");
      if (!el) return;
      var a = anchor();
      if (a) {
        var r = a.getBoundingClientRect();
        el.style.right = Math.max(0, window.innerWidth - r.left + 10) + "px";
        el.style.bottom = Math.max(0, window.innerHeight - r.bottom) + "px";
      } else {
        el.style.right = "10px";
        el.style.bottom = "4px";
      }
    }
    function cell() {
      var el = document.getElementById("pomo-extend");
      if (el) return el;
      if (!document.body) return null;
      el = document.createElement("div");
      el.id = "pomo-extend";
      el.style.cssText =
        "position:fixed;display:flex;flex-direction:column;" +
        "align-items:center;white-space:nowrap;z-index:10;";
      var left = document.createElement("span");
      left.id = "pomo-extend-left";
      left.className = "stattxt";
      var btn = document.createElement("button");
      btn.id = "pomo-extend-btn";
      btn.style.margin = "0";
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        pycmd("pomo:extend");
      });
      el.appendChild(left);
      el.appendChild(btn);
      document.body.appendChild(el);
      window.addEventListener("resize", place);
      return el;
    }
    function tick() {
      var left = document.getElementById("pomo-extend-left");
      var secs = Math.max(0, Math.ceil((st.deadline - Date.now()) / 1000));
      if (left) left.textContent = st.prefix + " " + secs + "s";
    }
    window.pomoExtend = {
      show: function (label, title, prefix, remainingMs) {
        var el = cell();
        if (!el) return;
        var btn = document.getElementById("pomo-extend-btn");
        btn.textContent = label;
        btn.title = title;
        st.prefix = prefix;
        st.deadline = Date.now() + remainingMs;
        el.style.display = "flex";
        place();
        if (st.timer) clearInterval(st.timer);
        st.timer = setInterval(tick, 250);
        tick();
      },
      hide: function () {
        if (st.timer) clearInterval(st.timer);
        st.timer = null;
        var el = document.getElementById("pomo-extend");
        if (el) el.style.display = "none";
      },
    };
  }
  %s
})();
"""


def _bottom_eval(call):
    bottom = getattr(mw.reviewer, "bottom", None)
    if bottom is not None and getattr(bottom, "web", None) is not None:
        bottom.web.eval(_EXTEND_JS % call)


def _show_extend_button(side):
    label_key, key, prefix_key = EXTEND_INFO[side]
    conf = get_dialog().conf
    extra = int(conf[key])
    label = tr(label_key)
    prefix = tr(prefix_key, action=action_label(conf["auto_action_type"]))
    args = ", ".join(
        json.dumps(v)
        for v in (
            f"{label} +{extra}s",
            tr("add_secs", n=extra)
            + (tr("shortcut", key=conf["extend_time_key"]) if conf.get("extend_time_key") else ""),
            prefix,
            max(_card_timer.remainingTime(), 0),
        )
    )
    _bottom_eval(f"window.pomoExtend.show({args});")


def _hide_extend_button():
    _bottom_eval("window.pomoExtend.hide();")


def _stop_card_timer():
    _card_timer.stop()
    _hide_extend_button()


def _schedule(card, side, seconds):
    global _card_timer_target
    _card_timer.stop()
    _card_timer_target = (card.id, side)
    _card_timer.start(int(seconds) * 1000)
    _show_extend_button(side)


def extend_card_timer():
    """Add extra time to the running reveal ("Tip of my tongue") or
    auto-answer ("Studying") countdown for the current card."""
    r = mw.reviewer
    if (
        mw.state != "review"
        or not r.card
        or _card_timer_target is None
        or not _card_timer.isActive()
    ):
        return
    card_id, side = _card_timer_target
    if r.card.id != card_id or r.state != side:
        return
    extra = int(get_dialog().conf[EXTEND_INFO[side][1]])
    _card_timer.start(max(_card_timer.remainingTime(), 0) + extra * 1000)
    _show_extend_button(side)
    tooltip(f"+{extra}s", period=800, parent=mw)


def _on_card_timer():
    _hide_extend_button()
    r = mw.reviewer
    if mw.state != "review" or not r.card or _card_timer_target is None:
        return
    card_id, side = _card_timer_target
    # Only act if the same card is still showing the same side.
    if r.card.id != card_id or r.state != side:
        return
    conf = get_dialog().conf
    if side == "question":
        r._showAnswer()
    elif conf["auto_action_type"] == "bury":
        if hasattr(r, "bury_current_card"):
            r.bury_current_card()
        else:
            r.onBuryCard()
        tooltip(tr("buried"), parent=mw)
    else:
        r._answerCard(1)
        tooltip(tr("marked_again"), parent=mw)


_card_timer.timeout.connect(_on_card_timer)


def on_show_question(card):
    dialog = get_dialog()
    conf = dialog.conf
    if conf["auto_reveal"] and dialog.hold_reason() is None:
        _schedule(card, "question", conf["auto_reveal_seconds"])
    else:
        _stop_card_timer()


def on_show_answer(card):
    dialog = get_dialog()
    conf = dialog.conf
    if conf["auto_action"] and dialog.hold_reason() is None:
        _schedule(card, "answer", conf["auto_action_seconds"])
    else:
        _stop_card_timer()


HOLD_MESSAGES = {"break": "hold_break", "paused": "hold_paused"}


def _on_hold_changed(reason, old_reason):
    """Stop the current card's timer when put on hold, restart it when released."""
    r = mw.reviewer
    in_review = mw.state == "review" and getattr(r, "card", None)
    if reason is not None:
        if old_reason is None:  # was running: stop it
            if in_review:
                _stop_card_timer()
            else:
                _card_timer.stop()
        if in_review:
            tooltip(tr(HOLD_MESSAGES[reason]), parent=mw)
        return
    if not in_review:
        return
    if r.state == "question":
        on_show_question(r.card)
    elif r.state == "answer":
        on_show_answer(r.card)


def on_js_message(handled, message, context):
    if message == "pomo:extend":
        extend_card_timer()
        return (True, None)
    return handled


def on_state_change_card_timer(new_state, old_state):
    if new_state != "review":
        _card_timer.stop()


gui_hooks.reviewer_did_show_question.append(on_show_question)
gui_hooks.reviewer_did_show_answer.append(on_show_answer)
gui_hooks.webview_did_receive_js_message.append(on_js_message)
gui_hooks.state_did_change.append(on_state_change_card_timer)


# ---------- Focus Options: lock into the deck ----------

LOCKED_STATES = ("deckBrowser", "overview")
_lock_bypass = False  # set while syncing / closing so Anki can do its own thing


def _lock_active():
    try:
        conf = get_dialog().conf
    except Exception:
        return False
    return (
        conf.get("lock_in_deck")
        and not _lock_bypass
        and mw.col is not None
        and mw.state == "review"
        # When the deck runs out, the reviewer clears its card before moving
        # to the overview, so finishing the deck always gets through.
        and getattr(mw.reviewer, "card", None) is not None
    )


_orig_move_to_state = mw.moveToState


_locked_deck_id = None  # deck selected when the review started


def _current_deck_id():
    try:
        return mw.col.decks.get_current_id()
    except AttributeError:
        return mw.col.decks.current()["id"]


def _guarded_move_to_state(state, *args, **kwargs):
    if state in LOCKED_STATES and _lock_active():
        # "/" (Study Deck) selects the new deck before moving to its overview;
        # put the original deck back so the next card still comes from it.
        try:
            if _locked_deck_id is not None and _current_deck_id() != _locked_deck_id:
                mw.col.decks.select(_locked_deck_id)
        except Exception:
            pass
        tooltip(tr("locked_block"), parent=mw)
        return None
    return _orig_move_to_state(state, *args, **kwargs)


mw.moveToState = _guarded_move_to_state


def _set_lock_bypass(*_):
    global _lock_bypass
    _lock_bypass = True


def on_state_change_lock(new_state, old_state):
    global _lock_bypass, _locked_deck_id
    if new_state == "review" and old_state != "review":
        _lock_bypass = False
        try:
            _locked_deck_id = _current_deck_id()
        except Exception:
            _locked_deck_id = None
        if get_dialog().conf.get("lock_in_deck"):
            tooltip(tr("locked_start"), parent=mw)


gui_hooks.state_did_change.append(on_state_change_lock)


# ---------- Hotkey for the add-time buttons ----------


def on_state_shortcuts(state, shortcuts):
    if state != "review":
        return
    key = (get_dialog().conf.get("extend_time_key") or "").strip()
    if key:
        shortcuts.append((key, extend_card_timer))


gui_hooks.state_shortcuts_will_change.append(on_state_shortcuts)
for _hook_name in ("sync_will_start", "profile_will_close"):
    _hook = getattr(gui_hooks, _hook_name, None)
    if _hook is not None:
        _hook.append(_set_lock_bypass)
