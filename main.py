import os
import sys
import time
import shutil
import threading
import traceback
import subprocess
import warnings
from collections import deque

if not hasattr(time, "perf_counter_ns"):
    time.perf_counter_ns = lambda: int(time.perf_counter() * 1e9)

import cv2
import numpy as np
import mss
import soundcard as sc
from scipy.io import wavfile
from pynput import keyboard, mouse
from PySide6.QtCore import Qt, QPoint, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QIcon, QPainter, QPalette, QPen, QPixmap, QPolygon
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QListWidget, QMenu, QPushButton, QSpinBox,
    QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget
)

warnings.simplefilter("ignore")


def get_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def unique_timestamp():
    return time.strftime("%Y%m%d_%H%M%S") + "_{:03d}".format(int(time.time() * 1000) % 1000)


def safe_replace(src, dst, retries=10, delay=0.2):
    if not os.path.exists(src):
        return False
    for _ in range(retries):
        try:
            if os.path.exists(dst):
                os.remove(dst)
            os.replace(src, dst)
            return True
        except PermissionError:
            time.sleep(delay)
        except FileNotFoundError:
            return False
    return False


def safe_remove(path, retries=10, delay=0.2):
    if not os.path.exists(path):
        return False
    for _ in range(retries):
        try:
            os.remove(path)
            return True
        except PermissionError:
            time.sleep(delay)
        except FileNotFoundError:
            return False
    return False


def merge_video_audio(temp_video, temp_audio, final_output):
    ffmpeg_exe = get_ffmpeg()
    if not ffmpeg_exe:
        print("FFmpeg not found. Install it (winget install ffmpeg) or "
              "run: pip install imageio-ffmpeg. Saving video without audio.")
        return False

    cmd = [
        ffmpeg_exe, "-y",
        "-i", temp_video,
        "-i", temp_audio,
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        final_output,
    ]
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=True, creationflags=flags)
        return True
    except Exception as e:
        print("FFmpeg merge failed:", e)
        return False


