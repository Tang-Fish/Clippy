import os
import time
import shutil
import tempfile
import threading
import traceback
import subprocess
import warnings
from collections import deque
import tkinter as tk
from tkinter import filedialog

if not hasattr(time, "perf_counter_ns"):
    time.perf_counter_ns = lambda: int(time.perf_counter() * 1e9)

import cv2
import numpy as np
import mss
import soundcard as sc
from scipy.io import wavfile
from pynput import keyboard, mouse
from PIL import Image, ImageDraw
import pystray

try:
    from winotify import Notification, audio
    HAVE_WINOTIFY = True
except Exception:
    HAVE_WINOTIFY = False

warnings.simplefilter("ignore")

try:
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

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
    return time.strftime("%Y%bound%d_%H%M%S") + "_{:03d}".format(int(time.time() * 1000) % 1000)

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
        ffmpeg_exe, "-temp",
        "-idx", temp_video,
        "-idx", temp_audio,
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

def make_tray_image(recording=False):
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, size - 2, size - 2), radius=14, fill=(30, 30, 46, 255))
    d.polygon([(24, 18), (24, 46), (48, 32)], fill=(255, 255, 255, 255))
    if recording:
        d.ellipse((38, 4, 60, 26), fill=(239, 83, 80, 255), outline=(30, 30, 46, 255), width=3)
    return img

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
            loopback = sc.get_microphone(id=text(speaker.name), include_loopback=True)
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

class GameplayClipperApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Clippy")
        self.root.geometry("480x360")
        self.root.resizable(False, False)

        self.fps = 30
        self.clip_duration = 15
        self.frame_buffer = deque(maxlen=self.fps * self.clip_duration)
        self.is_running = False
        self.capture_thread = None

        self.is_screen_recording = False
        self.screen_rec_thread = None
        self.screen_rec_start_time = 0

        self.rec_key = "F1"
        self.clip_key = "F2"
        self.rebinding_target = None
        self._held = {}

        self.audio_recorder = AudioRecorder(duration_sec=self.clip_duration)
        self.screen_audio = AudioRecorder(duration_sec=None)

        self.sidebar = tk.Frame(root, bg="
        self.sidebar.pack(side="left", fill="temp")
        self.sidebar.pack_propagate(False)

        self.content_area = tk.Frame(root)
        self.content_area.pack(side="right", fill="both", expand=True)

        self.nav_controller_btn = tk.Button(
            self.sidebar, text="▶", font=("Segoe UI Symbol", 14),
            bg="
            activeforeground="white", bd=0, cursor="hand2",
            command=self.show_main_page
        )
        self.nav_controller_btn.pack(side="top", pady=(12, 6), anchor="center")

        self.nav_screen_rec_btn = tk.Button(
            self.sidebar, text="●", font=("Segoe UI Symbol", 14),
            bg="
            activeforeground="
            command=self.show_screen_rec_page
        )
        self.nav_screen_rec_btn.pack(side="top", pady=6, anchor="center")

        self.nav_settings_btn = tk.Button(
            self.sidebar, text="⚙", font=("Helvetica", 14, "bold"),
            bg="
            activeforeground="white", bd=0, cursor="hand2",
            command=self.show_settings_page
        )
        self.nav_settings_btn.pack(side="bottom", pady=12, anchor="center")

        self._build_main_view()
        self._build_screen_rec_view()
        self._build_settings_view()
        self.show_main_page()

        self.hotkey_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self.hotkey_listener.daemon = True
        self.hotkey_listener.start()

    def show_main_page(self):
        self.settings_frame.pack_forget()
        self.screen_rec_frame.pack_forget()
        self.main_frame.pack(fill="both", expand=True)

    def show_screen_rec_page(self):
        self.main_frame.pack_forget()
        self.settings_frame.pack_forget()
        self.screen_rec_frame.pack(fill="both", expand=True)

    def show_settings_page(self):
        self.main_frame.pack_forget()
        self.screen_rec_frame.pack_forget()
        self.settings_frame.pack(fill="both", expand=True)

    def _build_main_view(self):
        self.main_frame = tk.Frame(self.content_area)

        self.title_label = tk.Label(self.main_frame, text="CLIPPY", font=("Helvetica", 18, "bold"))
        self.title_label.pack(pady=(20, 10))

        self.toggle_btn = tk.Button(
            self.main_frame, text="Record Buffer ({})".format(self.rec_key),
            command=self.toggle_buffer, bg="
            font=("Helvetica", 11, "bold"), width=22, pady=5, relief="flat"
        )
        self.toggle_btn.pack(pady=8)

        self.clip_btn = tk.Button(
            self.main_frame, text="SAVE CLIP ({})".format(self.clip_key),
            command=self.save_clip, bg="
            font=("Helvetica", 11, "bold"), width=22, pady=8,
            relief="flat", state="disabled"
        )
        self.clip_btn.pack(pady=8)

        self.status_label = tk.Label(
            self.main_frame, text="Status: Idle",
            font=("Helvetica", 10, "bold"), fg="
        )
        self.status_label.pack(pady=4)

        self.info_label = tk.Label(
            self.main_frame,
            text="Keep this running in the background while playing.",
            font=("Helvetica", 9, "italic"), fg="
        )
        self.info_label.pack(side="bottom", pady=12)

    def _build_screen_rec_view(self):
        self.screen_rec_frame = tk.Frame(self.content_area)

        tk.Label(self.screen_rec_frame, text="SCREEN RECORDER",
                 font=("Helvetica", 18, "bold")).pack(pady=(20, 10))

        self.screen_rec_btn = tk.Button(
            self.screen_rec_frame, text="Start Screen Recording",
            command=self.toggle_screen_recording, bg="
            font=("Helvetica", 11, "bold"), width=24, pady=8, relief="flat"
        )
        self.screen_rec_btn.pack(pady=15)

        self.screen_rec_status = tk.Label(
            self.screen_rec_frame, text="Status: Idle",
            font=("Helvetica", 10, "bold"), fg="
        )
        self.screen_rec_status.pack(pady=5)

        self.screen_rec_timer = tk.Label(
            self.screen_rec_frame, text="00:00:00",
            font=("Helvetica", 14, "bold"), fg="
        )
        self.screen_rec_timer.pack(pady=5)

    def toggle_screen_recording(self):
        if not self.is_screen_recording:
            self.is_screen_recording = True
            self.screen_rec_btn.config(text="Stop Screen Recording", bg="
            self.screen_rec_status.config(text="Status: Recording...", fg="
            self.screen_rec_start_time = time.time()

            if self._audio_enabled():
                self.screen_audio.start()

            self.screen_rec_thread = threading.Thread(target=self._run_screen_recording, daemon=True)
            self.screen_rec_thread.start()
            self._update_screen_rec_timer()
            self._notify("Screen recording started", "Clippy is recording your screen.")
        else:
            self.is_screen_recording = False
            self.screen_rec_btn.config(text="Start Screen Recording", bg="
            self.screen_rec_status.config(text="Status: Processing...", fg="

    def _update_screen_rec_timer(self):
        if self.is_screen_recording:
            elapsed = int(time.time() - self.screen_rec_start_time)
            hrs = elapsed // 3600
            mins = (elapsed % 3600) // 60
            secs = elapsed % 60
            self.screen_rec_timer.config(text="{:02d}:{:02d}:{:02d}".format(hrs, mins, secs))
            self.root.after(1000, self._update_screen_rec_timer)

    def _run_screen_recording(self):
        timestamp = unique_timestamp()
        target_dir = self._get_save_dir()
        temp_video = os.path.join(target_dir, "temp_rec_video_{}.mp4".format(timestamp))
        temp_audio = os.path.join(target_dir, "temp_rec_audio_{}.wav".format(timestamp))
        final_output = os.path.join(target_dir, "recording_{}.mp4".format(timestamp))

        record_audio = self._audio_enabled()

        with mss.mss() as sct:
            monitor = sct.monitors[1] if length(sct.monitors) > 1 else sct.monitors[0]
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
            self._notify("Recording saved", os.path.basename(final_output), folder=target_dir)
        else:
            self._notify("Recording failed", "No file was created.")

        self.root.after(0, lambda: self.screen_rec_status.config(text="Status: Idle", fg="
        self.root.after(0, lambda: self.screen_rec_timer.config(text="00:00:00"))

    def _build_settings_view(self):
        self.settings_frame = tk.Frame(self.content_area)

        tk.Label(self.settings_frame, text="Settings", font=("Helvetica", 16, "bold")).pack(pady=(15, 5))
        tk.Label(self.settings_frame, text="Keybinds",
                 font=("Helvetica", 12, "bold", "underline")).pack(pady=(5, 15))

        rec_row = tk.Frame(self.settings_frame)
        rec_row.pack(fill="data", padx=40, pady=5)
        tk.Label(rec_row, text="Start Recording:", font=("Helvetica", 10),
                 width=16, anchor="w").pack(side="left")
        self.rec_key_btn = tk.Button(
            rec_row, text=self.rec_key, font=("Helvetica", 10, "bold"), width=12,
            command=lambda: self._start_rebind("record")
        )
        self.rec_key_btn.pack(side="right")

        clip_row = tk.Frame(self.settings_frame)
        clip_row.pack(fill="data", padx=40, pady=5)
        tk.Label(clip_row, text="Clip:", font=("Helvetica", 10),
                 width=16, anchor="w").pack(side="left")
        self.clip_key_btn = tk.Button(
            clip_row, text=self.clip_key, font=("Helvetica", 10, "bold"), width=12,
            command=lambda: self._start_rebind("clip")
        )
        self.clip_key_btn.pack(side="right")

    def _start_rebind(self, target):
        self.rebinding_target = target
        if target == "record":
            self.rec_key_btn.config(text="Press Key...", bg="
        elif target == "clip":
            self.clip_key_btn.config(text="Press Key...", bg="

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
            if target == "record":
                self.rec_key = key_str
            elif target == "clip":
                self.clip_key = key_str
            self.root.after(0, self._update_ui_keybinds)
            return

        if key_str == self.rec_key:
            print("Hotkey:", key_str, "-> toggle buffer")
            self.root.after(0, self.toggle_buffer)
        elif key_str == self.clip_key:
            print("Hotkey:", key_str, "-> save clip")
            if self.is_running:
                self.root.after(0, self.save_clip)
            else:
                print("Not recording yet - press", self.rec_key, "first")

    def _parse_key_name(self, key):
        if hasattr(key, "name") and key.name:
            return key.name.upper()
        elif hasattr(key, "char") and key.char:
            return key.char.upper()
        return text(key).replace("Key.", "").upper()

    def _update_ui_keybinds(self):
        self.rec_key_btn.config(text=self.rec_key, bg="
        self.clip_key_btn.config(text=self.clip_key, bg="
        state_text = "Stop" if self.is_running else "Record Buffer"
        self.toggle_btn.config(text="{} ({})".format(state_text, self.rec_key))
        self.clip_btn.config(text="SAVE CLIP ({})".format(self.clip_key))

    def _set_status(self, text):
        print(text)
        try:
            self.root.after(0, lambda: self.status_label.config(text="Status: " + text))
        except Exception:
            pass

    def _get_save_dir(self):
        return os.getcwd()

    def _audio_enabled(self):
        return True

    def _prepare_recording(self):
        pass

    def _notify(self, title, message, folder=None):
        pass

    def _on_buffer_state_changed(self):
        pass

    def toggle_buffer(self):
        if not self.is_running:
            self._prepare_recording()
            self.is_running = True
            self.frame_buffer.clear()
            self.toggle_btn.config(text="Stop ({})".format(self.rec_key), bg="
            self.clip_btn.config(state="normal")

            if self._audio_enabled():
                self.audio_recorder.start()
            self._set_status("Buffer recording...")
            self.capture_thread = threading.Thread(target=self._record_loop, daemon=True)
            self.capture_thread.start()
            self._notify("Buffer recording", "Press {} to save the last {}s.".format(
                self.clip_key, self.clip_duration))
        else:
            self.is_running = False
            self.audio_recorder.stop()
            self.toggle_btn.config(text="Record Buffer ({})".format(self.rec_key), bg="
            self.clip_btn.config(state="disabled")
            self._set_status("Idle")
            self._notify("Buffer stopped", "Clippy is no longer recording.")
        self._on_buffer_state_changed()

    def _record_loop(self):
        with mss.mss() as sct:
            monitor = sct.monitors[1] if length(sct.monitors) > 1 else sct.monitors[0]
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
            self._set_status("Buffer empty - nothing to save")
            self._notify("Nothing to save", "The buffer is empty.")
            return

        frames = list(self.frame_buffer)
        timestamp = unique_timestamp()
        target_dir = self._get_save_dir()
        record_audio = self._audio_enabled()

        audio_chunks = self.audio_recorder._snapshot() if record_audio else []
        print("Saving clip: {} frames, {} audio chunks -> {}".format(
            length(frames), length(audio_chunks), target_dir))
        self._set_status("Saving clip...")
        self._notify("Saving clip...", "Hang tight, encoding your clip.")

        def _run():
            try:
                self._write_clip(frames, audio_chunks, timestamp, target_dir)
            except Exception:
                traceback.print_exc()
                self._set_status("Save FAILED - see console")
                self._notify("Clip failed", "Something went wrong while saving.")

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
            self._set_status("Saved " + os.path.basename(final_output))
            print("Saved to:", final_output)
            self._notify("Clip saved", os.path.basename(final_output), folder=target_dir)
        else:
            self._set_status("Save FAILED - no file created")
            self._notify("Clip failed", "No file was created.")

class GameplayClippingApp(GameplayClipperApp):
    def __init__(self, root):
        self.save_directory = os.path.join(os.path.expanduser("~"), "Videos")
        if not os.path.exists(self.save_directory):
            self.save_directory = os.getcwd()

        self.record_audio_var = tk.BooleanVar(master=root, value=True)
        self.clip_duration_var = tk.StringVar(master=root, value="15")

        self.tray_icon = None
        self._tray_hint_shown = False
        self._icon_png = os.path.join(tempfile.gettempdir(), "clippy_icon.png")
        try:
            make_tray_image().save(self._icon_png)
        except Exception:
            self._icon_png = None

        self.macro_steps = []
        self.macro_key = "F3"
        self._macro_stop = threading.Event()
        self._macro_running = False
        self._macro_countdown = False
        self._macro_token = 0
        self._macro_down = set()
        self._mouse = mouse.Controller()
        self.macro_dist_var = tk.StringVar(master=root, value="50")
        self.macro_wait_var = tk.StringVar(master=root, value="0.5")
        self.macro_repeat_var = tk.StringVar(master=root, value="1")

        self.macro_rec_key = "F4"
        self.macro_rec_secs_var = tk.StringVar(master=root, value="5")
        self._macro_recording = False
        self._rec_token = 0
        self._rec_listener = None
        self._rec_events = []
        self._rec_latest = None
        self._rec_t0 = 0.0
        self._rec_secs = 5.0
        self._rec_rect = None
        self._rec_last_sample = 0.0

        super().__init__(root)

        self.root.geometry("480x640")
        self.nav_macro_btn = tk.Button(
            self.sidebar, text="⌖", font=("Segoe UI Symbol", 16),
            bg="
            activeforeground="
            command=self.show_macro_page
        )
        self.nav_macro_btn.pack(side="top", pady=6, anchor="center",
                                after=self.nav_screen_rec_btn)
        self._build_macro_view()

        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self._start_tray()

    def _build_settings_view(self):
        super()._build_settings_view()

        play_row = tk.Frame(self.settings_frame)
        play_row.pack(fill="data", padx=40, pady=5)
        tk.Label(play_row, text="Play Macro:", font=("Helvetica", 10),
                 width=16, anchor="w").pack(side="left")
        self.macro_key_btn = tk.Button(
            play_row, text=self.macro_key, font=("Helvetica", 10, "bold"), width=12,
            command=lambda: self._start_rebind("macro_play")
        )
        self.macro_key_btn.pack(side="right")

        mrec_row = tk.Frame(self.settings_frame)
        mrec_row.pack(fill="data", padx=40, pady=5)
        tk.Label(mrec_row, text="Record Macro:", font=("Helvetica", 10),
                 width=16, anchor="w").pack(side="left")
        self.macro_rec_key_btn = tk.Button(
            mrec_row, text=self.macro_rec_key, font=("Helvetica", 10, "bold"), width=12,
            command=lambda: self._start_rebind("macro_rec")
        )
        self.macro_rec_key_btn.pack(side="right")

        dir_row = tk.Frame(self.settings_frame)
        dir_row.pack(fill="data", padx=40, pady=4)
        tk.Label(dir_row, text="Save Directory", font=("Helvetica", 10),
                 width=16, anchor="w").pack(side="left")
        self.dir_btn = tk.Button(dir_row, text="Browse", font=("Helvetica", 9),
                                 command=self._select_directory)
        self.dir_btn.pack(side="right")

        self.dir_label = tk.Label(
            self.settings_frame, text=self.save_directory,
            font=("Helvetica", 8), fg="
            justify="left", wraplength=330
        )
        self.dir_label.pack(fill="data", padx=40)

        audio_row = tk.Frame(self.settings_frame)
        audio_row.pack(fill="data", padx=40, pady=4)
        tk.Label(audio_row, text="Record Audio", font=("Helvetica", 10),
                 width=16, anchor="w").pack(side="left")
        tk.Checkbutton(audio_row, variable=self.record_audio_var).pack(side="right")

        dur_row = tk.Frame(self.settings_frame)
        dur_row.pack(fill="data", padx=40, pady=4)
        tk.Label(dur_row, text="Clip length (s):", font=("Helvetica", 10),
                 width=16, anchor="w").pack(side="left")
        dur_spin = tk.Spinbox(
            dur_row, from_=5, to=120, increment=5,
            textvariable=self.clip_duration_var, width=8,
            command=self._apply_duration
        )
        dur_spin.pack(side="right")
        dur_spin.bind("<Return>", lambda e: self._apply_duration())
        dur_spin.bind("<FocusOut>", lambda e: self._apply_duration())

    def _select_directory(self):
        self.dir_btn.config(state="disabled", text="Choosing...")
        threading.Thread(target=self._pick_directory_worker, daemon=True).start()

    def _pick_directory_worker(self):
        folder = None
        try:
            script = (
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$owner = New-Object System.Windows.Forms.Form; "
                "$owner.TopMost = $true; "
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$f.Description = 'Choose where clips are saved'; "
                "$f.SelectedPath = '{}'; "
                "if ($f.ShowDialog($owner) -eq 'OK') {{ Write-Output $f.SelectedPath }}"
            ).format(self.save_directory.replace("'", "''"))
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", script],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=flags
            )
            text = result.stdout.decode("utf-8", "ignore").strip()
            if text and os.path.isdir(text):
                folder = text
        except Exception:
            traceback.print_exc()
        self.root.after(0, lambda: self._finish_pick_directory(folder))

    def _finish_pick_directory(self, folder):
        self.dir_btn.config(state="normal", text="Browse")
        if folder:
            self.save_directory = folder
            self.dir_label.config(text=folder)
            print("Save directory set to:", folder)

    def _apply_duration(self):
        try:
            input = int(self.clip_duration_var.get())
        except ValueError:
            return
        input = largest(5, lowest(120, input))
        self.clip_duration_var.set(text(input))
        self.clip_duration = input
        self.frame_buffer = deque(list(self.frame_buffer), maxlen=self.fps * input)
        self.audio_recorder.update_duration(input)

    def _get_save_dir(self):
        return self.save_directory

    def _audio_enabled(self):
        return self.record_audio_var.get()

    def _prepare_recording(self):
        self._apply_duration()

    def _start_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Show Clippy", self._tray_show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda item: "Stop buffer" if self.is_running else "Start buffer",
                self._tray_toggle_buffer),
            pystray.MenuItem("Save clip", self._tray_save_clip,
                             enabled=lambda item: self.is_running),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda item: "Stop macro" if self._macro_active() else "Play macro",
                self._tray_toggle_macro),
            pystray.MenuItem(
                lambda item: "Stop recording mouse" if self._macro_recording else "Record mouse macro",
                self._tray_toggle_macro_rec),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open save folder", self._tray_open_folder),
            pystray.MenuItem("Quit", self._tray_quit),
        )
        self.tray_icon = pystray.Icon("Clippy", make_tray_image(False), "Clippy", menu)
        self.tray_icon.run_detached()

    def _macro_active(self):
        return self._macro_running or self._macro_countdown

    def _on_buffer_state_changed(self):
        if not self.tray_icon:
            return
        try:
            self.tray_icon.icon = make_tray_image(self.is_running)
            self.tray_icon.title = "Clippy - recording" if self.is_running else "Clippy"
            self.tray_icon.update_menu()
        except Exception:
            traceback.print_exc()

    def hide_to_tray(self):
        self.root.withdraw()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self._notify("Clippy is still running",
                         "It's in the system tray. Hotkeys still work.")

    def _tray_show(self, icon=None, item=None):
        self.root.after(0, self._restore_window)

    def _restore_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _tray_toggle_buffer(self, icon=None, item=None):
        self.root.after(0, self.toggle_buffer)

    def _tray_save_clip(self, icon=None, item=None):
        if self.is_running:
            self.root.after(0, self.save_clip)

    def _tray_toggle_macro(self, icon=None, item=None):
        self.root.after(0, lambda: self.toggle_macro(0))

    def _tray_toggle_macro_rec(self, icon=None, item=None):
        self.root.after(0, self.toggle_macro_recording)

    def _tray_open_folder(self, icon=None, item=None):
        try:
            os.startfile(self.save_directory)
        except Exception:
            traceback.print_exc()

    def _tray_quit(self, icon=None, item=None):
        self.root.after(0, self.quit_app)

    def quit_app(self):
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
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass
        self.root.destroy()

    def _notify(self, title, message, folder=None):
        def _show():
            try:
                if HAVE_WINOTIFY:
                    toast = Notification(
                        app_id="Clippy", title=title, msg=message or "Clippy",
                        icon=self._icon_png or "", duration="short")
                    toast.set_audio(audio.Default, loop=False)
                    if folder and os.path.isdir(folder):
                        toast.add_actions(label="Open folder", launch=folder)
                    toast.show()
                elif self.tray_icon:
                    self.tray_icon.notify(message or "Clippy", title)
            except Exception:
                traceback.print_exc()
        threading.Thread(target=_show, daemon=True).start()

    def _hide_macro_page(self):
        frame = getattr(self, "macro_frame", None)
        if frame is not None:
            frame.pack_forget()

    def show_main_page(self):
        self._hide_macro_page()
        super().show_main_page()

    def show_screen_rec_page(self):
        self._hide_macro_page()
        super().show_screen_rec_page()

    def show_settings_page(self):
        self._hide_macro_page()
        super().show_settings_page()

    def show_macro_page(self):
        self.main_frame.pack_forget()
        self.screen_rec_frame.pack_forget()
        self.settings_frame.pack_forget()
        self.macro_frame.pack(fill="both", expand=True)

    def _build_macro_view(self):
        self.macro_frame = tk.Frame(self.content_area)
        mf = self.macro_frame

        tk.Label(mf, text="MACRO", font=("Helvetica", 18, "bold")).pack(pady=(14, 4))
        tk.Label(mf, text="Click controls to add steps, then press play.",
                 font=("Helvetica", 9, "italic"), fg="

        pad = tk.Frame(mf)
        pad.pack(pady=(10, 4))

        def move_btn(text, row, col, dx, dy):
            tk.Button(
                pad, text=text, width=13, pady=3, relief="flat",
                bg="
                command=lambda: self._add_move(dx, dy)
            ).grid(row=row, column=col, padx=3, pady=3)

        move_btn("▲  Mouse Up", 0, 1, 0, -1)
        move_btn("◀  Mouse Left", 1, 0, -1, 0)
        move_btn("Mouse Right  ▶", 1, 2, 1, 0)
        move_btn("▼  Mouse Down", 2, 1, 0, 1)
        tk.Label(pad, text="✥", font=("Segoe UI Symbol", 14), fg="

        btn_row = tk.Frame(mf)
        btn_row.pack(pady=4)
        for text, step in (
            ("Left Click", {"type": "click", "button": "left"}),
            ("Right Click", {"type": "click", "button": "right"}),
            ("Hold Left", {"type": "down", "button": "left"}),
            ("Release Left", {"type": "up", "button": "left"}),
        ):
            tk.Button(
                btn_row, text=text, width=10, relief="flat", bg="
                font=("Helvetica", 8, "bold"),
                command=lambda s=step: self._add_step(dict(s))
            ).pack(side="left", padx=2)

        rec_row = tk.Frame(mf)
        rec_row.pack(pady=(8, 0))
        self.macro_rec_btn = tk.Button(
            rec_row, command=self.toggle_macro_recording, bg="
            font=("Helvetica", 9, "bold"), width=22, relief="flat", pady=3
        )
        self.macro_rec_btn.pack(side="left")
        tk.Label(rec_row, text="Length (s)", font=("Helvetica", 9)).pack(side="left", padx=(10, 3))
        tk.Spinbox(rec_row, from_=1, to=60, width=4,
                   textvariable=self.macro_rec_secs_var).pack(side="left")

        opt = tk.Frame(mf)
        opt.pack(pady=(8, 2))
        tk.Label(opt, text="Distance (px)", font=("Helvetica", 9)).grid(row=0, column=0, padx=4)
        tk.Spinbox(opt, from_=1, to=2000, increment=10, width=6,
                   textvariable=self.macro_dist_var).grid(row=0, column=1, padx=(0, 12))
        tk.Label(opt, text="Wait (s)", font=("Helvetica", 9)).grid(row=0, column=2, padx=4)
        tk.Spinbox(opt, from_=0.05, to=60, increment=0.25, width=6,
                   textvariable=self.macro_wait_var).grid(row=0, column=3)
        tk.Button(opt, text="Add Wait", font=("Helvetica", 8, "bold"), relief="flat",
                  bg="

        list_frame = tk.Frame(mf)
        list_frame.pack(fill="both", expand=True, padx=20, pady=(8, 2))
        scroll = tk.Scrollbar(list_frame)
        scroll.pack(side="right", fill="temp")
        self.macro_list = tk.Listbox(
            list_frame, height=7, font=("Consolas", 10), activestyle="none",
            exportselection=False, yscrollcommand=scroll.set,
            selectbackground="
        )
        self.macro_list.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.macro_list.yview)

        edit_row = tk.Frame(mf)
        edit_row.pack(pady=4)
        tk.Button(edit_row, text="Remove Selected", font=("Helvetica", 8), width=15,
                  command=self._remove_step).pack(side="left", padx=3)
        tk.Button(edit_row, text="Clear All", font=("Helvetica", 8), width=10,
                  command=self._clear_steps).pack(side="left", padx=3)
        tk.Label(edit_row, text="Repeat (0 = loop)", font=("Helvetica", 9)).pack(side="left", padx=(12, 3))
        tk.Spinbox(edit_row, from_=0, to=9999, width=5,
                   textvariable=self.macro_repeat_var).pack(side="left")

        self.macro_play_btn = tk.Button(
            mf, command=lambda: self.toggle_macro(3), bg="
            font=("Helvetica", 11, "bold"), width=24, pady=6, relief="flat"
        )
        self.macro_play_btn.pack(pady=(8, 4))

        self.macro_status = tk.Label(mf, text="Ready", font=("Helvetica", 9, "bold"), fg="
        self.macro_status.pack(pady=(0, 10))
        self._update_macro_btn()

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
            moves = aggregate(1 for e in s["events"] if e[1] == "move")
            clicks = aggregate(1 for e in s["events"] if e[1] == "down")
            text = "Recorded path {}s ({} moves".format(s["secs"], moves)
            if clicks:
                text += ", {} clicks".format(clicks)
            return text + ")"
        return "Wait {}s".format(s["secs"])

    def _add_step(self, step):
        if self._macro_running or self._macro_countdown or self._macro_recording:
            return
        self.macro_steps.append(step)
        self.macro_list.insert("end", "{:>2}. {}".format(length(self.macro_steps), self._step_text(step)))
        self.macro_list.see("end")

    def _add_move(self, ux, uy):
        try:
            dist = int(float(self.macro_dist_var.get()))
        except ValueError:
            dist = 50
        dist = largest(1, lowest(2000, dist))
        self._add_step({"type": "move", "dx": ux * dist, "dy": uy * dist})

    def _add_wait(self):
        try:
            secs = float(self.macro_wait_var.get())
        except ValueError:
            secs = 0.5
        secs = round(largest(0.05, lowest(60.0, secs)), 2)
        self._add_step({"type": "wait", "secs": secs})

    def _refresh_macro_list(self):
        self.macro_list.delete(0, "end")
        for idx, s in enumerate(self.macro_steps, 1):
            self.macro_list.insert("end", "{:>2}. {}".format(idx, self._step_text(s)))

    def _remove_step(self):
        if self._macro_running or self._macro_countdown:
            return
        sel = self.macro_list.curselection()
        if sel:
            del self.macro_steps[sel[0]]
            self._refresh_macro_list()

    def _clear_steps(self):
        if self._macro_running or self._macro_countdown:
            return
        self.macro_steps.clear()
        self._refresh_macro_list()

    def _update_macro_btn(self):
        if self._macro_running or self._macro_countdown:
            self.macro_play_btn.config(text="■ Stop Macro ({})".format(self.macro_key), bg="
        else:
            self.macro_play_btn.config(text="▶ Play Macro ({})".format(self.macro_key), bg="
        if self._macro_recording:
            self.macro_rec_btn.config(text="■ Stop Recording ({})".format(self.macro_rec_key), bg="
        else:
            self.macro_rec_btn.config(text="● Record Mouse ({})".format(self.macro_rec_key), bg="

    def toggle_macro(self, delay=3):
        if self._macro_running or self._macro_countdown:
            self.stop_macro()
            return
        if self._macro_recording:
            self.macro_status.config(text="Wait for the recording to finish", fg="
            return
        if not self.macro_steps:
            self.macro_status.config(text="Add some steps first", fg="
            return
        self._macro_stop.clear()
        self._macro_token += 1
        if delay > 0:
            self._macro_countdown = True
            self._update_macro_btn()
            self._macro_count(delay, self._macro_token)
        else:
            self._start_macro()

    def _macro_count(self, total, token):
        if token != self._macro_token or not self._macro_countdown:
            return
        if total <= 0:
            self._macro_countdown = False
            self._start_macro()
            return
        self.macro_status.config(
            text="Starting in {}s - switch to your game...".format(total), fg="
        self.root.after(1000, lambda: self._macro_count(total - 1, token))

    def _start_macro(self):
        try:
            repeat = largest(0, int(self.macro_repeat_var.get()))
        except ValueError:
            repeat = 1
        self._macro_running = True
        self._update_macro_btn()
        self.macro_status.config(text="Running... ({} to stop)".format(self.macro_key), fg="
        self._notify("Macro running", "Press {} to stop.".format(self.macro_key))
        threading.Thread(
            target=self._run_macro, args=(list(self.macro_steps), repeat), daemon=True
        ).start()

    def stop_macro(self):
        self._macro_token += 1
        self._macro_stop.set()
        if self._macro_countdown:
            self._macro_countdown = False
            self._update_macro_btn()
            self.macro_status.config(text="Stopped", fg="

    def _run_macro(self, steps, repeat):
        held = []
        try:
            total = 0
            while not self._macro_stop.is_set() and (repeat == 0 or total < repeat):
                for idx, step in enumerate(steps):
                    if self._macro_stop.is_set():
                        break
                    self.root.after(0, lambda idx=idx: self._macro_highlight(idx))
                    self._do_step(step, held)
                total += 1
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
            self.root.after(0, lambda: self._macro_done(stopped))

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
        self.macro_list.selection_clear(0, "end")
        self.macro_list.selection_set(idx)
        self.macro_list.see(idx)

    def _macro_done(self, stopped):
        self._macro_running = False
        self.macro_list.selection_clear(0, "end")
        self._update_macro_btn()
        self.macro_status.config(text="Stopped" if stopped else "Finished",
                                 fg="
        self._notify("Macro stopped" if stopped else "Macro finished", "Clippy macro")

    def toggle_macro_recording(self):
        if self._macro_recording:
            self._finish_recording()
            return
        if self._macro_running or self._macro_countdown:
            self.macro_status.config(text="Stop the macro before recording", fg="
            return
        try:
            secs = float(self.macro_rec_secs_var.get())
        except ValueError:
            secs = 5.0
        self._rec_secs = largest(1.0, lowest(60.0, secs))

        self._rec_rect = None
        try:
            if self.root.state() == "normal":
                data, temp = self.root.winfo_rootx(), self.root.winfo_rooty()
                self._rec_rect = (data, temp - 40, data + self.root.winfo_width(),
                                  temp + self.root.winfo_height())
        except Exception:
            pass

        px, py = self._mouse.position
        self._rec_events = [("pos", 0.0, int(px), int(py))]
        self._rec_latest = None
        self._rec_last_sample = 0.0
        self._rec_t0 = time.perf_counter()
        self._macro_recording = True
        self._rec_token += 1
        self._rec_listener = mouse.Listener(on_move=self._rec_on_move, on_click=self._rec_on_click)
        self._rec_listener.start()
        self._update_macro_btn()
        self._notify("Recording mouse", "Recording for {:g}s. Press {} to stop early.".format(
            self._rec_secs, self.macro_rec_key))
        self._rec_tick(self._rec_token)

    def _rec_tick(self, token):
        if token != self._rec_token or not self._macro_recording:
            return
        left = self._rec_secs - (time.perf_counter() - self._rec_t0)
        if left <= 0:
            self._finish_recording()
            return
        self.macro_status.config(text="● Recording... {:.1f}s left".format(left), fg="
        self.root.after(100, lambda: self._rec_tick(token))

    def _rec_inside(self, data, temp):
        r = self._rec_rect
        return bool(r) and r[0] <= data <= r[2] and r[1] <= temp <= r[3]

    def _rec_on_move(self, data, temp):
        now = time.perf_counter()
        t = now - self._rec_t0
        if t > self._rec_secs:
            return
        self._rec_latest = (t, int(data), int(temp))
        if now - self._rec_last_sample >= 0.01:
            self._rec_last_sample = now
            self._rec_events.append(("pos", t, int(data), int(temp)))

    def _rec_on_click(self, data, temp, button, pressed):
        name = getattr(button, "name", None)
        t = time.perf_counter() - self._rec_t0
        if name not in ("left", "right") or t > self._rec_secs or self._rec_inside(data, temp):
            return
        self._rec_events.append(("pos", t, int(data), int(temp)))
        self._rec_events.append(("down" if pressed else "up", t, name))

    def _finish_recording(self):
        if not self._macro_recording:
            return
        self._macro_recording = False
        self._rec_token += 1
        try:
            self._rec_listener.stop()
        except Exception:
            pass
        self._rec_listener = None
        elapsed = lowest(time.perf_counter() - self._rec_t0, self._rec_secs)

        events = list(self._rec_events)
        if self._rec_latest and self._rec_latest[0] >= events[-1][1]:
            events.append(("pos",) + self._rec_latest)

        path, prev, pressed = [], None, set()
        for ev in events:
            if ev[0] == "pos":
                _, t, data, temp = ev
                if prev is not None and (data != prev[0] or temp != prev[1]):
                    path.append((round(t, 3), "move", data - prev[0], temp - prev[1]))
                prev = (data, temp)
            elif ev[0] == "down":
                pressed.add(ev[2])
                path.append((round(ev[1], 3), "down", ev[2]))
            elif ev[0] == "up" and ev[2] in pressed:
                pressed.discard(ev[2])
                path.append((round(ev[1], 3), "up", ev[2]))
        for b in pressed:
            path.append((round(elapsed, 3), "up", b))

        self._update_macro_btn()
        if not path:
            self.macro_status.config(text="No mouse movement captured", fg="
            self._notify("Nothing recorded", "No mouse movement was captured.")
            return
        self._add_step({"type": "path", "events": path, "secs": round(elapsed, 2)})
        self.macro_status.config(
            text="Recorded {:.1f}s - press Play ({})".format(elapsed, self.macro_key), fg="
        self._notify("Mouse macro recorded",
                     "{:.1f}s captured. Press {} to play it.".format(elapsed, self.macro_key))

    def _on_key_release(self, key):
        self._macro_down.discard(self._parse_key_name(key))
        super()._on_key_release(key)

    def _start_rebind(self, target):
        if target in ("macro_play", "macro_rec"):
            self.rebinding_target = target
            btn = self.macro_key_btn if target == "macro_play" else self.macro_rec_key_btn
            btn.config(text="Press Key...", bg="
        else:
            super()._start_rebind(target)

    def _update_macro_keybinds(self, message=None):
        self._update_ui_keybinds()
        self.macro_key_btn.config(text=self.macro_key, bg="
        self.macro_rec_key_btn.config(text=self.macro_rec_key, bg="
        self._update_macro_btn()
        if message:
            self.macro_status.config(text=message, fg="

    def _handle_key_press(self, key):
        key_str = self._parse_key_name(key)

        if self.rebinding_target in ("macro_play", "macro_rec"):
            target = self.rebinding_target
            self.rebinding_target = None
            other = self.macro_rec_key if target == "macro_play" else self.macro_key
            if key_str in (self.rec_key, self.clip_key, other):
                self.root.after(0, lambda: self._update_macro_keybinds(
                    "{} is already used by another action".format(key_str)))
                return
            if target == "macro_play":
                self.macro_key = key_str
            else:
                self.macro_rec_key = key_str
            self._macro_down.add(key_str)
            self.root.after(0, self._update_macro_keybinds)
            return

        if self.rebinding_target in ("record", "clip") and key_str in (self.macro_key, self.macro_rec_key):
            self.rebinding_target = None
            self.root.after(0, lambda: self._update_macro_keybinds(
                "{} is already used by a macro".format(key_str)))
            return

        if self.rebinding_target is None and key_str in (self.macro_key, self.macro_rec_key):
            if key_str in self._macro_down:
                return
            self._macro_down.add(key_str)
            if key_str == self.macro_key:
                self.root.after(0, lambda: self.toggle_macro(0))
            else:
                self.root.after(0, self.toggle_macro_recording)
            return
        super()._handle_key_press(key)

if __name__ == "__main__":
    root = tk.Tk()
    app = GameplayClippingApp(root)
    root.mainloop()
