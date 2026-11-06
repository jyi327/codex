"""Windows YouTube audio downloader and verified USB copier."""
import ctypes
from collections import deque, OrderedDict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import queue
import re
import signal
import shutil
import shlex
import subprocess
import sys
import threading
import tempfile
import time
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse, parse_qs, urlencode
import windows_usb
import youtube_search
import thumbnails
import preview
from download_queue import DownloadQueue
import customtkinter as ctk


def bundled_tools():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'tools'


def downloader_command():
    if getattr(sys, 'frozen', False):
        executable = bundled_tools() / 'downloader' / 'yt-dlp.exe'
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


def clean_usb_filename(filename):
    """Remove the downloader's trailing video ID and spaces for USB filenames."""
    title = re.sub(r' \[[A-Za-z0-9_-]{11}\](?=\.mp3$)', '', filename, flags=re.IGNORECASE)
    return re.sub(r'\s+', '', title)


def copy_verified(source, folder, progress=None, cancel=None, filename=None):
    def report(stage, percent=None):
        if progress:
            progress(stage, percent)

    check_cancel(cancel)
    folder.mkdir(parents=True, exist_ok=True)
    filename = filename or source.name
    if Path(filename).name != filename or '\\' in filename or filename in ('.', '..'):
        raise ValueError('Invalid destination filename.')
    target = folder / filename
    if target.exists():
        report('Checking existing file')
        if digest(source, cancel) == digest(target, cancel):
            report('Already present', 100)
            return 'Already present: ' + target.name
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
    return 'Copied and verified: ' + target.name


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
        return 'Preparing song…'
    if 'downloading webpage' in text or 'extracting url' in text:
        return 'Preparing song…'
    if 'downloading' in text and ('player' in text or 'api json' in text):
        return 'Preparing song…'
    if 'playlist' in text:
        return 'Preparing song…'
    if '[info]' in text and 'format' in text:
        return 'Preparing song…'
    return None


def download_with_progress(command, progress, cancel=None, detail=None, ffmpeg_progress=None):
    """Stream progress without buffering the entire downloader output."""
    recent = deque(maxlen=40)
    check_cancel(cancel)
    done = threading.Event()
    stop_errors = []
    last_output = [time.monotonic()]
    launched_at = last_output[0]
    conversion = {'active': False, 'duration': None, 'baseline': None}
    display = {'rank': 0, 'label': '', 'last': None}
    display_lock = threading.RLock()

    def emit(stage, percent=None, rank=None):
        with display_lock:
            if rank is not None:
                if rank < display['rank']:
                    return
                display['rank'] = rank
            message = display['label'] + stage
            if (message, percent) != display['last']:
                display['last'] = (message, percent)
                progress(message, percent)

    emit('Preparing song…')
    if detail:
        detail('Starting downloader; waiting for its first response…')
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding='utf-8', errors='replace',
                          start_new_session=sys.platform != 'win32',
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
        def watch_cancel():
            notified_at = None
            last_ffmpeg_signature = None
            last_conversion_log = 0
            # A separate watcher can stop FFmpeg even while it produces no output.
            while not done.wait(0.1):
                if cancel is not None and cancel.is_set():
                    try:
                        stop_process_tree(process)
                    except (OSError, subprocess.SubprocessError) as error:
                        if process.poll() is None:
                            stop_errors.append(str(error))
                            emit('Unable to stop yet. Please wait for the current song to finish.')
                    return
                if ffmpeg_progress is not None and conversion['active']:
                    try:
                        stat = ffmpeg_progress.stat()
                        signature = (stat.st_mtime_ns, stat.st_size)
                        if signature != last_ffmpeg_signature and signature != conversion['baseline']:
                            last_ffmpeg_signature = signature
                            fields = dict(line.split('=', 1) for line in ffmpeg_progress.read_text(encoding='utf-8').splitlines() if '=' in line)
                            seconds = max(0, int(fields['out_time_us']) / 1_000_000)
                            last_output[0] = time.monotonic()
                            duration = conversion['duration']
                            emit('Making the MP3 file', min(99, seconds * 100 / duration) if duration else None, rank=2)
                            if detail and time.monotonic() - last_conversion_log >= 5:
                                last_conversion_log = time.monotonic()
                                detail(f'FFmpeg has converted {seconds:.1f} seconds of audio.')
                    except (OSError, ValueError, KeyError):
                        pass  # FFmpeg may be between writes to its progress file.
                if time.monotonic() - last_output[0] >= QUIET_NOTICE_SECONDS and notified_at != last_output[0]:
                    notified_at = last_output[0]
                    emit('Waiting for audio conversion… You can cancel.' if conversion['active'] else 'Still waiting… You can cancel.')
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
                if not line.startswith(('USB_PROGRESS:', 'USB_CONVERT:', 'USB_TRACK:')):
                    text = line.rstrip()
                    if text:
                        recent.append(text)
                        if detail:
                            detail(text)
                if line.startswith('USB_TRACK:'):
                    fields = line.strip().split(':', 1)[1].split('|')
                    with display_lock:
                        display['rank'] = 0
                        display['last'] = None
                        if len(fields) >= 3 and fields[1].isdigit():
                            display['label'] = f'Song {fields[1]} of {fields[2]} · ' if fields[2].isdigit() and int(fields[2]) > 1 else f'Song {fields[1]} · '
                        else:
                            display['label'] = ''
                        conversion['active'] = False
                    if detail:
                        detail('Processing song: ' + ' | '.join(fields))
                    emit('Downloading audio', 0, rank=1)
                elif line.startswith('USB_PROGRESS:'):
                    parts = line.strip().split(':', 1)[1].split('|')
                    try:
                        downloaded = float(parts[0])
                        total = next(float(value) for value in parts[1:] if value not in ('NA', 'None', '') and float(value) > 0)
                        emit('Downloading this song', max(0, min(100, downloaded * 100 / total)), rank=1)
                    except (ValueError, StopIteration, IndexError):
                        emit('Downloading this song', rank=1)
                elif line.startswith('USB_CONVERT:'):
                    fields = line.strip().split(':', 1)[1].split('|')
                    processor, status, duration = (fields + ['NA', 'NA'])[:3] if len(fields) > 1 else ('ExtractAudio', fields[0], 'NA')
                    if detail:
                        detail(f'Post-processing: {processor} {status}.')
                    if processor == 'ExtractAudio':
                        conversion['active'] = status != 'finished'
                        try:
                            conversion['duration'] = float(duration) if float(duration) > 0 else None
                        except ValueError:
                            conversion['duration'] = None
                        if status == 'started' and ffmpeg_progress is not None:
                            try:
                                stat = ffmpeg_progress.stat()
                                conversion['baseline'] = (stat.st_mtime_ns, stat.st_size)
                            except OSError:
                                conversion['baseline'] = None
                        emit('MP3 ready. Finishing…' if status == 'finished' else 'Making the MP3 file', rank=3 if status == 'finished' else 2)
                elif line.startswith('[ExtractAudio]'):
                    emit('Making the MP3 file', rank=2)
                else:
                    item = re.search(r'Downloading item (\d+) of (\d+)', line)
                    if item:
                        with display_lock:
                            display['label'] = f'Song {item[1]} of {item[2]} · '
                        emit('Preparing song…')
                    else:
                        stage = preparation_status(line)
                        if stage:
                            emit(stage, rank=None if 'Retrying' in stage else 0)
            result = subprocess.CompletedProcess(command, process.wait(), '', '\n'.join(recent))
        finally:
            done.set()
            watcher.join()
        if stop_errors:
            raise RuntimeError('Could not stop immediately; the current download has now ended.')
        check_cancel(cancel)
        return result


