"""Windows YouTube audio downloader and verified USB copier."""
import ctypes
from collections import deque
import hashlib
import os
from pathlib import Path
import queue
import signal
import shutil
import subprocess
import sys
import threading
import tempfile
import time
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse
import windows_usb
import customtkinter as ctk


def bundled_tools():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'tools'


def downloader_command():
    if getattr(sys, 'frozen', False):
        executable = bundled_tools() / 'yt-dlp.exe'
        if not executable.is_file():
            raise FileNotFoundError('Bundled downloader is missing. Extract the complete ZIP again.')
        return [str(executable)]
    return [sys.executable, '-m', 'yt_dlp']


def validate_urls(text):
    urls = text.split()
    if not urls:
        raise ValueError('Paste at least one YouTube link.')
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname not in (
            'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be'
        ):
            raise ValueError('Only HTTPS YouTube links are supported: ' + url)
    return urls


class Cancelled(Exception):
    """The user requested a safe stop."""


def check_cancel(cancel=None):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def digest(path, cancel=None):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            check_cancel(cancel)
            result.update(chunk)
    check_cancel(cancel)
    return result.digest()


class TransferCleanupError(OSError):
    """A USB error prevented confirming that transfer cleanup finished."""


def sync_directory(folder):
    # Windows file writes use fsync; publication additionally requests write-through.
    # Opening a volume for flushing on Windows would require administrator access.
    if sys.platform != 'win32':
        descriptor = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def publish_verified(temporary, target):
    """Publish a verified file without replacing any existing destination."""
    if sys.platform == 'win32':
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        move = kernel32.MoveFileExW
        move.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        move.restype = wintypes.BOOL
        # Same-directory move; deliberately omit MOVEFILE_REPLACE_EXISTING.
        if not move(str(temporary), str(target), 0x8):  # MOVEFILE_WRITE_THROUGH
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        # link is atomic and fails if target already exists, unlike POSIX rename.
        os.link(temporary, target)
        temporary.unlink()
    sync_directory(target.parent)


def copy_verified(source, folder, progress=None, cancel=None):
    def report(stage, percent=None):
        if progress:
            progress(stage, percent)

    check_cancel(cancel)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / source.name
    if target.exists():
        report('Checking existing file')
        if digest(source, cancel) == digest(target, cancel):
            report('Already present', 100)
            return 'Already present: ' + source.name
        raise FileExistsError('Different file already exists: ' + str(target))
    if shutil.disk_usage(folder).free < source.stat().st_size + 1024 * 1024:
        raise OSError('Not enough free space on the selected drive.')
    # Only an operation-owned temporary file is ever written or removed.
    descriptor, temporary_name = tempfile.mkstemp(prefix='.youtubetousb-', suffix='.partial', dir=folder)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            try:
                report('Copying to USB', 0)
                with source.open('rb') as incoming:
                    total = source.stat().st_size
                    copied = 0
                    previous = -1
                    for chunk in iter(lambda: incoming.read(1024 * 1024), b''):
                        check_cancel(cancel)
                        output.write(chunk)
                        copied += len(chunk)
                        percent = min(99, int(copied * 100 / total)) if total else 99
                        if percent != previous:
                            report('Copying to USB', percent)
                            previous = percent
            finally:
                # Finish the current OS write and flush before closing, even on Cancel.
                output.flush()
                os.fsync(output.fileno())
        report('Verifying USB copy')
        if digest(source, cancel) != digest(temporary, cancel):
            raise OSError('Copy verification failed: ' + source.name)
        check_cancel(cancel)
        publish_verified(temporary, target)
    finally:
        # Never delete the final MP3 or touch someone else's temporary file.
        try:
            temporary.unlink(missing_ok=True)
            if not folder.is_dir():
                raise OSError('USB destination is no longer available')
            sync_directory(folder)
        except OSError as error:
            raise TransferCleanupError('USB cleanup could not finish. Keep the drive connected. Temporary file: ' + str(temporary)) from error
    report('Copy verified', 100)
    return 'Copied and verified: ' + source.name