class AudioRecorder:
    CHUNK = 4096

    def __init__(self, duration_sec=15, sample_rate=44100):
        self.sample_rate = sample_rate
        self.duration_sec = duration_sec
        self.buffer = deque(maxlen=self._maxlen(duration_sec))
        self.is_recording = False
        self._thread = None

    def _maxlen(self, duration_sec):
        if duration_sec is None:
            return None
        return int(self.sample_rate * duration_sec / self.CHUNK) + 1

    def start(self):
        if self.is_recording:
            return
        self.buffer.clear()
        self.is_recording = True
        self._thread = threading.Thread(target=self._record_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.is_recording = False

    def update_duration(self, duration_sec):
        self.duration_sec = duration_sec
        self.buffer = deque(list(self.buffer), maxlen=self._maxlen(duration_sec))

    def _record_loop(self):
        try:
            speaker = sc.default_speaker()
            loopback = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            with loopback.recorder(samplerate=self.sample_rate, channels=2) as mic:
                while self.is_recording:
                    self.buffer.append(mic.record(numframes=self.CHUNK))
        except Exception as e:
            print("Audio capture error:", e)

    def _snapshot(self):
        for _ in range(5):
            try:
                return list(self.buffer)
            except RuntimeError:
                time.sleep(0.01)
        return []

    def save_wav(self, output_filepath):
        chunks = self._snapshot()
        if not chunks:
            return False
        try:
            audio_data = np.concatenate(chunks, axis=0)
            audio_int16 = (np.clip(audio_data, -1.0, 1.0) * 32767).astype(np.int16)
            wavfile.write(output_filepath, self.sample_rate, audio_int16)
            return True
        except Exception as e:
            print("Audio save error:", e)
            return False


BUTTON_KINDS = {
    "green": ("#2e7d32", "#388e3c"),
    "red": ("#c62828", "#d32f2f"),
    "blue": ("#1565c0", "#1976d2"),
    "orange": ("#ef6c00", "#fb8c00"),
    "amber": ("#ffb300", "#ffc107"),
    "slate": ("#37474f", "#455a64"),
}


def build_stylesheet():
    rules = [
        "QFrame#sidebar { background-color: #11111b; }",
        "QLabel { background: transparent; }",
        "QLabel#title { font-size: 24px; font-weight: bold; }",
        'QLabel[muted="true"] { color: #8b8fa8; }',
        "QPushButton { background-color: #313244; border: none; border-radius: 6px; padding: 7px 12px; }",
        "QPushButton:hover { background-color: #3d3f57; }",
        "QPushButton:disabled { background-color: #232436; color: #6c7086; }",
        "QPushButton#nav { background: transparent; font-size: 20px; padding: 10px 0px; border-radius: 10px; }",
        "QPushButton#nav:hover { background-color: #2a2a3d; }",
    ]
    for name, (normal, hover) in BUTTON_KINDS.items():
        text = "#1e1e2e" if name == "amber" else "white"
        rules.append('QPushButton[kind="%s"] { background-color: %s; color: %s; font-weight: bold; }'
                     % (name, normal, text))
        rules.append('QPushButton[kind="%s"]:hover { background-color: %s; }' % (name, hover))
        rules.append('QPushButton[kind="%s"]:disabled { background-color: #232436; color: #6c7086; }' % name)
    return "\n".join(rules)


def apply_dark_palette(app):
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor("#181825"))
    pal.setColor(QPalette.ColorRole.WindowText, QColor("#cdd6f4"))
    pal.setColor(QPalette.ColorRole.Base, QColor("#1e1e2e"))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#181825"))
    pal.setColor(QPalette.ColorRole.Text, QColor("#cdd6f4"))
    pal.setColor(QPalette.ColorRole.Button, QColor("#313244"))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor("#cdd6f4"))
    pal.setColor(QPalette.ColorRole.Highlight, QColor("#1565c0"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#313244"))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#cdd6f4"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#6c7086"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#6c7086"))
    app.setPalette(pal)


def set_kind(widget, kind):
    widget.setProperty("kind", kind)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def make_icon(recording=False):
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#1e1e2e"))
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setBrush(QColor("white"))
    p.drawPolygon(QPolygon([QPoint(24, 18), QPoint(24, 46), QPoint(48, 32)]))
    if recording:
        p.setPen(QPen(QColor("#1e1e2e"), 3))
        p.setBrush(QColor("#ef5350"))
        p.drawEllipse(38, 4, 22, 22)
    p.end()
    return QIcon(pix)


class ClippyWindow(QWidget):
    invoke = Signal(object)

    def __init__(self):
        super().__init__()
        self.invoke.connect(self._run_call)

        self.setWindowTitle("Clippy")
        self.setWindowIcon(make_icon())
        self.setMinimumSize(520, 680)
        self.resize(540, 720)

        self.fps = 30
        self.clip_duration = 15
        self.frame_buffer = deque(maxlen=self.fps * self.clip_duration)
        self.is_running = False
        self.capture_thread = None

        self.is_screen_recording = False
        self.screen_rec_start_time = 0

        self.keys = {"record": "F1", "clip": "F2", "macro_play": "F3", "macro_rec": "F4"}
        self.rebinding_target = None
        self._held = {}

        self.save_directory = os.path.join(os.path.expanduser("~"), "Videos")
        if not os.path.exists(self.save_directory):
            self.save_directory = os.getcwd()

        self.audio_recorder = AudioRecorder(duration_sec=self.clip_duration)
        self.screen_audio = AudioRecorder(duration_sec=None)

        self.macro_steps = []
        self._macro_stop = threading.Event()
        self._macro_running = False
        self._macro_countdown = False
        self._countdown_left = 0
        self._mouse = mouse.Controller()

        self._macro_recording = False
        self._rec_listener = None
        self._rec_events = []
        self._rec_latest = None
        self._rec_t0 = 0.0
        self._rec_secs = 5.0
        self._rec_rect = None
        self._rec_last_sample = 0.0

        self._quitting = False
        self._tray_hint_shown = False
        self._toast_folder = None

        self.screen_timer = QTimer(self)
        self.screen_timer.setInterval(1000)
        self.screen_timer.timeout.connect(self._update_screen_rec_timer)

        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(1000)
        self.countdown_timer.timeout.connect(self._countdown_step)

        self.rec_timer = QTimer(self)
        self.rec_timer.setInterval(100)
        self.rec_timer.timeout.connect(self._rec_tick)

        self._build_ui()
        self._build_tray()
        self.refresh_labels()

        self.hotkey_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self.hotkey_listener.daemon = True
        self.hotkey_listener.start()

    def _run_call(self, fn):
        try:
            fn()
        except Exception:
            traceback.print_exc()

    def ui(self, fn):
        self.invoke.emit(fn)

    def _build_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(64)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(6, 12, 6, 12)
        side.setSpacing(6)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_main_page())
        self.stack.addWidget(self._build_screen_page())
        self.stack.addWidget(self._build_macro_page())
        self.stack.addWidget(self._build_settings_page())

        def nav(text, index, color=None):
            btn = QPushButton(text)
            btn.setObjectName("nav")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if color:
                btn.setStyleSheet("color: %s;" % color)
            btn.clicked.connect(lambda _=False, i=index: self.stack.setCurrentIndex(i))
            return btn

        side.addWidget(nav("▶", 0))
        side.addWidget(nav("●", 1, "#ef5350"))
        side.addWidget(nav("⌖", 2, "#89b4fa"))
        side.addStretch()
        side.addWidget(nav("⚙", 3))

        outer.addWidget(sidebar)
        outer.addWidget(self.stack, 1)

    def _page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(26, 22, 26, 18)
        lay.setSpacing(10)
        return page, lay

    def _title(self, text):
        label = QLabel(text)
        label.setObjectName("title")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    def _muted(self, text, italic=False, small=False):
        label = QLabel(text)
        label.setProperty("muted", True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        font = label.font()
        font.setItalic(italic)
        if small:
            font.setPointSize(8)
        label.setFont(font)
        return label

    def _big_button(self, kind, slot, width=280, height=42):
        btn = QPushButton()
        btn.setFixedSize(width, height)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setProperty("kind", kind)
        btn.clicked.connect(slot)
        return btn

    def _status_label(self, text):
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("font-weight: bold; color: #8b8fa8;")
        return label

    def _color_label(self, label, color):
        label.setStyleSheet("font-weight: bold; color: %s;" % color)

    def _build_main_page(self):
        page, lay = self._page()
        lay.addWidget(self._title("CLIPPY"))
        lay.addSpacing(16)

        self.toggle_btn = self._big_button("green", self.toggle_buffer)
        self.clip_btn = self._big_button("blue", self.save_clip, height=50)
        self.clip_btn.setEnabled(False)
        self.status_label = self._status_label("Status: Idle")

        lay.addWidget(self.toggle_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.clip_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.status_label)
        lay.addStretch()
        lay.addWidget(self._muted("Keep this running in the background while playing.", italic=True))
        return page

    def _build_screen_page(self):
        page, lay = self._page()
        lay.addWidget(self._title("SCREEN RECORDER"))
        lay.addSpacing(16)

        self.screen_rec_btn = self._big_button("red", self.toggle_screen_recording)
        self.screen_rec_btn.setText("Start Screen Recording")
        self.screen_rec_status = self._status_label("Status: Idle")
        self.screen_rec_timer_label = QLabel("00:00:00")
        self.screen_rec_timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.screen_rec_timer_label.setStyleSheet("font-size: 22px; font-weight: bold;")

        lay.addWidget(self.screen_rec_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.screen_rec_status)
        lay.addWidget(self.screen_rec_timer_label)
        lay.addStretch()
        return page

    def _build_macro_page(self):
        page, lay = self._page()
        lay.setSpacing(8)
        lay.addWidget(self._title("MACRO"))
        lay.addWidget(self._muted("Click controls to add steps, then press play.", italic=True))

        grid = QGridLayout()
        grid.setSpacing(6)

        def move_button(text, row, col, ux, uy):
            btn = QPushButton(text)
            btn.setProperty("kind", "slate")
            btn.setFixedWidth(136)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, a=ux, b=uy: self._add_move(a, b))
            grid.addWidget(btn, row, col)

        move_button("▲  Mouse Up", 0, 1, 0, -1)
        move_button("◀  Mouse Left", 1, 0, -1, 0)
        move_button("Mouse Right  ▶", 1, 2, 1, 0)
        move_button("▼  Mouse Down", 2, 1, 0, 1)
        center = QLabel("✥")
        center.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.setStyleSheet("font-size: 18px; color: #8b8fa8;")
        grid.addWidget(center, 1, 1)

        pad_row = QHBoxLayout()
        pad_row.addStretch()
        pad_row.addLayout(grid)
        pad_row.addStretch()
        lay.addLayout(pad_row)

        click_row = QHBoxLayout()
        click_row.addStretch()
        for text, step in (
            ("Left Click", {"type": "click", "button": "left"}),
            ("Right Click", {"type": "click", "button": "right"}),
            ("Hold Left", {"type": "down", "button": "left"}),
            ("Release Left", {"type": "up", "button": "left"}),
        ):
            btn = QPushButton(text)
            btn.setProperty("kind", "slate")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, s=step: self._add_step(dict(s)))
            click_row.addWidget(btn)
        click_row.addStretch()
        lay.addLayout(click_row)

        rec_row = QHBoxLayout()
        rec_row.addStretch()
        self.macro_rec_btn = QPushButton()
        self.macro_rec_btn.setFixedWidth(230)
        self.macro_rec_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.macro_rec_btn.setProperty("kind", "red")
        self.macro_rec_btn.clicked.connect(lambda: self.toggle_macro_recording())
        rec_row.addWidget(self.macro_rec_btn)
        rec_row.addSpacing(8)
        rec_row.addWidget(QLabel("Length (s)"))
        self.rec_secs_spin = QSpinBox()
        self.rec_secs_spin.setRange(1, 60)
        self.rec_secs_spin.setValue(5)
        rec_row.addWidget(self.rec_secs_spin)
        rec_row.addStretch()
        lay.addLayout(rec_row)

        opt_row = QHBoxLayout()
        opt_row.addStretch()
        opt_row.addWidget(QLabel("Distance (px)"))
        self.dist_spin = QSpinBox()
        self.dist_spin.setRange(1, 2000)
        self.dist_spin.setSingleStep(10)
        self.dist_spin.setValue(50)
        opt_row.addWidget(self.dist_spin)
        opt_row.addSpacing(10)
        opt_row.addWidget(QLabel("Wait (s)"))
        self.wait_spin = QDoubleSpinBox()
        self.wait_spin.setRange(0.05, 60.0)
        self.wait_spin.setSingleStep(0.25)
        self.wait_spin.setDecimals(2)
        self.wait_spin.setValue(0.5)
        opt_row.addWidget(self.wait_spin)
        add_wait = QPushButton("Add Wait")
        add_wait.setProperty("kind", "slate")
        add_wait.clicked.connect(lambda: self._add_wait())
        opt_row.addWidget(add_wait)
        opt_row.addStretch()
        lay.addLayout(opt_row)

        self.macro_list = QListWidget()
        self.macro_list.setFont(QFont("Consolas", 10))
        self.macro_list.setMinimumHeight(140)
        lay.addWidget(self.macro_list, 1)

        edit_row = QHBoxLayout()
        edit_row.addStretch()
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(lambda: self._remove_step())
        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(lambda: self._clear_steps())
        edit_row.addWidget(remove_btn)
        edit_row.addWidget(clear_btn)
        edit_row.addSpacing(10)
        edit_row.addWidget(QLabel("Repeat (0 = loop)"))
        self.repeat_spin = QSpinBox()
        self.repeat_spin.setRange(0, 9999)
        self.repeat_spin.setValue(1)
        edit_row.addWidget(self.repeat_spin)
        edit_row.addStretch()
        lay.addLayout(edit_row)

        self.macro_play_btn = self._big_button("green", lambda: self.toggle_macro(3))
        lay.addWidget(self.macro_play_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.macro_status = self._status_label("Ready")
        lay.addWidget(self.macro_status)
        return page

    def _build_settings_page(self):
        page, lay = self._page()
        lay.addWidget(self._title("Settings"))

        heading = QLabel("Keybinds")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setStyleSheet("font-size: 15px; font-weight: bold; text-decoration: underline;")
        lay.addWidget(heading)
        lay.addSpacing(6)

        self.key_buttons = {}
        for target, label in (
            ("record", "Start Recording"),
            ("clip", "Clip"),
            ("macro_play", "Play Macro"),
            ("macro_rec", "Record Macro"),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(label + ":"))
            row.addStretch()
            btn = QPushButton()
            btn.setFixedWidth(140)
            btn.clicked.connect(lambda _=False, t=target: self._start_rebind(t))
            row.addWidget(btn)
            lay.addLayout(row)
            self.key_buttons[target] = btn

        self.settings_note = QLabel("")
        self.settings_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.settings_note.setStyleSheet("color: #ef5350;")
        lay.addWidget(self.settings_note)
        lay.addSpacing(6)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Save Directory"))
        dir_row.addStretch()
        browse = QPushButton("Browse")
        browse.clicked.connect(lambda: self._select_directory())
        dir_row.addWidget(browse)
        lay.addLayout(dir_row)

        self.dir_label = QLabel(self.save_directory)
        self.dir_label.setWordWrap(True)
        self.dir_label.setProperty("muted", True)
        self.dir_label.setStyleSheet("font-size: 11px;")
        lay.addWidget(self.dir_label)

        self.audio_check = QCheckBox("Record Audio")
        self.audio_check.setChecked(True)
        lay.addWidget(self.audio_check)

        length_row = QHBoxLayout()
        length_row.addWidget(QLabel("Clip length (s):"))
        length_row.addStretch()
        self.length_spin = QSpinBox()
        self.length_spin.setRange(5, 120)
        self.length_spin.setSingleStep(5)
        self.length_spin.setValue(self.clip_duration)
        self.length_spin.valueChanged.connect(lambda _=0: self._apply_duration())
        length_row.addWidget(self.length_spin)
        lay.addLayout(length_row)

        lay.addStretch()
        return page

    def _build_tray(self):
        self.tray_ok = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray = QSystemTrayIcon(make_icon(False), self)
        self.tray.setToolTip("Clippy")

        menu = QMenu()
        menu.addAction("Show Clippy", self.show_window)
        menu.addSeparator()
        self.act_buffer = menu.addAction("Start buffer", self.toggle_buffer)
        self.act_clip = menu.addAction("Save clip", self.save_clip)
        menu.addSeparator()
        self.act_macro = menu.addAction("Play macro", lambda: self.toggle_macro(0))
        self.act_macro_rec = menu.addAction("Record mouse macro", lambda: self.toggle_macro_recording())
        menu.addSeparator()
        menu.addAction("Open save folder", self.open_save_folder)
        menu.addAction("Quit", self.quit_app)
        menu.aboutToShow.connect(self.refresh_tray_menu)

        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.messageClicked.connect(self._toast_clicked)
        if self.tray_ok:
            self.tray.show()

    def refresh_tray_menu(self):
        self.act_buffer.setText("Stop buffer" if self.is_running else "Start buffer")
        self.act_clip.setEnabled(self.is_running)
        self.act_macro.setText("Stop macro" if self._macro_running or self._macro_countdown else "Play macro")
        self.act_macro_rec.setText("Stop recording mouse" if self._macro_recording else "Record mouse macro")

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def _toast_clicked(self):
        if self._toast_folder and os.path.isdir(self._toast_folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._toast_folder))

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def open_save_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.save_directory))

    def update_tray_state(self):
        self.tray.setIcon(make_icon(self.is_running))
        self.tray.setToolTip("Clippy - recording" if self.is_running else "Clippy")

    def notify(self, title, message="", folder=None):
        self.ui(lambda: self._show_toast(title, message, folder))

    def _show_toast(self, title, message, folder):
        self._toast_folder = folder
        if self.tray_ok:
            self.tray.showMessage(title, message or "Clippy",
                                  QSystemTrayIcon.MessageIcon.NoIcon, 4000)

    def closeEvent(self, event):
        if self._quitting:
            event.accept()
            return
        if not self.tray_ok:
            event.ignore()
            self.quit_app()
            return
        event.ignore()
        self.hide()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.notify("Clippy is still running", "It's in the system tray. Hotkeys still work.")

    def quit_app(self):
        self._quitting = True
        self.is_running = False
        self.is_screen_recording = False
        self._macro_stop.set()
        self.audio_recorder.stop()
        self.screen_audio.stop()
        try:
            if self._rec_listener:
                self._rec_listener.stop()
        except Exception:
            pass
        try:
            self.hotkey_listener.stop()
        except Exception:
            pass
        self.tray.hide()
        QApplication.quit()

    def refresh_labels(self):
        for target, btn in self.key_buttons.items():
            btn.setText(self.keys[target])
            set_kind(btn, "")
        state = "Stop" if self.is_running else "Record Buffer"
        self.toggle_btn.setText("{} ({})".format(state, self.keys["record"]))
        self.clip_btn.setText("SAVE CLIP ({})".format(self.keys["clip"]))
        self.update_macro_buttons()

    def _start_rebind(self, target):
        self.rebinding_target = target
        self.settings_note.setText("")
        btn = self.key_buttons[target]
        btn.setText("Press Key...")
        set_kind(btn, "amber")

    def _finish_rebind(self, key_str, changed):
        self.refresh_labels()
        if changed:
            self.settings_note.setText("")
        else:
            self.settings_note.setText("{} is already used by another action".format(key_str))

    def _on_key_release(self, key):
        self._held.pop(self._parse_key_name(key), None)

    def _on_key_press(self, key):
        try:
            self._handle_key_press(key)
        except Exception:
            traceback.print_exc()

    def _handle_key_press(self, key):
        key_str = self._parse_key_name(key)
        now = time.time()

        last = self._held.get(key_str)
        self._held[key_str] = now
        if last is not None and now - last < 1.2:
            return

        if self.rebinding_target:
            target = self.rebinding_target
            self.rebinding_target = None
            taken = [v for k, v in self.keys.items() if k != target]
            if key_str in taken:
                self.ui(lambda: self._finish_rebind(key_str, False))
            else:
                self.keys[target] = key_str
                self.ui(lambda: self._finish_rebind(key_str, True))
            return

        if key_str == self.keys["record"]:
            print("Hotkey:", key_str, "-> toggle buffer")
            self.ui(self.toggle_buffer)
        elif key_str == self.keys["clip"]:
            print("Hotkey:", key_str, "-> save clip")
            if self.is_running:
                self.ui(self.save_clip)
            else:
                print("Not recording yet - press", self.keys["record"], "first")
        elif key_str == self.keys["macro_play"]:
            self.ui(lambda: self.toggle_macro(0))
        elif key_str == self.keys["macro_rec"]:
            self.ui(lambda: self.toggle_macro_recording())

    def _parse_key_name(self, key):
        if hasattr(key, "name") and key.name:
            return key.name.upper()
        elif hasattr(key, "char") and key.char:
            return key.char.upper()
        return str(key).replace("Key.", "").upper()

    def set_status(self, text):
        print(text)
        self.ui(lambda: self.status_label.setText("Status: " + text))

    def _apply_duration(self):
        val = self.length_spin.value()
        if val == self.clip_duration:
            return
        self.clip_duration = val
        self.frame_buffer = deque(list(self.frame_buffer), maxlen=self.fps * val)
        self.audio_recorder.update_duration(val)

    def _select_directory(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose where clips are saved", self.save_directory)
        if folder:
            self.save_directory = folder
            self.dir_label.setText(folder)
            print("Save directory set to:", folder)

    def toggle_buffer(self):
        if not self.is_running:
            self._apply_duration()
            self.is_running = True
            self.frame_buffer.clear()
            set_kind(self.toggle_btn, "red")
            self.clip_btn.setEnabled(True)

            if self.audio_check.isChecked():
                self.audio_recorder.start()
            self.set_status("Buffer recording...")
            self.capture_thread = threading.Thread(target=self._record_loop, daemon=True)
            self.capture_thread.start()
            self.notify("Buffer recording", "Press {} to save the last {}s.".format(
                self.keys["clip"], self.clip_duration))
        else:
            self.is_running = False
            self.audio_recorder.stop()
            set_kind(self.toggle_btn, "green")
            self.clip_btn.setEnabled(False)
            self.set_status("Idle")
            self.notify("Buffer stopped", "Clippy is no longer recording.")
        self.refresh_labels()
        self.update_tray_state()

    def _record_loop(self):
        with mss.mss() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            frame_delay = 1.0 / self.fps

            while self.is_running:
                start_time = time.time()
                try:
                    sct_img = sct.grab(monitor)
                    frame_np = np.array(sct_img)
                    frame_bgr = cv2.cvtColor(frame_np, cv2.COLOR_BGRA2BGR)
                    self.frame_buffer.append(frame_bgr)
                except Exception:
                    pass

                sleep_time = frame_delay - (time.time() - start_time)
                if sleep_time > 0:
                    time.sleep(sleep_time)

    def save_clip(self):
        if not self.frame_buffer:
            self.set_status("Buffer empty - nothing to save")
            self.notify("Nothing to save", "The buffer is empty.")
            return

        frames = list(self.frame_buffer)
        timestamp = unique_timestamp()
        target_dir = self.save_directory
        record_audio = self.audio_check.isChecked()

        audio_chunks = self.audio_recorder._snapshot() if record_audio else []
        print("Saving clip: {} frames, {} audio chunks -> {}".format(
            len(frames), len(audio_chunks), target_dir))
        self.set_status("Saving clip...")
        self.notify("Saving clip...", "Hang tight, encoding your clip.")

        def _run():
            try:
                self._write_clip(frames, audio_chunks, timestamp, target_dir)
            except Exception:
                traceback.print_exc()
                self.set_status("Save FAILED - see console")
                self.notify("Clip failed", "Something went wrong while saving.")

        threading.Thread(target=_run, daemon=True).start()

    def _write_clip(self, frames, audio_chunks, timestamp, target_dir):
        os.makedirs(target_dir, exist_ok=True)
        temp_video = os.path.join(target_dir, "temp_video_{}.mp4".format(timestamp))
        temp_audio = os.path.join(target_dir, "temp_audio_{}.wav".format(timestamp))
        final_output = os.path.join(target_dir, "gameplay_clip_{}.mp4".format(timestamp))

        has_audio = False
        if audio_chunks:
            try:
                audio_data = np.concatenate(audio_chunks, axis=0)
                audio_int16 = (np.clip(audio_data, -1.0, 1.0) * 32767).astype(np.int16)
                wavfile.write(temp_audio, self.audio_recorder.sample_rate, audio_int16)
                has_audio = True
            except Exception:
                traceback.print_exc()

        height, width, _ = frames[0].shape
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(temp_video, fourcc, float(self.fps), (width, height))
        if not out.isOpened():
            raise RuntimeError("OpenCV could not open a video writer for " + temp_video)

        for frame in frames:
            if frame.shape[:2] != (height, width):
                frame = cv2.resize(frame, (width, height))
            out.write(frame)
        out.release()
        del out
        time.sleep(0.2)

        merged = False
        if has_audio and os.path.exists(temp_video):
            merged = merge_video_audio(temp_video, temp_audio, final_output)
            if merged:
                safe_remove(temp_video)
            safe_remove(temp_audio)

        if not merged and os.path.exists(temp_video):
            safe_replace(temp_video, final_output)

        if os.path.exists(final_output):
            self.set_status("Saved " + os.path.basename(final_output))
            print("Saved to:", final_output)
            self.notify("Clip saved", os.path.basename(final_output), folder=target_dir)
        else:
            self.set_status("Save FAILED - no file created")
            self.notify("Clip failed", "No file was created.")

    def toggle_screen_recording(self):
        if not self.is_screen_recording:
            self.is_screen_recording = True
            self.screen_rec_btn.setText("Stop Screen Recording")
            set_kind(self.screen_rec_btn, "green")
            self.screen_rec_status.setText("Status: Recording...")
            self._color_label(self.screen_rec_status, "#ef5350")
            self.screen_rec_start_time = time.time()

            record_audio = self.audio_check.isChecked()
            target_dir = self.save_directory
            if record_audio:
                self.screen_audio.start()

            threading.Thread(target=self._run_screen_recording,
                             args=(record_audio, target_dir), daemon=True).start()
            self._update_screen_rec_timer()
            self.screen_timer.start()
            self.notify("Screen recording started", "Clippy is recording your screen.")
        else:
            self.is_screen_recording = False
            self.screen_rec_btn.setText("Start Screen Recording")
            set_kind(self.screen_rec_btn, "red")
            self.screen_rec_status.setText("Status: Processing...")
            self._color_label(self.screen_rec_status, "#64b5f6")

    def _update_screen_rec_timer(self):
        if not self.is_screen_recording:
            self.screen_timer.stop()
            return
        elapsed = int(time.time() - self.screen_rec_start_time)
        hrs = elapsed // 3600
        mins = (elapsed % 3600) // 60
        secs = elapsed % 60
        self.screen_rec_timer_label.setText("{:02d}:{:02d}:{:02d}".format(hrs, mins, secs))

    def _reset_screen_rec_ui(self):
        self.screen_rec_status.setText("Status: Idle")
        self._color_label(self.screen_rec_status, "#8b8fa8")
        self.screen_rec_timer_label.setText("00:00:00")

    def _run_screen_recording(self, record_audio, target_dir):
        timestamp = unique_timestamp()
        temp_video = os.path.join(target_dir, "temp_rec_video_{}.mp4".format(timestamp))
        temp_audio = os.path.join(target_dir, "temp_rec_audio_{}.wav".format(timestamp))
        final_output = os.path.join(target_dir, "recording_{}.mp4".format(timestamp))

        with mss.mss() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            width = monitor["width"]
            height = monitor["height"]
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            out = cv2.VideoWriter(temp_video, fourcc, float(self.fps), (width, height))

            frame_delay = 1.0 / self.fps
            while self.is_screen_recording:
                start_time = time.time()
                try:
                    sct_img = sct.grab(monitor)
                    frame_np = np.array(sct_img)
                    frame_bgr = cv2.cvtColor(frame_np, cv2.COLOR_BGRA2BGR)
                    out.write(frame_bgr)
                except Exception:
                    pass

                sleep_time = frame_delay - (time.time() - start_time)
                if sleep_time > 0:
                    time.sleep(sleep_time)

            out.release()
            del out

        time.sleep(0.2)

        has_audio = False
        if record_audio:
            self.screen_audio.stop()
            has_audio = self.screen_audio.save_wav(temp_audio)

        merged = False
        if has_audio and os.path.exists(temp_video):
            merged = merge_video_audio(temp_video, temp_audio, final_output)
            if merged:
                safe_remove(temp_video)
            safe_remove(temp_audio)

        if not merged and os.path.exists(temp_video):
            safe_replace(temp_video, final_output)

        if os.path.exists(final_output):
            self.notify("Recording saved", os.path.basename(final_output), folder=target_dir)
        else:
            self.notify("Recording failed", "No file was created.")

        self.ui(self._reset_screen_rec_ui)

    def set_macro_status(self, text, color="#8b8fa8"):
        self.macro_status.setText(text)
        self._color_label(self.macro_status, color)

    def update_macro_buttons(self):
        busy = self._macro_running or self._macro_countdown
        play_key = self.keys["macro_play"]
        rec_key = self.keys["macro_rec"]
        if busy:
            self.macro_play_btn.setText("■ Stop Macro ({})".format(play_key))
            set_kind(self.macro_play_btn, "orange")
        else:
            self.macro_play_btn.setText("▶ Play Macro ({})".format(play_key))
            set_kind(self.macro_play_btn, "green")
        if self._macro_recording:
            self.macro_rec_btn.setText("■ Stop Recording ({})".format(rec_key))
            set_kind(self.macro_rec_btn, "orange")
        else:
            self.macro_rec_btn.setText("● Record Mouse ({})".format(rec_key))
            set_kind(self.macro_rec_btn, "red")

    def _step_text(self, s):
        t = s["type"]
        if t == "move":
            if s["dx"] < 0:
                name = "Mouse Left"
            elif s["dx"] > 0:
                name = "Mouse Right"
            elif s["dy"] < 0:
                name = "Mouse Up"
            else:
                name = "Mouse Down"
            return "{} {}px".format(name, abs(s["dx"]) or abs(s["dy"]))
        if t == "click":
            return "{} Click".format(s["button"].capitalize())
        if t == "down":
            return "Hold {} Button".format(s["button"].capitalize())
        if t == "up":
            return "Release {} Button".format(s["button"].capitalize())
        if t == "path":
            moves = sum(1 for e in s["events"] if e[1] == "move")
            clicks = sum(1 for e in s["events"] if e[1] == "down")
            text = "Recorded path {}s ({} moves".format(s["secs"], moves)
            if clicks:
                text += ", {} clicks".format(clicks)
            return text + ")"
        return "Wait {}s".format(s["secs"])

    def _add_step(self, step):
        if self._macro_running or self._macro_countdown or self._macro_recording:
            return
        self.macro_steps.append(step)
        self.macro_list.addItem("{:>2}. {}".format(len(self.macro_steps), self._step_text(step)))
        self.macro_list.scrollToBottom()

    def _add_move(self, ux, uy):
        dist = self.dist_spin.value()
        self._add_step({"type": "move", "dx": ux * dist, "dy": uy * dist})

    def _add_wait(self):
        secs = round(self.wait_spin.value(), 2)
        self._add_step({"type": "wait", "secs": secs})

    def _refresh_macro_list(self):
        self.macro_list.clear()
        for i, s in enumerate(self.macro_steps, 1):
            self.macro_list.addItem("{:>2}. {}".format(i, self._step_text(s)))

    def _remove_step(self):
        if self._macro_running or self._macro_countdown:
            return
        row = self.macro_list.currentRow()
        if row >= 0:
            del self.macro_steps[row]
            self._refresh_macro_list()

    def _clear_steps(self):
        if self._macro_running or self._macro_countdown:
            return
        self.macro_steps.clear()
        self._refresh_macro_list()

    def toggle_macro(self, delay=3):
        if self._macro_running or self._macro_countdown:
            self.stop_macro()
            return
        if self._macro_recording:
            self.set_macro_status("Wait for the recording to finish", "#ef5350")
            return
        if not self.macro_steps:
            self.set_macro_status("Add some steps first", "#ef5350")
            return
        self._macro_stop.clear()
        if delay > 0:
            self._macro_countdown = True
            self._countdown_left = delay
            self.update_macro_buttons()
            self._countdown_step()
            self.countdown_timer.start()
        else:
            self._start_macro()

    def _countdown_step(self):
        if not self._macro_countdown:
            self.countdown_timer.stop()
            return
        if self._countdown_left <= 0:
            self.countdown_timer.stop()
            self._macro_countdown = False
            self._start_macro()
            return
        self.set_macro_status("Starting in {}s - switch to your game...".format(self._countdown_left), "#64b5f6")
        self._countdown_left -= 1

    def _start_macro(self):
        repeat = self.repeat_spin.value()
        self._macro_running = True
        self.update_macro_buttons()
        self.set_macro_status("Running... ({} to stop)".format(self.keys["macro_play"]), "#66bb6a")
        self.notify("Macro running", "Press {} to stop.".format(self.keys["macro_play"]))
        threading.Thread(
            target=self._run_macro, args=(list(self.macro_steps), repeat), daemon=True
        ).start()

    def stop_macro(self):
        self._macro_stop.set()
        if self._macro_countdown:
            self._macro_countdown = False
            self.countdown_timer.stop()
            self.update_macro_buttons()
            self.set_macro_status("Stopped")

    def _run_macro(self, steps, repeat):
        held = []
        try:
            n = 0
            while not self._macro_stop.is_set() and (repeat == 0 or n < repeat):
                for i, step in enumerate(steps):
                    if self._macro_stop.is_set():
                        break
                    self.ui(lambda i=i: self._macro_highlight(i))
                    self._do_step(step, held)
                n += 1
                self._macro_stop.wait(0.01)
        except Exception:
            traceback.print_exc()
        finally:
            for btn in held:
                try:
                    self._mouse.release(btn)
                except Exception:
                    pass
            stopped = self._macro_stop.is_set()
            self.ui(lambda: self._macro_done(stopped))

    def _do_step(self, step, held):
        t = step["type"]
        if t == "wait":
            self._macro_stop.wait(step["secs"])
            return
        if t == "path":
            start = time.perf_counter()
            for ev in step["events"]:
                delay = ev[0] - (time.perf_counter() - start)
                if delay > 0 and self._macro_stop.wait(delay):
                    return
                if self._macro_stop.is_set():
                    return
                if ev[1] == "move":
                    self._mouse.move(ev[2], ev[3])
                else:
                    btn = mouse.Button.left if ev[2] == "left" else mouse.Button.right
                    if ev[1] == "down":
                        self._mouse.press(btn)
                        if btn not in held:
                            held.append(btn)
                    else:
                        self._mouse.release(btn)
                        if btn in held:
                            held.remove(btn)
            rest = step["secs"] - (time.perf_counter() - start)
            if rest > 0:
                self._macro_stop.wait(rest)
            return
        if t == "move":
            self._mouse.move(step["dx"], step["dy"])
        else:
            btn = mouse.Button.left if step["button"] == "left" else mouse.Button.right
            if t == "click":
                self._mouse.press(btn)
                self._macro_stop.wait(0.03)
                self._mouse.release(btn)
            elif t == "down":
                self._mouse.press(btn)
                if btn not in held:
                    held.append(btn)
            elif t == "up":
                self._mouse.release(btn)
                if btn in held:
                    held.remove(btn)
        self._macro_stop.wait(0.02)

    def _macro_highlight(self, idx):
        self.macro_list.setCurrentRow(idx)
        self.macro_list.scrollToItem(self.macro_list.item(idx))

    def _macro_done(self, stopped):
        self._macro_running = False
        self.macro_list.clearSelection()
        self.macro_list.setCurrentRow(-1)
        self.update_macro_buttons()
        if stopped:
            self.set_macro_status("Stopped")
        else:
            self.set_macro_status("Finished", "#66bb6a")
        self.notify("Macro stopped" if stopped else "Macro finished", "Clippy macro")

    def toggle_macro_recording(self):
        if self._macro_recording:
            self._finish_recording()
            return
        if self._macro_running or self._macro_countdown:
            self.set_macro_status("Stop the macro before recording", "#ef5350")
            return
        self._rec_secs = float(self.rec_secs_spin.value())

        self._rec_rect = None
        if self.isVisible() and not self.isMinimized():
            g = self.frameGeometry()
            r = self.devicePixelRatioF()
            self._rec_rect = (g.left() * r, g.top() * r, g.right() * r, g.bottom() * r)

        px, py = self._mouse.position
        self._rec_events = [("pos", 0.0, int(px), int(py))]
        self._rec_latest = None
        self._rec_last_sample = 0.0
        self._rec_t0 = time.perf_counter()
        self._macro_recording = True
        self._rec_listener = mouse.Listener(on_move=self._rec_on_move, on_click=self._rec_on_click)
        self._rec_listener.start()
        self.update_macro_buttons()
        self.notify("Recording mouse", "Recording for {:g}s. Press {} to stop early.".format(
            self._rec_secs, self.keys["macro_rec"]))
        self.rec_timer.start()
        self._rec_tick()

    def _rec_tick(self):
        if not self._macro_recording:
            self.rec_timer.stop()
            return
        left = self._rec_secs - (time.perf_counter() - self._rec_t0)
        if left <= 0:
            self._finish_recording()
            return
        self.set_macro_status("● Recording... {:.1f}s left".format(left), "#ef5350")

    def _rec_inside(self, x, y):
        r = self._rec_rect
        return bool(r) and r[0] <= x <= r[2] and r[1] <= y <= r[3]

    def _rec_on_move(self, x, y):
        now = time.perf_counter()
        t = now - self._rec_t0
        if t > self._rec_secs:
            return
        self._rec_latest = (t, int(x), int(y))
        if now - self._rec_last_sample >= 0.01:
            self._rec_last_sample = now
            self._rec_events.append(("pos", t, int(x), int(y)))

    def _rec_on_click(self, x, y, button, pressed):
        name = getattr(button, "name", None)
        t = time.perf_counter() - self._rec_t0
        if name not in ("left", "right") or t > self._rec_secs or self._rec_inside(x, y):
            return
        self._rec_events.append(("pos", t, int(x), int(y)))
        self._rec_events.append(("down" if pressed else "up", t, name))

    def _finish_recording(self):
        if not self._macro_recording:
            return
        self._macro_recording = False
        self.rec_timer.stop()
        try:
            self._rec_listener.stop()
        except Exception:
            pass
        self._rec_listener = None
        elapsed = min(time.perf_counter() - self._rec_t0, self._rec_secs)

        events = list(self._rec_events)
        if self._rec_latest and self._rec_latest[0] >= events[-1][1]:
            events.append(("pos",) + self._rec_latest)

        path, prev, pressed = [], None, set()
        for ev in events:
            if ev[0] == "pos":
                _, t, x, y = ev
                if prev is not None and (x != prev[0] or y != prev[1]):
                    path.append((round(t, 3), "move", x - prev[0], y - prev[1]))
                prev = (x, y)
            elif ev[0] == "down":
                pressed.add(ev[2])
                path.append((round(ev[1], 3), "down", ev[2]))
            elif ev[0] == "up" and ev[2] in pressed:
                pressed.discard(ev[2])
                path.append((round(ev[1], 3), "up", ev[2]))
        for b in pressed:
            path.append((round(elapsed, 3), "up", b))

        self.update_macro_buttons()
        if not path:
            self.set_macro_status("No mouse movement captured", "#ef5350")
            self.notify("Nothing recorded", "No mouse movement was captured.")
            return
        self._add_step({"type": "path", "events": path, "secs": round(elapsed, 2)})
        self.set_macro_status(
            "Recorded {:.1f}s - press Play ({})".format(elapsed, self.keys["macro_play"]), "#66bb6a")
        self.notify("Mouse macro recorded",
                    "{:.1f}s captured. Press {} to play it.".format(elapsed, self.keys["macro_play"]))


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(make_icon())
    apply_dark_palette(app)
    app.setStyleSheet(build_stylesheet())

    window = ClippyWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