def canonical_download_url(url):
    parsed = urlparse(url)
    video_id = parse_qs(parsed.query).get('v', [''])[0] if parsed.path == '/watch' else ''
    if parsed.hostname == 'youtu.be':
        video_id = parsed.path.strip('/')
    if parsed.hostname in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be') and re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        return 'https://www.youtube.com/watch?' + urlencode({'v': video_id})
    return url


def make_download_command(url, cache, manifest, ffmpeg_progress):
    location = bundled_tools() if getattr(sys, 'frozen', False) else Path(shutil.which('ffmpeg') or 'ffmpeg').resolve().parent
    return downloader_command() + [
        '--ignore-config', '--js-runtimes', 'node', '--ffmpeg-location', str(location),
        '--yes-playlist' if urlparse(url).path.rstrip('/') == '/playlist' else '--no-playlist',
        '--lazy-playlist', '--format', 'bestaudio/best', '--concurrent-fragments', '4',
        '--no-simulate', '--print', 'before_dl:USB_TRACK:%(id)s|%(playlist_index)s|%(playlist_count)s',
        '--retries', '3', '--fragment-retries', '3', '--extractor-retries', '2', '--socket-timeout', '20',
        '--no-quiet', '--progress', '--newline', '--no-colors', '--progress-delta', '0.3',
        '--progress-template', 'download:USB_PROGRESS:%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s',
        '--progress-template', 'postprocess:USB_CONVERT:%(progress.postprocessor)s|%(progress.status)s|%(info.duration)s',
        '--postprocessor-args', 'ExtractAudio+ffmpeg_o:-nostdin -stats_period 0.5 -progress ' + shlex.quote(str(ffmpeg_progress)),
        '--windows-filenames', '--no-overwrites', '--extract-audio', '--audio-format', 'mp3', '--audio-quality', '192K',
        '--paths', str(cache), '-o', '%(title).140B [%(id)s].%(ext)s',
        '--print-to-file', 'after_move:filepath', str(manifest), '--', canonical_download_url(url),
    ]


def conversion_smoke_test():
    """Exercise the actual downloader, FFmpeg, ffprobe and copy path offline."""
    with tempfile.TemporaryDirectory(prefix='YouTubeToUSB conversion ') as work:
        root = Path(work)
        source = root / 'test tone.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=1', str(source)], check=True,
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=30)
        manifest = root / 'files.txt'
        ffmpeg_progress = root / 'conversion progress.txt'
        command = make_download_command(source.as_uri(), root / 'downloads', manifest, ffmpeg_progress)
        command[-2:-2] = ['--enable-file-urls']  # Offline self-test only; GUI still accepts only YouTube.
        result = download_with_progress(command, lambda *args: None, ffmpeg_progress=ffmpeg_progress)
        if result.returncode:
            raise RuntimeError(result.stderr)
        files = manifest.read_text(encoding='utf-8').splitlines()
        if len(files) != 1 or not Path(files[0]).is_file():
            raise RuntimeError('Offline conversion did not create one MP3.')
        codec = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries',
                                         'stream=codec_name', '-of', 'default=nw=1:nk=1', files[0]],
                                        text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=30)
        if codec.strip() != 'mp3':
            raise RuntimeError('Converted output is not MP3 audio.')
        copy_verified(Path(files[0]), root / 'simulated USB')
        if 'progress=end' not in ffmpeg_progress.read_text(encoding='utf-8'):
            raise RuntimeError('FFmpeg did not report completed conversion progress.')


def removable_drives():
    if sys.platform != 'win32':
        return []
    return [f'{letter}:\\' for letter in 'DEFGHIJKLMNOPQRSTUVWXYZ'
            if ctypes.windll.kernel32.GetDriveTypeW(f'{letter}:\\') == 2]