def stop_process_tree(process):
    if process.poll() is not None:
        return
    if sys.platform == 'win32':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=15, check=True)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


QUIET_NOTICE_SECONDS = 20


def preparation_status(line):
    text = line.lower()
    if 'retrying' in text:
        return 'Connection slow. Retrying…'
    if '[jsc' in text or 'challenge' in text:
        return 'Checking YouTube playback…'
    if 'downloading webpage' in text or 'extracting url' in text:
        return 'Reading YouTube link…'
    if 'downloading' in text and ('player' in text or 'api json' in text):
        return 'Checking available audio…'
    if 'playlist' in text:
        return 'Reading playlist…'
    if '[info]' in text and 'format' in text:
        return 'Starting audio download…'
    return None


def download_with_progress(command, progress, cancel=None, detail=None):
    """Stream progress without buffering the entire downloader output."""
    recent = deque(maxlen=40)
    check_cancel(cancel)
    done = threading.Event()
    stop_errors = []
    last_output = [time.monotonic()]
    launched_at = last_output[0]
    progress('Starting downloader…')
    if detail:
        detail('Starting downloader; waiting for its first response…')
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding='utf-8', errors='replace',
                          start_new_session=sys.platform != 'win32',
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
        def watch_cancel():
            notified_at = None
            # A separate watcher can stop FFmpeg even while it produces no output.
            while not done.wait(0.1):
                if cancel is not None and cancel.is_set():
                    try:
                        stop_process_tree(process)
                    except (OSError, subprocess.SubprocessError) as error:
                        if process.poll() is None:
                            stop_errors.append(str(error))
                            progress('Unable to stop yet. Please wait for the current song to finish.')
                    return
                if time.monotonic() - last_output[0] >= QUIET_NOTICE_SECONDS and notified_at != last_output[0]:
                    notified_at = last_output[0]
                    progress('Still waiting… You can cancel.')
                    if detail:
                        detail(f'No new downloader output for {QUIET_NOTICE_SECONDS} seconds. Still waiting; Cancel remains available.')

        watcher = threading.Thread(target=watch_cancel, daemon=True)
        watcher.start()
        first_output = True
        try:
            for line in process.stdout:
                last_output[0] = time.monotonic()
                if first_output:
                    first_output = False
                    if detail:
                        detail(f'Downloader responded after {time.monotonic() - launched_at:.1f} seconds.')
                if not line.startswith(('USB_PROGRESS:', 'USB_CONVERT:')):
                    text = line.rstrip()
                    if text:
                        recent.append(text)
                        if detail:
                            detail(text)
                if line.startswith('USB_PROGRESS:'):
                    parts = line.strip().split(':', 1)[1].split('|')
                    try:
                        downloaded = float(parts[0])
                        total = next(float(value) for value in parts[1:] if value not in ('NA', 'None', '') and float(value) > 0)
                        progress('Downloading this song', max(0, min(100, downloaded * 100 / total)))
                    except (ValueError, StopIteration, IndexError):
                        progress('Downloading this song')
                elif line.startswith('USB_CONVERT:') or line.startswith('[ExtractAudio]'):
                    progress('Making the MP3 file')
                else:
                    stage = preparation_status(line)
                    if stage:
                        progress(stage)
            result = subprocess.CompletedProcess(command, process.wait(), '', '\n'.join(recent))
        finally:
            done.set()
            watcher.join()
        if stop_errors:
            raise RuntimeError('Could not stop immediately; the current download has now ended.')
        check_cancel(cancel)
        return result


def removable_drives():
    if sys.platform != 'win32':
        return []
    return [f'{letter}:\\' for letter in 'DEFGHIJKLMNOPQRSTUVWXYZ'
            if ctypes.windll.kernel32.GetDriveTypeW(f'{letter}:\\') == 2]


