# Imports
import os
import time
import threading
import subprocess
from collections import deque
import tkinter as tk
import cv2
import numpy as np
import mss
import soundcard as sc
from scipy.io import wavfile
from pynput import keyboard

try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


class GameplayClipperApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Clippy")
        self.root.geometry("480x250")
        self.root.resizable(False, False)

        self.fps = 30
        self.clip_duration = 15
        self.frame_buffer = deque(maxlen=self.fps * self.clip_duration)
        self.is_running = False
        self.capture_thread = None

        self.rec_key = "F1"
        self.clip_key = "F2"
        self.rebinding_target = None

        self.audio_recorder = AudioRecorder(duration_sec=self.clip_duration)

        self.sidebar = tk.Frame(root, bg="#1e1e2e", width=60)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.content_area = tk.Frame(root)
        self.content_area.pack(side="right", fill="both", expand=True)

        self.nav_controller_btn = tk.Button(
            self.sidebar,
            text="🎮",
            font=("Segoe UI Emoji", 16),
            bg="#1e1e2e",
            fg="white",
            activebackground="#2a2a3d",
            activeforeground="white",
            bd=0,
            cursor="hand2",
            command=self.show_main_page
        )
        self.nav_controller_btn.pack(side="top", pady=15, anchor="nw", padx=12)

        self.nav_settings_btn = tk.Button(
            self.sidebar,
            text="⚙️",
            font=("Segoe UI Emoji", 16),
            bg="#1e1e2e",
            fg="white",
            activebackground="#2a2a3d",
            activeforeground="white",
            bd=0,
            cursor="hand2",
            command=self.show_settings_page
        )
        self.nav_settings_btn.pack(side="bottom", pady=15, anchor="se", padx=12)

        self._build_main_view()
        self._build_settings_view()

        self.show_main_page()

        self.hotkey_listener = keyboard.Listener(on_press=self._on_key_press)
        self.hotkey_listener.daemon = True
        self.hotkey_listener.start()

    def show_main_page(self):
        self.settings_frame.pack_forget()
        self.main_frame.pack(fill="both", expand=True)

    def show_settings_page(self):
        self.main_frame.pack_forget()
        self.settings_frame.pack(fill="both", expand=True)

    def _build_main_view(self):
        self.main_frame = tk.Frame(self.content_area)

        # Title
        self.title_label = tk.Label(self.main_frame, text="🎮 Clippy", font=("Helvetica", 18, "bold"))
        self.title_label.pack(pady=(20, 10))

        # Record / Stop Button
        self.toggle_btn = tk.Button(
            self.main_frame,
            text=f"Record ({self.rec_key})",
            command=self.toggle_buffer,
            bg="#2e7d32",
            fg="white",
            font=("Helvetica", 11, "bold"),
            width=22,
            pady=5,
            relief="flat"
        )
        self.toggle_btn.pack(pady=8)

        self.clip_btn = tk.Button(
            self.main_frame,
            text=f"🎬 SAVE CLIP ({self.clip_key})",
            command=self.save_clip,
            bg="#1565c0",
            fg="white",
            font=("Helvetica", 11, "bold"),
            width=22,
            pady=8,
            relief="flat",
            state="disabled"
        )
        self.clip_btn.pack(pady=8)

        self.info_label = tk.Label(
            self.main_frame,
            text="Keep this running in the background while playing.",
            font=("Helvetica", 9, "italic"),
            fg="#616161"
        )
        self.info_label.pack(side="bottom", pady=12)

    def _build_settings_view(self):
        self.settings_frame = tk.Frame(self.content_area)

        title = tk.Label(self.settings_frame, text="⚙️ Settings", font=("Helvetica", 16, "bold"))
        title.pack(pady=(15, 5))

        keybinds_header = tk.Label(self.settings_frame, text="Keybinds", font=("Helvetica", 12, "bold", "underline"))
        keybinds_header.pack(pady=(5, 15))

        rec_row = tk.Frame(self.settings_frame)
        rec_row.pack(fill="x", padx=40, pady=5)

        tk.Label(rec_row, text="Start Recording:", font=("Helvetica", 10), width=16, anchor="w").pack(side="left")
        self.rec_key_btn = tk.Button(
            rec_row,
            text=self.rec_key,
            font=("Helvetica", 10, "bold"),
            width=12,
            command=lambda: self._start_rebind("record")
        )
        self.rec_key_btn.pack(side="right")

        clip_row = tk.Frame(self.settings_frame)
        clip_row.pack(fill="x", padx=40, pady=5)

        tk.Label(clip_row, text="Clip:", font=("Helvetica", 10), width=16, anchor="w").pack(side="left")
        self.clip_key_btn = tk.Button(
            clip_row,
            text=self.clip_key,
            font=("Helvetica", 10, "bold"),
            width=12,
            command=lambda: self._start_rebind("clip")
        )
        self.clip_key_btn.pack(side="right")

    def _start_rebind(self, target):
        self.rebinding_target = target
        if target == "record":
            self.rec_key_btn.config(text="Press Key...", bg="#ffb300")
        elif target == "clip":
            self.clip_key_btn.config(text="Press Key...", bg="#ffb300")

    def _on_key_press(self, key):
        key_str = self._parse_key_name(key)

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
            self.root.after(0, self.toggle_buffer)
        elif key_str == self.clip_key and self.is_running:
            self.root.after(0, self.save_clip)

    def _parse_key_name(self, key):
        if hasattr(key, 'name') and key.name:
            return key.name.upper()
        elif hasattr(key, 'char') and key.char:
            return key.char.upper()
        return str(key).replace("Key.", "").upper()

    def _update_ui_keybinds(self):
        self.rec_key_btn.config(text=self.rec_key, bg="#f0f0f0")
        self.clip_key_btn.config(text=self.clip_key, bg="#f0f0f0")

        current_state_text = "Stop" if self.is_running else "Record"
        self.toggle_btn.config(text=f"{current_state_text} ({self.rec_key})")
        self.clip_btn.config(text=f"🎬 SAVE CLIP ({self.clip_key})")

    def toggle_buffer(self):
        if not self.is_running:
            self.is_running = True
            self.frame_buffer.clear()
            self.toggle_btn.config(text=f"Stop ({self.rec_key})", bg="#c62828")
            self.clip_btn.config(state="normal")

            self.audio_recorder.start()
            self.capture_thread = threading.Thread(target=self._record_loop, daemon=True)
            self.capture_thread.start()
        else:
            self.is_running = False
            self.audio_recorder.stop()
            self.toggle_btn.config(text=f"Record ({self.rec_key})", bg="#2e7d32")
            self.clip_btn.config(state="disabled")

    def _record_loop(self):
        """Continuously captures primary screen into RAM buffer."""
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
                except Exception as e:
                    print(f"Screen capture error: {e}")

                elapsed = time.time() - start_time
                sleep_time = frame_delay - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

    def save_clip(self):
        if not self.frame_buffer:
            return

        frames = list(self.frame_buffer)
        timestamp = time.strftime("%Y%m%d_%H%M%S")

        def _write_files():
            temp_video = f"temp_video_{timestamp}.mp4"
            temp_audio = f"temp_audio_{timestamp}.wav"
            final_output = f"gameplay_clip_{timestamp}.mp4"

            has_audio = self.audio_recorder.save_wav(temp_audio)

            height, width, _ = frames[0].shape
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            out = cv2.VideoWriter(temp_video, fourcc, float(self.fps), (width, height))

            if out.isOpened():
                for frame in frames:
                    if frame.shape[:2] != (height, width):
                        frame = cv2.resize(frame, (width, height))
                    out.write(frame)
                out.release()

            if has_audio and os.path.exists(temp_video):
                cmd = [
                    "ffmpeg", "-y",
                    "-i", temp_video,
                    "-i", temp_audio,
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-shortest",
                    final_output
                ]
                try:
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                except Exception as e:
                    print(f"FFmpeg merge skipped/failed: {e}")
                    if os.path.exists(temp_video):
                        os.rename(temp_video, final_output)

                if os.path.exists(temp_video) and os.path.exists(final_output):
                    os.remove(temp_video)
                if os.path.exists(temp_audio):
                    os.remove(temp_audio)
            elif os.path.exists(temp_video):
                os.rename(temp_video, final_output)

        threading.Thread(target=_write_files, daemon=True).start()