class USBSelector(ctk.CTkFrame):
    """One compact device card; secondary choices stay in its dropdown."""
    def __init__(self, master, on_change, font_family, compact=False):
        super().__init__(master, fg_color='white', border_color='#d4e2f6', border_width=1, corner_radius=18, height=94)
        self._value = ''
        self._choices = []
        self._state = 'normal'
        self.on_change = on_change
        self.grid_columnconfigure(1, weight=1)
        icon_frame = ctk.CTkFrame(self, width=40 if compact else 56, height=40 if compact else 56, corner_radius=12, fg_color='#e7f0ff')
        icon_frame.grid(row=0, column=0, rowspan=2, padx=(14, 12), pady=8 if compact else 18)
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
        ctk.CTkLabel(self, text='USB drive', font=(font_family, 16 if compact else 18, 'bold'), text_color='#182338', anchor='w', height=22).grid(row=0, column=1, sticky='sw', pady=(7 if compact else 16, 0))
        self.path_label = ctk.CTkLabel(self, text='No USB connected', font=(font_family, 14 if compact else 16), text_color='#64748b', anchor='w', height=20)
        self.path_label.grid(row=1, column=1, sticky='nw', pady=(0, 7 if compact else 16))
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
        self.searching = False
        self.results = []
        self.selected_url = None
        self.result_widgets = []
        self.preview_widgets = {}
        self.preview_url = None
        self.preview_token = 0
        self.preview_cancel = threading.Event()
        self.preview_thread = None
        self.preview_state = None
        self.queue = DownloadQueue()
        self.queue_widgets = {}
        self.thumbnail_cache = OrderedDict()
        self.thumbnail_pending = set()
        self.thumbnail_failed = set()
        self.thumbnail_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='thumbnail')
        self.result_thumbnail_slots = {}
        self.queue_thumbnail_slots = {}
        self.details_window = None
        self.result_choice = tk.IntVar(value=-1)
        self.interface_font = 'Segoe UI' if sys.platform == 'win32' else 'Helvetica'
        interface_font = 'Segoe UI' if sys.platform == 'win32' else 'Helvetica'
        ctk.set_appearance_mode('light')
        root.title('YouTube to MP3')
        scale = getattr(root, '_get_window_scaling', lambda: 1.0)()
        width = min(1200, max(760, int(root.winfo_screenwidth() / scale) - 40))
        height = min(760, max(420, int(root.winfo_screenheight() / scale) - 80))
        root.geometry(f'{width}x{height}')
        root.minsize(min(900, width), min(540, height))
        root.configure(bg='#eff5ff')
        self.page = ctk.CTkFrame(root, fg_color='#eff5ff', corner_radius=0)
        self.page.pack(fill='both', expand=True)
        self.page.grid_columnconfigure(0, weight=1)
        self.page.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self.page, fg_color='transparent')
        header.grid(row=0, column=0, sticky='ew', padx=20, pady=(14, 0))
        ctk.CTkLabel(header, text='Find a song', font=(interface_font, 22, 'bold'), text_color='#17253b', anchor='w').pack(fill='x', pady=(0, 6))
        search_row = ctk.CTkFrame(header, fg_color='transparent')
        search_row.pack(fill='x')
        self.search_entry = ctk.CTkEntry(search_row, placeholder_text='Song name or artist', height=46, corner_radius=14,
                                         border_width=1, border_color='#ccdcf3', fg_color='white', text_color='#17253b', font=(interface_font, 18))
        self.search_entry.pack(side='left', fill='x', expand=True, padx=(0, 12))
        self.search_entry.bind('<Return>', lambda event: self.search_songs())
        self.search_button = ctk.CTkButton(search_row, text='SEARCH', width=140, height=46, corner_radius=14,
                                          font=(interface_font, 17, 'bold'), fg_color='#2563eb', hover_color='#1d4ed8', command=self.search_songs)
        self.search_button.pack(side='right')
        body = ctk.CTkFrame(self.page, fg_color='transparent')
        body.grid(row=1, column=0, sticky='nsew', padx=20, pady=(10, 0))
        body.grid_columnconfigure(0, weight=6, uniform='panels')
        body.grid_columnconfigure(1, weight=4, uniform='panels')
        body.grid_rowconfigure(0, weight=1)
        left = ctk.CTkFrame(body, fg_color='transparent')
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        right = ctk.CTkFrame(body, fg_color='transparent')
        right.grid(row=0, column=1, sticky='nsew')
        for panel in (left, right):
            panel.grid_columnconfigure(0, weight=1)
            panel.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(left, text='Search results', font=(interface_font, 18, 'bold'), text_color='#17253b', anchor='w').grid(row=0, column=0, sticky='ew', pady=(0, 6))
        self.queue_heading = ctk.CTkLabel(right, text='Download list (0)', font=(interface_font, 18, 'bold'), text_color='#17253b', anchor='w')
        self.queue_heading.grid(row=0, column=0, sticky='ew', pady=(0, 6))
        self.result_frame = ctk.CTkScrollableFrame(left, height=1, corner_radius=16, fg_color='white', border_width=1, border_color='#d4e2f6')
        self.result_frame.grid(row=1, column=0, sticky='nsew')
        self.result_frame.grid_columnconfigure(0, weight=1)
        self.queue_frame = ctk.CTkScrollableFrame(right, height=1, corner_radius=16, fg_color='white', border_width=1, border_color='#d4e2f6')
        self.queue_frame.grid(row=1, column=0, sticky='nsew')
        self.queue_frame.grid_columnconfigure(0, weight=1)
        self.drive = USBSelector(self.page, self.drive_changed, interface_font, compact=True)
        self.drive.grid(row=2, column=0, sticky='ew', padx=20, pady=(10, 0))
        status = ctk.CTkFrame(self.page, fg_color='transparent')
        status.grid(row=3, column=0, sticky='ew', padx=20, pady=(8, 6))
        progress_row = ctk.CTkFrame(status, fg_color='transparent')
        progress_row.pack(fill='x')
        self.progress_label = ctk.CTkLabel(progress_row, text='Search for a song.', font=(interface_font, 17, 'bold'), text_color='#17253b', anchor='w', height=28)
        self.progress_label.pack(side='left', fill='x', expand=True)
        self.percent_label = ctk.CTkLabel(progress_row, text='0%', font=(interface_font, 18), text_color='#526177')
        self.percent_label.pack(side='right')
        self.progress_bar = ctk.CTkProgressBar(status, height=9, corner_radius=5, fg_color='#d8e3f2', progress_color='#2563eb')
        self.progress_bar.set(0)
        self.progress_bar.pack(fill='x', pady=(5, 0))
        footer = ctk.CTkFrame(self.page, fg_color='transparent')
        footer.grid(row=4, column=0, sticky='ew', padx=20, pady=(4, 0))
        footer.grid_columnconfigure(0, weight=1)
        self.button_area = ctk.CTkFrame(footer, fg_color='transparent')
        self.button_area.grid(row=0, column=0, sticky='ew')
        self.action_frame = ctk.CTkFrame(self.button_area, fg_color='transparent')
        self.action_frame.pack(fill='x')
        self.button = ctk.CTkButton(self.action_frame, text='▶  START', height=50, corner_radius=14, font=(interface_font, 19, 'bold'), fg_color='#2563eb', hover_color='#1d4ed8', text_color='white', text_color_disabled='#7f93ae', command=self.start)
        self.button.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.cancel_button = ctk.CTkButton(self.action_frame, text='×  CANCEL', height=50, corner_radius=14, border_width=1, border_color='#c6d6ec', font=(interface_font, 19, 'bold'), fg_color='white', hover_color='#e8f0fc', text_color='#526177', text_color_disabled='#9aa9bd', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='left', fill='x', expand=True, padx=(8, 0))
        self.complete_frame = ctk.CTkFrame(self.button_area, fg_color='transparent')
        self.next_button = ctk.CTkButton(self.complete_frame, text='DOWNLOAD MORE', height=50, corner_radius=14, font=(interface_font, 18, 'bold'), fg_color='#2563eb', hover_color='#1d4ed8', text_color='white', command=self.next_song)
        self.next_button.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.done_button = ctk.CTkButton(self.complete_frame, text='✓  DONE', height=50, corner_radius=14, border_width=1, border_color='#c6d6ec', font=(interface_font, 18, 'bold'), fg_color='white', hover_color='#e8f0fc', text_color='#526177', command=self.close)
        self.done_button.pack(side='left', fill='x', expand=True, padx=(8, 0))
        self.retry_button = ctk.CTkButton(footer, text='RETRY FAILED', height=50, width=160, corner_radius=14, font=(interface_font, 15, 'bold'), command=lambda: self.start(retry_failed=True))
        self.details_visible = tk.BooleanVar(value=False)
        self.details_check = ctk.CTkButton(self.page, text='Details', font=(interface_font, 13), text_color='#64748b', fg_color='transparent', hover_color='#e2ecfb', width=80, height=24, anchor='w', command=self.toggle_details)
        self.details_check.grid(row=5, column=0, sticky='w', padx=20, pady=(0, 2))
        self.details_frame = ctk.CTkFrame(root)
        self.log = ctk.CTkTextbox(self.details_frame, state='disabled', wrap='word', font=(interface_font, 13))
        self.render_results('Search for a song to begin.')
        self.render_queue()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<Alt-s>', lambda event: self.start() if not self.busy else None)
        root.bind('<Escape>', lambda event: self.cancel())
        self.refresh_drives()
        root.after(100, self.poll)
        root.after(1500, self.monitor_drives)
        self.search_entry.focus_set()

    def toggle_details(self):
        if self.details_window is not None and self.details_window.winfo_exists():
            self.details_window.lift()
            return
        self.details_window = ctk.CTkToplevel(self.root)
        self.details_window.title('Details')
        self.details_window.geometry('720x400')
        self.details_text = ctk.CTkTextbox(self.details_window, font=(self.interface_font, 13), wrap='word')
        self.details_text.pack(fill='both', expand=True, padx=12, pady=12)
        self.details_text.insert('end', self.log.get('1.0', 'end'))
        self.details_text.configure(state='disabled')

    def destroy_window(self):
        self.stop_preview()
        pool = getattr(self, 'thumbnail_pool', None)
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
        for timer in self.root.tk.call('after', 'info'):
            try:
                self.root.tk.call('after', 'cancel', timer)
            except tk.TclError:
                pass
        self.root.destroy()

    def drive_changed(self, refresh=False, browse=False):
        if refresh:
            self.refresh_drives()
        elif browse:
            self.browse()
        self.update_start_state()

    def update_start_state(self):
        if not hasattr(self, 'button'):
            return
        ready = bool(self.queue.pending()) and bool(self.drive.get()) and not self.busy and not self.ejecting
        self.button.configure(state='normal' if ready else 'disabled', fg_color='#2563eb' if ready else '#dce5f2')
        can_cancel = not self.ejecting and ((self.busy and not self.cancel_event.is_set()) or (not self.busy and bool(self.queue.items)))
        self.cancel_button.configure(state='normal' if can_cancel else 'disabled')
        if self.queue.failed():
            self.retry_button.grid(row=0, column=1, padx=(12, 0))
            self.retry_button.configure(state='normal' if self.drive.get() and not self.busy and not self.ejecting else 'disabled')
        else:
            self.retry_button.grid_remove()
        if not self.busy and not self.ejecting and not self.download_complete and not self.drive.get():
            self.show_progress('No USB connected')

    def monitor_drives(self):
        if not self.busy and not self.ejecting:
            self.refresh_drives()
        self.root.after(1500, self.monitor_drives)

    def search_songs(self):
        if self.busy or self.ejecting:
            return
        try:
            query = youtube_search.validate_query(self.search_entry.get())
            command = downloader_command()
        except (ValueError, FileNotFoundError) as error:
            self.show_progress(str(error))
            return
        self.stop_preview()
        self.busy = True
        self.searching = True
        self.download_complete = False
        self.results = []
        self.thumbnail_failed.clear()
        self.cancel_event.clear()
        self.remove_when_stopped = False
        self.close_when_done = False
        self.show_done(False)
        self.render_results('Searching YouTube…')
        self.set_controls(True)
        self.show_progress('Searching YouTube…')
        self.log_detail('Searching YouTube for: ' + query)
        threading.Thread(target=self.search_worker, args=(query, command), daemon=False).start()

    def search_worker(self, query, command):
        try:
            results = youtube_search.search(query, command, self.cancel_event, stop_process_tree)
        except youtube_search.SearchCancelled:
            self.events.put({'search_cancelled': True})
        except Exception as error:
            self.events.put({'search_error': str(error)})
        else:
            self.events.put({'search_results': results})

    def finish_search(self, event):
        self.searching = False
        self.busy = False
        self.progress_bar.stop()
        self.results = event.get('search_results', [])
        if 'search_error' in event:
            self.log_detail('Search error: ' + event['search_error'])
            message = 'Search failed. Please try again.'
        elif event.get('search_cancelled'):
            message = 'Search cancelled.'
        else:
            message = 'Choose a song below.' if self.results else 'No songs found. Try another name.'
        self.render_results(message)
        self.set_controls(False)
        self.show_progress(message)
        if self.close_when_done:
            self.prepare_exit()

    def make_thumbnail(self, parent, song, size, slots):
        box = ctk.CTkFrame(parent, width=size[0], height=size[1], corner_radius=9, fg_color='#e7eef8')
        box.grid_propagate(False)
        image = ctk.CTkLabel(box, text='YouTube', font=(self.interface_font, 12), text_color='#7b8ba2')
        image.place(relx=.5, rely=.5, anchor='center', relwidth=1, relheight=1)
        video_id = song.get('id')
        if video_id:
            slots.setdefault(video_id, []).append((image, size))
            if video_id in self.thumbnail_cache:
                picture = self.thumbnail_cache[video_id]
                image.configure(text='', image=ctk.CTkImage(light_image=picture, size=size))
        seconds = song.get('duration')
        if seconds is not None:
            seconds = int(seconds)
            duration = f'{seconds//3600}:{seconds%3600//60:02d}:{seconds%60:02d}' if seconds >= 3600 else f'{seconds//60}:{seconds%60:02d}'
            badge = ctk.CTkLabel(box, text=duration, height=20, corner_radius=4, fg_color='#152031', text_color='white', font=(self.interface_font, 12), padx=4)
            badge.place(relx=.97, rely=.95, anchor='se')
        return box

    def load_thumbnails(self):
        for video_id in set(self.result_thumbnail_slots) | set(self.queue_thumbnail_slots):
            if video_id not in self.thumbnail_cache and video_id not in self.thumbnail_pending and video_id not in self.thumbnail_failed and len(self.thumbnail_pending) < 10:
                self.thumbnail_pending.add(video_id)
                self.thumbnail_pool.submit(self.thumbnail_worker, video_id)

    def thumbnail_worker(self, video_id):
        try:
            picture = thumbnails.fetch(video_id)
        except Exception:
            picture = None
        self.events.put({'thumbnail_id': video_id, 'picture': picture})

    def apply_thumbnail(self, event):
        video_id, picture = event['thumbnail_id'], event['picture']
        self.thumbnail_pending.discard(video_id)
        if picture is None:
            self.thumbnail_failed.add(video_id)
        if picture is not None:
            self.thumbnail_cache[video_id] = picture
            if len(self.thumbnail_cache) > 80:
                self.thumbnail_cache.popitem(last=False)
            for slots in (self.result_thumbnail_slots, self.queue_thumbnail_slots):
                for label, size in slots.get(video_id, []):
                    if label.winfo_exists():
                        label.configure(text='', image=ctk.CTkImage(light_image=picture, size=size))
        self.load_thumbnails()

    def render_results(self, message=''):
        for child in self.result_frame.winfo_children():
            child.destroy()
        self.result_widgets = []
        self.preview_widgets = {}
        self.result_thumbnail_slots = {}
        if not self.results:
            ctk.CTkLabel(self.result_frame, text=message, font=(self.interface_font, 15), text_color='#64748b', wraplength=400).grid(row=0, column=0, sticky='ew', pady=25)
            return
        for index, result in enumerate(self.results):
            row = ctk.CTkFrame(self.result_frame, corner_radius=12, border_width=1, border_color='#e5edf7', fg_color='white')
            row.grid(row=index, column=0, sticky='ew', padx=3, pady=4)
            row.grid_columnconfigure(1, weight=1)
            thumbnail = self.make_thumbnail(row, result, (144, 81), self.result_thumbnail_slots)
            thumbnail.grid(row=0, column=0, rowspan=2, padx=(10, 12), pady=10)
            title = ctk.CTkLabel(row, text=result['title'], font=(self.interface_font, 16, 'bold'), text_color='#17253b', anchor='w', justify='left', wraplength=350)
            title.grid(row=0, column=1, sticky='ew', pady=(10, 0))
            subtitle = ctk.CTkLabel(row, text=result.get('channel', ''), font=(self.interface_font, 13), text_color='#64748b', anchor='w', wraplength=330)
            subtitle.grid(row=1, column=1, sticky='ew', pady=(0, 10))
            check = ctk.CTkLabel(row, text='', width=30, font=(self.interface_font, 24, 'bold'), text_color='#2563eb')
            check.grid(row=0, column=2, padx=8, pady=(6, 0))
            play_button = ctk.CTkButton(row, text='▶ Preview', width=110, height=32, corner_radius=9,
                                       font=(self.interface_font, 13, 'bold'), fg_color='#e7f0ff',
                                       hover_color='#d7e6ff', text_color='#1d4ed8',
                                       command=lambda i=index: self.preview_song(i))
            play_button.grid(row=1, column=2, padx=(0, 10), pady=(0, 10))
            self.preview_widgets[result['url']] = play_button
            for target in (row, thumbnail, *thumbnail.winfo_children(), title, subtitle, check):
                target.bind('<Button-1>', lambda event, i=index: self.select_song(i))
            def fit_title(event, label=title, channel=subtitle, card=row):
                width = max(100, int(event.width / card._get_widget_scaling()) - 300)
                label.configure(wraplength=width)
                channel.configure(wraplength=width)
            row.bind('<Configure>', fit_title)
            self.result_widgets.append((row, check))
        self.refresh_checks()
        self.refresh_preview_buttons()
        self.load_thumbnails()

    def refresh_preview_buttons(self):
        for url, button in getattr(self, 'preview_widgets', {}).items():
            active = url == self.preview_url
            text = ('■ Loading…' if self.preview_state == 'loading' else '■ STOP') if active else '▶ Preview'
            button.configure(text=text, state='disabled' if self.busy or self.ejecting else 'normal',
                             fg_color='#2563eb' if active else '#e7f0ff',
                             text_color='white' if active else '#1d4ed8')

    def stop_preview(self):
        event = getattr(self, 'preview_cancel', None)
        if event is not None:
            event.set()
        self.preview_token = getattr(self, 'preview_token', 0) + 1
        self.preview_url = None
        self.preview_state = None
        self.refresh_preview_buttons()

    def preview_song(self, index):
        if self.busy or self.ejecting or not 0 <= index < len(self.results):
            return
        url = self.results[index]['url']
        if self.preview_url == url:
            self.stop_preview()
            return
        try:
            player = str(bundled_tools() / 'ffplay.exe') if getattr(sys, 'frozen', False) else shutil.which('ffplay')
            if not player or not Path(player).is_file():
                raise FileNotFoundError('Preview player is missing. Extract the complete ZIP again.')
            downloader = downloader_command()
        except FileNotFoundError as error:
            self.show_progress(str(error), 0)
            return
        self.stop_preview()
        self.preview_url = url
        self.preview_state = 'loading'
        self.preview_cancel = threading.Event()
        previous = self.preview_thread
        self.preview_thread = threading.Thread(target=self.preview_worker,
            args=(self.preview_token, url, downloader, player, self.preview_cancel, previous), daemon=False)
        self.refresh_preview_buttons()
        self.preview_thread.start()

    def preview_worker(self, token, url, downloader, player, cancel, previous):
        # Wait off the UI thread so two samples can never overlap, even on rapid clicks.
        if previous is not None:
            previous.join()
        if cancel.is_set():
            return
        def notify(state):
            self.events.put({'preview_token': token, 'preview_state': state})
        try:
            preview.play(url, downloader, player, cancel, stop_process_tree, notify)
        except preview.PreviewCancelled:
            return
        except Exception:
            notify('error')
        else:
            notify('finished')

    def apply_preview_event(self, event):
        if event['preview_token'] != self.preview_token:
            return
        self.preview_state = event['preview_state']
        if self.preview_state in ('finished', 'error'):
            failed = self.preview_state == 'error'
            self.stop_preview()
            if failed and not self.busy and not self.ejecting:
                self.show_progress('Preview unavailable. Please try another song.', 0)
        else:
            self.refresh_preview_buttons()

    def refresh_checks(self):
        for song, (row, check) in zip(self.results, self.result_widgets):
            chosen = song['url'] in self.queue.items
            row.configure(fg_color='#edf4ff' if chosen else 'white', border_color='#2563eb' if chosen else '#e5edf7')
            check.configure(text='✓' if chosen else '□')

    def render_queue(self):
        for child in self.queue_frame.winfo_children():
            child.destroy()
        self.queue_widgets = {}
        self.queue_thumbnail_slots = {}
        self.queue_heading.configure(text=f'Download list ({len(self.queue.items)})')
        if not self.queue.items:
            ctk.CTkLabel(self.queue_frame, text='Choose songs from the results.', font=(self.interface_font, 14), text_color='#64748b', wraplength=250).grid(row=0, column=0, pady=25)
            return
        for index, (url, song) in enumerate(self.queue.items.items(), 1):
            row = ctk.CTkFrame(self.queue_frame, corner_radius=12, border_width=1, border_color='#e5edf7', fg_color='white')
            row.grid(row=index, column=0, sticky='ew', padx=3, pady=4)
            row.grid_columnconfigure(1, weight=1)
            thumbnail = self.make_thumbnail(row, song, (72, 42), self.queue_thumbnail_slots)
            thumbnail.grid(row=0, column=0, rowspan=2, padx=(8, 0), pady=10)
            title = ctk.CTkLabel(row, text=f'{index}. {song["title"]}', font=(self.interface_font, 15, 'bold'), text_color='#17253b', anchor='w', justify='left', wraplength=260)
            title.grid(row=0, column=1, sticky='ew', padx=(12, 0), pady=(10, 0))
            state = ctk.CTkLabel(row, text=song['status'], font=(self.interface_font, 13), text_color='#64748b', anchor='w')
            state.grid(row=1, column=1, sticky='ew', padx=(12, 0), pady=(0, 10))
            remove = ctk.CTkButton(row, text='×', width=30, height=30, corner_radius=8, fg_color='transparent', hover_color='#e8f0fc', text_color='#526177', font=(self.interface_font, 22), command=lambda u=url: self.remove_song(u), state='disabled' if self.busy else 'normal')
            remove.grid(row=0, column=2, rowspan=2, padx=8)
            row.bind('<Configure>', lambda event, label=title, card=row: label.configure(wraplength=max(100, int(event.width / card._get_widget_scaling())-150)))
            self.queue_widgets[url] = (state, remove)
        self.load_thumbnails()

    def select_song(self, index):
        if self.busy or self.ejecting or not 0 <= index < len(self.results):
            return
        song = self.results[index]
        if song['url'] in self.queue.items:
            self.queue.remove(song['url'])
        else:
            self.queue.add(song)
        self.selected_url = next(iter(self.queue.items), None)
        self.download_complete = False
        self.show_done(False)
        self.refresh_checks()
        self.render_queue()
        self.update_start_state()
        self.show_progress(f'{len(self.queue.items)} songs selected', 0)

    def remove_song(self, url):
        if self.busy or self.ejecting:
            return
        self.queue.remove(url)
        self.selected_url = next(iter(self.queue.items), None)
        self.refresh_checks()
        self.render_queue()
        self.update_start_state()
        self.show_progress(f'{len(self.queue.items)} songs selected', 0)

    def song_event(self, url, status, percent=None, error=''):
        self.events.put({'song_url': url, 'song_status': status, 'percent': percent, 'error': error})

    def apply_song_event(self, event):
        url = event['song_url']
        self.queue.update(url, event['song_status'], event.get('percent'), event.get('error', ''))
        if url in self.queue_widgets:
            label, remove = self.queue_widgets[url]
            percent = event.get('percent')
            label.configure(text=event['song_status'] + (f' {percent:.0f}%' if percent is not None and event['song_status'] not in ('Done','Failed') else ''), text_color='#137b60' if event['song_status']=='Done' else '#b42318' if event['song_status']=='Failed' else '#64748b')

    def show_done(self, complete):
        if complete:
            self.action_frame.pack_forget()
            self.complete_frame.pack(fill='x')
        else:
            self.complete_frame.pack_forget()
            self.action_frame.pack(fill='x')

    def next_song(self):
        if self.busy or self.ejecting:
            return
        self.stop_preview()
        self.download_complete = False
        self.cancel_event.clear()
        self.remove_when_stopped = False
        self.close_when_done = False
        self.search_entry.configure(state='normal')
        self.search_entry.delete(0, 'end')
        self.results = []
        self.selected_url = None
        self.render_results('Search for another song.')
        self.show_done(False)
        self.refresh_drives()
        self.set_controls(False)
        if self.drive.get():
            self.show_progress('Search for another song.', 0)
        else:
            self.show_progress('No USB connected')
        self.search_entry.focus_set()

    def set_controls(self, busy):
        self.button.configure(state='disabled' if busy else 'normal')
        self.cancel_button.configure(state='normal' if busy else 'disabled')
        self.search_entry.configure(state='disabled' if busy else 'normal')
        self.search_button.configure(state='disabled' if busy else 'normal')
        for status, remove in self.queue_widgets.values():
            remove.configure(state='disabled' if busy else 'normal')
        self.drive.configure(state='disabled' if busy else 'normal')
        self.next_button.configure(state='disabled' if busy else 'normal')
        self.update_start_state()
        self.refresh_preview_buttons()

    def cancel(self):
        self.stop_preview()
        if not self.busy and not self.ejecting:
            self.queue.clear()
            self.selected_url = None
            self.download_complete = False
            self.show_done(False)
            self.refresh_checks()
            self.render_queue()
            self.update_start_state()
            self.show_progress('Selection cleared', 0)
            return
        if self.busy and not self.cancel_event.is_set():
            self.remove_when_stopped = not self.searching
            self.cancel_event.set()
            self.cancel_button.configure(state='disabled')
            self.show_progress('Stopping search…' if self.searching else 'Stopping… Please keep the USB plugged in.')

    def close(self):
        self.stop_preview()
        if self.ejecting:
            return
        if self.busy:
            self.close_when_done = True
            self.cancel()
        else:
            self.prepare_exit()

    def prepare_exit(self, close_app=True):
        self.stop_preview()
        if self.busy or self.ejecting:
            return
        if close_app:
            self.root.withdraw()
        destination = self.last_destination or self.drive.get()
        if not destination:
            self.destroy_window()
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

    def start(self, retry_failed=False):
        if self.busy or self.ejecting:
            return
        try:
            urls = self.queue.failed() if retry_failed else self.queue.pending()
            if not urls:
                raise ValueError('Choose songs to download first.')
            for url in urls:
                validate_urls(url)
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
        self.stop_preview()
        self.busy = True
        self.download_complete = False
        self.remove_when_stopped = False
        self.close_when_done = False
        self.show_done(False)
        self.cancel_event.clear()
        self.transfer_cleanup_failed = False
        self.set_controls(True)
        for url in urls:
            self.queue.update(url, 'Waiting')
        self.render_queue()
        self.show_progress('Preparing download')
        threading.Thread(target=self.run, args=(urls, destination), daemon=False).start()

    def run(self, urls, destination):
        import tempfile
        failures = 0
        current_url = None
        cache = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'YouTubeToUSB' / 'Downloads'
        identity_thread = None
        try:
            self.last_destination = destination
            self.usb_device = None
            self.usb_identity_error = None
            self.log_detail('Identifying USB in parallel with downloader startup…')

            def identify_usb():
                started = time.monotonic()
                try:
                    self.usb_device = windows_usb.describe_usb(destination)
                except Exception as error:
                    self.usb_identity_error = error
                    self.events.put('Automatic safe removal unavailable: ' + str(error))
                self.log_detail(f'USB check finished in {time.monotonic() - started:.1f} seconds.')

            identity_thread = threading.Thread(target=identify_usb, daemon=False)
            identity_thread.start()
            check_cancel(self.cancel_event)
            cache.mkdir(parents=True, exist_ok=True)
            for link_index, url in enumerate(urls, 1):
                current_url = url
                try:
                    check_cancel(self.cancel_event)
                    self.song_event(url, 'Preparing')
                    self.report_progress(f'Preparing song {link_index} of {len(urls)}')
                    self.events.put('Downloading: ' + url)
                    with tempfile.TemporaryDirectory() as work:
                        manifest = Path(work) / 'files.txt'
                        ffmpeg_progress = Path(work) / 'ffmpeg-progress.txt'
                        command = make_download_command(url, cache, manifest, ffmpeg_progress)
                        def link_progress(stage, percent=None):
                            prefix = f'Song {link_index} of {len(urls)} · ' if len(urls) > 1 else ''
                            self.report_progress(prefix + stage, percent)
                            state = 'Converting' if 'MP3 file' in stage or 'audio conversion' in stage else 'Downloading' if 'Downloading' in stage else 'Retrying' if 'Retrying' in stage else 'Finishing' if 'Finishing' in stage else 'Preparing'
                            self.song_event(url, state, percent)

                        result = download_with_progress(command, link_progress, self.cancel_event, self.log_detail, ffmpeg_progress)
                        check_cancel(self.cancel_event)
                        # Even a partially failed playlist may contain completed tracks.
                        files = manifest.read_text(encoding='utf-8').splitlines() if manifest.exists() else []
                        identity_thread.join()
                        for filename in dict.fromkeys(files):
                            check_cancel(self.cancel_event)
                            source = Path(filename).resolve()
                            if source.parent != cache.resolve() or source.suffix.lower() != '.mp3':
                                raise ValueError('Unexpected downloader output path.')
                            if not destination.is_dir():
                                raise OSError('Destination disconnected. Local MP3 files are retained.')
                            filename = clean_usb_filename(source.name)
                            self.report_progress('Copying: ' + filename, 0)
                            def copy_progress(stage, percent=None):
                                self.report_progress(stage, percent)
                                self.song_event(url, 'Verifying' if 'Verif' in stage or 'Checking' in stage else 'Copying', percent)
                            self.events.put(copy_verified(source, destination, copy_progress, self.cancel_event, filename=filename))
                        if result.returncode:
                            raise RuntimeError(result.stderr.strip()[-2500:] or 'Download failed.')
                        if not files:
                            raise RuntimeError('No MP3 files were produced.')
                        self.song_event(url, 'Done', 100)
                except Cancelled:
                    self.song_event(url, 'Stopped')
                    raise
                except TransferCleanupError:
                    raise
                except Exception as error:
                    failures += 1
                    self.song_event(url, 'Failed', error=str(error))
                    self.events.put('ERROR: ' + str(error))
            self.download_complete = True
            self.events.put(f'Finished with {failures} failed link(s). Local copies: {cache}')
            self.events.put({'batch_summary': True, 'copied': len(urls)-failures, 'failed': failures})
        except Cancelled:
            self.events.put('Cancelled. Completed songs are kept. Any incomplete USB copy was removed; local downloads are kept for retry.')
        except TransferCleanupError as error:
            self.transfer_cleanup_failed = True
            if current_url:
                self.song_event(current_url, 'Failed', error=str(error))
            self.events.put('ERROR: ' + str(error))
            self.report_progress('USB cleanup needs attention. Open Show details below.')
        except Exception as error:
            for failed_url in ([current_url] if current_url else urls):
                self.song_event(failed_url, 'Failed', error=str(error))
            self.events.put('ERROR: ' + str(error))
            self.report_progress('Something went wrong. Open Show details below.')
        finally:
            if identity_thread is not None:
                identity_thread.join()
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
                    waiting = self.queue.pending() if hasattr(self, 'queue') else []
                    self.show_done(self.download_complete and not self.cancel_event.is_set() and not waiting)
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
                    if 'preview_token' in event:
                        self.apply_preview_event(event)
                    elif 'thumbnail_id' in event:
                        self.apply_thumbnail(event)
                    elif 'song_url' in event:
                        self.apply_song_event(event)
                    elif event.get('batch_summary'):
                        copied, failed = self.queue.counts()
                        noun = 'song' if copied == 1 else 'songs'
                        waiting = len(self.queue.pending())
                        summary = f'{copied} {noun} copied · {failed} failed' + (f' · {waiting} waiting' if waiting else '')
                        self.show_progress(summary, None if failed else 100)
                    elif any(key in event for key in ('search_results', 'search_error', 'search_cancelled')):
                        self.finish_search(event)
                    elif event.get('usb_ready'):
                        self.progress_bar.stop()
                        self.ejecting = False
                        if self.exit_after_removal:
                            self.destroy_window()
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
                        self.destroy_window()
                        return
                    elif not self.cancel_event.is_set() and not self.ejecting:
                        self.show_progress(event['stage'], event['percent'])
                else:
                    self.log.configure(state='normal')
                    self.log.insert('end', event + '\n')
                    self.log.see('end')
                    self.log.configure(state='disabled')
                    if self.details_window is not None and self.details_window.winfo_exists():
                        self.details_text.configure(state='normal')
                        self.details_text.insert('end', event + '\n')
                        self.details_text.see('end')
                        self.details_text.configure(state='disabled')
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