class USBSelector(ctk.CTkFrame):
    """One compact device card; secondary choices stay in its dropdown."""
    def __init__(self, master, on_change, font_family):
        super().__init__(master, fg_color='white', border_color='#d4e2f6', border_width=1, corner_radius=18, height=94)
        self._value = ''
        self._choices = []
        self._state = 'normal'
        self.on_change = on_change
        self.grid_columnconfigure(1, weight=1)
        icon_frame = ctk.CTkFrame(self, width=56, height=56, corner_radius=14, fg_color='#e7f0ff')
        icon_frame.grid(row=0, column=0, rowspan=2, padx=(18, 16), pady=18)
        icon_frame.grid_propagate(False)
        icon = tk.Canvas(icon_frame, width=32, height=40, bg='#e7f0ff', highlightthickness=0)
        icon.place(relx=.5, rely=.5, anchor='center')
        icon.create_line(16, 31, 16, 8, fill='#2563eb', width=3)
        icon.create_polygon(16, 2, 10, 10, 22, 10, fill='#2563eb')
        icon.create_oval(11, 29, 21, 39, fill='#2563eb', outline='')
        icon.create_line(16, 25, 6, 19, 6, 12, fill='#2563eb', width=3)
        icon.create_oval(2, 8, 10, 16, fill='#2563eb', outline='')
        icon.create_line(16, 21, 26, 15, 26, 10, fill='#2563eb', width=3)
        icon.create_rectangle(22, 5, 30, 13, fill='#2563eb', outline='')
        ctk.CTkLabel(self, text='USB drive', font=(font_family, 18, 'bold'), text_color='#182338', anchor='w').grid(row=0, column=1, sticky='sw', pady=(16, 0))
        self.path_label = ctk.CTkLabel(self, text='No USB connected', font=(font_family, 16), text_color='#64748b', anchor='w')
        self.path_label.grid(row=1, column=1, sticky='nw', pady=(0, 16))
        self.badge = ctk.CTkLabel(self, text='Not connected', font=(font_family, 13), corner_radius=14, fg_color='#f1f5f9', text_color='#64748b', padx=12, height=30)
        self.badge.grid(row=0, column=2, rowspan=2, padx=(8, 6))
        self.menu_button = ctk.CTkButton(self, text='⌄', width=42, height=42, corner_radius=12, fg_color='transparent', hover_color='#edf4ff', text_color='#526177', font=(font_family, 24), command=self.open_menu)
        self.menu_button.grid(row=0, column=3, rowspan=2, padx=(0, 14))
        self.menu = tk.Menu(self, tearoff=False, font=(font_family, 14), bg='white', fg='#182338', activebackground='#e7f0ff', activeforeground='#1d4ed8', borderwidth=0)

    def open_menu(self):
        if self._state == 'disabled':
            return
        self.on_change(refresh=True)
        self.menu.delete(0, 'end')
        for drive in self._choices:
            self.menu.add_command(label=drive, command=lambda value=drive: self.choose(value))
        if self._choices:
            self.menu.add_separator()
        self.menu.add_command(label='Choose folder…', command=lambda: self.on_change(browse=True))
        self.menu.add_command(label='Refresh', command=lambda: self.on_change(refresh=True))
        try:
            self.menu.tk_popup(self.winfo_rootx() + self.winfo_width() - 210, self.winfo_rooty() + self.winfo_height() - 10)
        finally:
            self.menu.grab_release()

    def choose(self, value):
        self.set(value)
        self.on_change()

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        self.path_label.configure(text=value or 'No USB connected')
        self.badge.configure(text='● Connected' if value else 'Not connected', fg_color='#e9f7ee' if value else '#f1f5f9', text_color='#237544' if value else '#64748b')

    def cget(self, key):
        if key == 'values':
            return self._choices
        if key == 'state':
            return self._state
        return super().cget(key)

    def configure(self, **kwargs):
        if 'values' in kwargs:
            self._choices = list(kwargs.pop('values'))
        if 'state' in kwargs:
            self._state = kwargs.pop('state')
            self.menu_button.configure(state=self._state)
        return super().configure(**kwargs)


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.busy = False
        self.close_when_done = False
        self.transfer_cleanup_failed = False
        self.ejecting = False
        self.usb_device = None
        self.usb_identity_error = None
        self.last_destination = None
        self.remove_when_stopped = False
        self.exit_after_removal = True
        self.download_complete = False
        interface_font = 'Segoe UI' if sys.platform == 'win32' else 'Helvetica'
        ctk.set_appearance_mode('light')
        root.title('YouTube to MP3')
        root.geometry('800x660')
        root.minsize(660, 580)
        root.configure(bg='#eff5ff')
        self.page = ctk.CTkScrollableFrame(root, fg_color='#eff5ff', corner_radius=0, scrollbar_button_color='#d9e4f4', scrollbar_button_hover_color='#b8cceb')
        self.page.pack(fill='both', expand=True)
        frame = ctk.CTkFrame(self.page, fg_color='transparent')
        frame.pack(fill='x', expand=True, padx=26, pady=24)
        ctk.CTkLabel(frame, text='YouTube links', font=(interface_font, 23, 'bold'), text_color='#17253b', anchor='w').pack(fill='x', pady=(0, 14))
        self.urls = ctk.CTkTextbox(frame, height=180, corner_radius=18, border_width=1, border_color='#ccdcf3',
                                  fg_color='white', text_color='#17253b', font=(interface_font, 17),
                                  wrap='word', spacing1=4, spacing3=4, undo=True)
        self.urls.pack(fill='x')
        self.urls.bind('<<Paste>>', self.paste_links)
        self.drive = USBSelector(frame, self.drive_changed, interface_font)
        self.drive.pack(fill='x', pady=(22, 0))
        progress_row = ctk.CTkFrame(frame, fg_color='transparent')
        progress_row.pack(fill='x', pady=(30, 12))
        self.progress_label = ctk.CTkLabel(progress_row, text='Ready', font=(interface_font, 18, 'bold'), text_color='#17253b', wraplength=500, anchor='w')
        self.progress_label.pack(side='left', fill='x', expand=True)
        self.percent_label = ctk.CTkLabel(progress_row, text='0%', font=(interface_font, 20), text_color='#526177')
        self.percent_label.pack(side='right', padx=(12, 0))
        self.progress_bar = ctk.CTkProgressBar(frame, height=10, corner_radius=5, fg_color='#d8e3f2', progress_color='#2563eb')
        self.progress_bar.set(0)
        self.progress_bar.pack(fill='x', pady=(0, 28))
        self.action_frame = ctk.CTkFrame(frame, fg_color='transparent')
        self.action_frame.pack(fill='x')
        self.button = ctk.CTkButton(self.action_frame, text='▶  START', height=60, corner_radius=16, font=(interface_font, 20, 'bold'), fg_color='#2563eb', hover_color='#1d4ed8', text_color='white', text_color_disabled='#7f93ae', command=self.start)
        self.button.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.cancel_button = ctk.CTkButton(self.action_frame, text='×  CANCEL', height=60, corner_radius=16, border_width=1, border_color='#c6d6ec', font=(interface_font, 20, 'bold'), fg_color='white', hover_color='#e8f0fc', text_color='#526177', text_color_disabled='#9aa9bd', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='left', fill='x', expand=True, padx=(8, 0))
        self.done_button = ctk.CTkButton(frame, text='✓  DONE', height=60, corner_radius=16, font=(interface_font, 20, 'bold'), fg_color='#2563eb', hover_color='#1d4ed8', text_color='white', command=self.close)
        self.details_visible = tk.BooleanVar(value=False)
        self.details_check = ctk.CTkButton(frame, text='⌄  Details', font=(interface_font, 14), text_color='#64748b', fg_color='transparent', hover_color='#e2ecfb', width=100, height=30, anchor='w', command=self.toggle_details)
        self.details_check.pack(anchor='w', pady=(16, 0))
        self.details_frame = ctk.CTkFrame(frame, fg_color='transparent')
        self.log = ctk.CTkTextbox(self.details_frame, height=110, state='disabled', wrap='word', corner_radius=12, font=(interface_font, 13), fg_color='white', text_color='#526177')
        self.log.pack(fill='both', expand=True)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<Alt-s>', lambda event: self.start() if not self.busy else None)
        root.bind('<Escape>', lambda event: self.cancel())
        self.refresh_drives()
        root.after(100, self.poll)
        root.after(1500, self.monitor_drives)
        self.urls.focus_set()

    def toggle_details(self):
        visible = not self.details_visible.get()
        self.details_visible.set(visible)
        self.details_check.configure(text=('⌃' if visible else '⌄') + '  Details')
        if visible:
            self.details_frame.pack(fill='both', expand=True, pady=(8, 0))
        else:
            self.details_frame.pack_forget()

    def drive_changed(self, refresh=False, browse=False):
        if refresh:
            self.refresh_drives()
        elif browse:
            self.browse()
        self.update_start_state()

    def update_start_state(self):
        if hasattr(self, 'button'):
            enabled = bool(self.drive.get()) and not self.busy and not self.ejecting
            self.button.configure(state='normal' if enabled else 'disabled', fg_color='#2563eb' if enabled else '#dce5f2')
            if not self.busy and not self.ejecting and not self.download_complete:
                if not self.drive.get():
                    self.show_progress('No USB connected')
                elif self.progress_label.cget('text') == 'No USB connected':
                    self.show_progress('Ready', 0)

    def monitor_drives(self):
        if not self.busy and not self.ejecting:
            self.refresh_drives()
        self.root.after(1500, self.monitor_drives)

    def paste_links(self, event=None):
        if self.busy or self.ejecting:
            return 'break'
        try:
            text = self.root.clipboard_get().strip()
            if text:
                try:
                    self.urls.delete('sel.first', 'sel.last')
                except tk.TclError:
                    pass
                if self.urls.get('insert linestart', 'insert').strip():
                    self.urls.insert('insert', '\n')
                self.urls.insert('insert', '\n'.join(text.split()) + '\n')
                self.urls.see('insert')
        except tk.TclError:
            self.show_progress('Copy a YouTube link first.')
        return 'break'

    def show_done(self, complete):
        if complete:
            self.action_frame.pack_forget()
            self.done_button.pack(fill='x', before=self.details_check, pady=(0, 4))
        else:
            self.done_button.pack_forget()
            self.action_frame.pack(fill='x', before=self.details_check)

    def set_controls(self, busy):
        self.button.configure(state='disabled' if busy else 'normal')
        self.cancel_button.configure(state='normal' if busy else 'disabled')
        self.urls.configure(state='disabled' if busy else 'normal')
        self.drive.configure(state='disabled' if busy else 'normal')
        self.update_start_state()

    def cancel(self):
        if self.busy and not self.cancel_event.is_set():
            self.remove_when_stopped = True
            self.cancel_event.set()
            self.cancel_button.configure(state='disabled')
            self.show_progress('Stopping… Please keep the USB plugged in.')

    def close(self):
        if self.ejecting:
            return
        if self.busy:
            self.close_when_done = True
            self.cancel()
        else:
            self.prepare_exit()

    def prepare_exit(self, close_app=True):
        if self.busy or self.ejecting:
            return
        destination = self.last_destination or self.drive.get()
        if not destination:
            self.root.destroy()
            return
        self.exit_after_removal = close_app
        self.ejecting = True
        self.set_controls(True)
        self.cancel_button.configure(state='disabled')
        self.done_button.configure(state='disabled')
        self.show_progress('Preparing USB… Please wait.')
        self.progress_bar.configure(mode='indeterminate')
        self.progress_bar.start()
        threading.Thread(target=self.prepare_usb, args=(destination,), daemon=False).start()

    def prepare_usb(self, destination):
        try:
            if self.last_destination is not None:
                if self.usb_identity_error:
                    raise self.usb_identity_error
                device = self.usb_device
                if device is None:
                    raise OSError('The original drive could not be identified. Use Windows safe removal before unplugging.')
            else:
                device = windows_usb.describe_usb(destination)
            windows_usb.eject_usb(device)
        except Exception as error:
            self.events.put({'usb_error': str(error)})
        else:
            self.events.put({'usb_ready': True})

    def browse(self):
        folder = filedialog.askdirectory(title='Select the flash drive or a folder on it')
        if folder:
            self.drive.set(folder)
            self.update_start_state()

    def refresh_drives(self):
        drives = removable_drives()
        current = self.drive.get()
        previous = self.drive.cget('values')
        self.drive.configure(values=drives)
        if current and current not in drives and not Path(current).is_dir():
            current = ''
        if not current or (current in previous and current not in drives):
            self.drive.set(drives[0] if len(drives) == 1 else '')
        self.update_start_state()

    def start(self):
        if self.busy or self.ejecting:
            return
        try:
            urls = validate_urls(self.urls.get('1.0', 'end'))
            # Also detect drives plugged in after the app opened.
            self.refresh_drives()
            destination = Path(self.drive.get())
            if not self.drive.get() or not destination.is_dir():
                raise ValueError('Select a connected flash drive first.')
            if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
                raise ValueError('FFmpeg is missing. Extract the complete portable ZIP again, or run Setup.cmd for source use.')
            if not shutil.which('node'):
                raise ValueError('Node.js is missing. Extract the complete portable ZIP again, or run Setup.cmd for source use.')
            downloader_command()
            if not getattr(sys, 'frozen', False):
                import yt_dlp  # Check before starting the worker.
        except (ValueError, ImportError, FileNotFoundError) as error:
            messagebox.showerror('Cannot start', str(error))
            return
        self.busy = True
        self.download_complete = False
        self.remove_when_stopped = False
        self.close_when_done = False
        self.show_done(False)
        self.cancel_event.clear()
        self.transfer_cleanup_failed = False
        self.set_controls(True)
        self.show_progress('Preparing download')
        threading.Thread(target=self.run, args=(urls, destination), daemon=False).start()

    def run(self, urls, destination):
        import tempfile
        failures = 0
        cache = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'YouTubeToUSB' / 'Downloads'
        try:
            self.last_destination = destination
            self.usb_device = None
            self.usb_identity_error = None
            self.report_progress('Checking USB…')
            self.log_detail('Checking USB identity for safe removal…')
            usb_check_started = time.monotonic()
            try:
                # Remember the actual device before copying, not just its drive letter.
                self.usb_device = windows_usb.describe_usb(destination)
            except Exception as error:
                self.usb_identity_error = error
                self.events.put('Automatic safe removal unavailable: ' + str(error))
            self.log_detail(f'USB check finished in {time.monotonic() - usb_check_started:.1f} seconds.')
            check_cancel(self.cancel_event)
            cache.mkdir(parents=True, exist_ok=True)
            for link_index, url in enumerate(urls, 1):
                try:
                    check_cancel(self.cancel_event)
                    self.report_progress(f'Preparing link {link_index} of {len(urls)}')
                    self.events.put('Downloading: ' + url)
                    with tempfile.TemporaryDirectory() as work:
                        manifest = Path(work) / 'files.txt'
                        command = downloader_command() + ['--ignore-config',
                                   '--js-runtimes', 'node',
                                   '--no-quiet', '--progress', '--newline', '--no-colors', '--progress-delta', '0.3',
                                   '--progress-template', 'download:USB_PROGRESS:%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s',
                                   '--progress-template', 'postprocess:USB_CONVERT:%(progress.status)s',
                                   '--windows-filenames', '--no-overwrites',
                                   '--extract-audio', '--audio-format', 'mp3', '--audio-quality', '192K',
                                   '--paths', str(cache), '-o', '%(title).140B [%(id)s].%(ext)s',
                                   '--print-to-file', 'after_move:filepath', str(manifest), '--', url]
                        result = download_with_progress(command, self.report_progress, self.cancel_event, self.log_detail)
                        check_cancel(self.cancel_event)
                        # Even a partially failed playlist may contain completed tracks.
                        files = manifest.read_text(encoding='utf-8').splitlines() if manifest.exists() else []
                        for filename in dict.fromkeys(files):
                            check_cancel(self.cancel_event)
                            source = Path(filename).resolve()
                            if source.parent != cache.resolve() or source.suffix.lower() != '.mp3':
                                raise ValueError('Unexpected downloader output path.')
                            if not destination.is_dir():
                                raise OSError('Destination disconnected. Local MP3 files are retained.')
                            self.report_progress('Copying: ' + source.name, 0)
                            self.events.put(copy_verified(source, destination, self.report_progress, self.cancel_event))
                        if result.returncode:
                            raise RuntimeError(result.stderr.strip()[-2500:] or 'Download failed.')
                        if not files:
                            raise RuntimeError('No MP3 files were produced.')
                except Cancelled:
                    raise
                except TransferCleanupError:
                    raise
                except Exception as error:
                    failures += 1
                    self.events.put('ERROR: ' + str(error))
            self.download_complete = failures == 0
            self.events.put(f'Finished with {failures} failed link(s). Local copies: {cache}')
            self.report_progress('Some songs could not be copied. Open Show details below.' if failures else 'Your songs are ready.', None if failures else 100)
        except Cancelled:
            self.events.put('Cancelled. Completed songs are kept. Any incomplete USB copy was removed; local downloads are kept for retry.')
        except TransferCleanupError as error:
            self.transfer_cleanup_failed = True
            self.events.put('ERROR: ' + str(error))
            self.report_progress('USB cleanup needs attention. Open Show details below.')
        except Exception as error:
            self.events.put('ERROR: ' + str(error))
            self.report_progress('Something went wrong. Open Show details below.')
        finally:
            self.events.put(None)

    def report_progress(self, stage, percent=None):
        self.events.put({'stage': stage, 'percent': percent})

    def log_detail(self, message):
        self.events.put(datetime.now().strftime('[%H:%M:%S] ') + message)

    def show_progress(self, stage, percent=None):
        self.progress_bar.stop()
        if percent is None and self.busy:
            self.progress_bar.configure(mode='indeterminate')
            self.progress_bar.start()
        else:
            self.progress_bar.configure(mode='determinate')
            self.progress_bar.set((percent or 0) / 100)
        self.percent_label.configure(text=f'{percent:.0f}%' if percent is not None else '')
        self.progress_label.configure(text=stage)

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event is None:
                    self.busy = False
                    self.progress_bar.stop()
                    self.set_controls(False)
                    self.show_done(self.download_complete and not self.cancel_event.is_set())
                    if self.transfer_cleanup_failed:
                        self.close_when_done = False
                        self.show_progress('USB cleanup could not finish. Keep it connected and open Show details.')
                    elif self.cancel_event.is_set():
                        self.show_progress('Stopped. Press Done before unplugging your USB.')
                    if self.close_when_done:
                        self.prepare_exit()
                    elif self.remove_when_stopped:
                        self.prepare_exit(close_app=False)
                elif isinstance(event, dict):
                    if event.get('usb_ready'):
                        self.progress_bar.stop()
                        self.ejecting = False
                        if self.exit_after_removal:
                            self.root.destroy()
                            return
                        self.set_controls(False)
                        self.drive.set('')
                        self.last_destination = None
                        self.usb_device = None
                        self.usb_identity_error = None
                        self.remove_when_stopped = False
                        self.show_progress('Stopped. You can unplug your USB now.')
                    elif 'usb_error' in event:
                        # Missing or in-use USB: do not interrupt the user or claim safe removal.
                        self.progress_bar.stop()
                        self.root.destroy()
                        return
                    elif not self.cancel_event.is_set() and not self.ejecting:
                        self.show_progress(event['stage'], event['percent'])
                else:
                    self.log.configure(state='normal')
                    self.log.insert('end', event + '\n')
                    self.log.see('end')
                    self.log.configure(state='disabled')
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


if __name__ == '__main__':
    if getattr(sys, 'frozen', False):
        os.environ['PATH'] = str(bundled_tools()) + os.pathsep + os.environ.get('PATH', '')
    root = ctk.CTk()
    App(root)
    if '--smoke-test' in sys.argv:
        root.withdraw()
        root.after(250, root.destroy)
    root.mainloop()