class AudioRecorder:
    def __init__(self, duration_sec=15, sample_rate=44100):
        self.sample_rate = sample_rate
        self.duration_sec = duration_sec
        self.buffer = deque(maxlen=int(sample_rate * duration_sec / 1024))
        self.is_recording = False
        self._thread = None

    def start(self):
        """Starts recording system audio in a background thread."""
        self.buffer.clear()
        self.is_recording = True
        self._thread = threading.Thread(target=self._record_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stops the audio recorder."""
        self.is_recording = False

    def _record_loop(self):
        """Gets Windows desktop speaker output"""
        try:
            default_speaker = sc.default_speaker()
            loopback_mic = sc.get_microphone(id=str(default_speaker.name), include_loopback=True)
            with loopback_mic.recorder(samplerate=self.sample_rate, channels=2) as mic:
                while self.is_recording:
                    data = mic.record(numframes=1024)
                    self.buffer.append(data)
        except Exception as e:
            print(f"Audio capture error: {e}")

    def save_wav(self, output_filepath):
        """Audio buffer to a WAV file."""
        if not self.buffer:
            return False

        audio_data = np.concatenate(list(self.buffer), axis=0)
        audio_int16 = (audio_data * 32767).astype(np.int16)
        wavfile.write(output_filepath, self.sample_rate, audio_int16)
        return True

# Run Script
if __name__ == "__main__":
    root = tk.Tk()
    app = GameplayClipperApp(root)
    root.mainloop()