if __name__ == '__main__':
    if getattr(sys, 'frozen', False):
        os.environ['PATH'] = str(bundled_tools()) + os.pathsep + os.environ.get('PATH', '')
    if '--search-test' in sys.argv:
        report = Path(sys.argv[sys.argv.index('--search-test') + 1])
        try:
            results = youtube_search.search('Jasmine Flower instrumental', downloader_command(), threading.Event(), stop_process_tree)
            if not results:
                raise RuntimeError('YouTube returned no search results.')
            try:
                thumbnails.fetch(results[0]['id'])
                image_status = 'Thumbnail download succeeded.'
            except Exception:
                image_status = 'Thumbnail unavailable on this network; placeholder remains usable.'
            report.write_text(f'Live search succeeded: {len(results)} results returned. {image_status}\n', encoding='utf-8')
        except Exception as error:
            report.write_text('Live search could not be verified on this network: ' + str(error) + '\n', encoding='utf-8')
            sys.exit(1)
        sys.exit(0)
    if '--conversion-test' in sys.argv:
        try:
            conversion_smoke_test()
        except Exception:
            import traceback
            Path(sys.argv[sys.argv.index('--conversion-test') + 1]).write_text(traceback.format_exc(), encoding='utf-8')
            sys.exit(1)
        sys.exit(0)
    if '--preview-test' in sys.argv:
        try:
            player = str(bundled_tools() / 'ffplay.exe') if getattr(sys, 'frozen', False) else shutil.which('ffplay')
            preview.smoke_test(player)
        except Exception:
            import traceback
            Path(sys.argv[sys.argv.index('--preview-test') + 1]).write_text(traceback.format_exc(), encoding='utf-8')
            sys.exit(1)
        sys.exit(0)
    if '--preview-stream-test' in sys.argv:
        report = Path(sys.argv[sys.argv.index('--preview-stream-test') + 1])
        try:
            results = youtube_search.search('Jasmine Flower instrumental', downloader_command(), threading.Event(), stop_process_tree)
            if not results:
                raise RuntimeError('No search results.')
            # Build check only: decode a brief real stream without needing runner speakers.
            os.environ['SDL_AUDIODRIVER'] = 'dummy'
            player = str(bundled_tools() / 'ffplay.exe') if getattr(sys, 'frozen', False) else shutil.which('ffplay')
            preview.play(results[0]['url'], downloader_command(), player, threading.Event(), stop_process_tree,
                         lambda state: None, seconds=2)
            report.write_text('Live audio preview succeeded: streamed a YouTube audio sample through a dummy audio device.\n', encoding='utf-8')
        except Exception:
            report.write_text('Live audio preview could not be verified on this network.\n', encoding='utf-8')
            sys.exit(1)
        sys.exit(0)
    root = ctk.CTk()
    App(root)
    if '--smoke-test' in sys.argv:
        root.withdraw()
        root.after(250, root.destroy)
    root.mainloop()
