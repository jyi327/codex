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
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse


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
    # Exclusive creation prevents a race from overwriting another file.
    created = False
    report('Copying to USB', 0)
    try:
        with target.open('xb') as output:
            created = True
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
            output.flush()
            os.fsync(output.fileno())
        report('Verifying USB copy')
        if digest(source, cancel) != digest(target, cancel):
            raise OSError('Copy verification failed: ' + source.name)
    except Exception:
        if created:
            target.unlink(missing_ok=True)
        raise
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


def download_with_progress(command, progress, cancel=None):
    """Stream progress without buffering the entire downloader output."""
    recent = deque(maxlen=40)
    check_cancel(cancel)
    done = threading.Event()
    stop_errors = []
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding='utf-8', errors='replace',
                          start_new_session=sys.platform != 'win32',
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
        def watch_cancel():
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

        watcher = threading.Thread(target=watch_cancel, daemon=True)
        watcher.start()
        try:
            for line in process.stdout:
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
                    recent.append(line.rstrip())
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


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.busy = False
        self.close_when_done = False
        root.title('Songs to your USB drive')
        root.geometry('860x740')
        root.minsize(680, 620)
        root.option_add('*Font', ('Segoe UI', 14))
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 14), background='#f3f5f8', foreground='#18283b')
        style.configure('Title.TLabel', font=('Segoe UI', 24, 'bold'))
        style.configure('Step.TLabel', font=('Segoe UI', 16, 'bold'))
        style.configure('TButton', padding=(16, 12))
        style.configure('Start.TButton', font=('Segoe UI', 17, 'bold'), background='#1457a0', foreground='white')
        style.map('Start.TButton', background=[('disabled', '#d6dee8'), ('active', '#103f75')], foreground=[('disabled', '#566373')])
        style.configure('Stop.TButton', font=('Segoe UI', 17, 'bold'), background='#a72a28', foreground='white')
        style.map('Stop.TButton', background=[('disabled', '#d6dee8'), ('active', '#7c1c1b')], foreground=[('disabled', '#566373')])
        style.configure('TProgressbar', thickness=28)
        canvas = tk.Canvas(root, highlightthickness=0, background='#f3f5f8')
        page_scroll = ttk.Scrollbar(root, command=canvas.yview)
        canvas.configure(yscrollcommand=page_scroll.set)
        page_scroll.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        frame = ttk.Frame(canvas, padding=22)
        page = canvas.create_window((0, 0), window=frame, anchor='nw')
        frame.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(page, width=event.width))
        root.bind('<MouseWheel>', lambda event: canvas.yview_scroll(-int(event.delta / 120), 'units') if not isinstance(event.widget, tk.Text) else None)
        ttk.Label(frame, text='Songs to your USB drive', style='Title.TLabel').pack(anchor='w')
        ttk.Label(frame, text='Plug in your USB drive. Then follow these three steps.').pack(anchor='w', pady=(4, 16))
        link_row = ttk.Frame(frame)
        link_row.pack(fill='x')
        ttk.Label(link_row, text='1. Paste your YouTube links', style='Step.TLabel').pack(side='left')
        self.paste_button = ttk.Button(link_row, text='Paste links', command=self.paste_links)
        self.paste_button.pack(side='right')
        link_box = ttk.Frame(frame)
        link_box.pack(fill='x', pady=(6, 4))
        self.urls = tk.Text(link_box, height=4, width=40, wrap='word', font=('Segoe UI', 14),
                            background='white', foreground='#18283b', insertbackground='#18283b',
                            relief='solid', borderwidth=1, undo=True)
        link_scroll = ttk.Scrollbar(link_box, command=self.urls.yview)
        self.urls.configure(yscrollcommand=link_scroll.set)
        self.urls.pack(side='left', fill='both', expand=True)
        link_scroll.pack(side='right', fill='y')
        ttk.Label(frame, text='Put each link on a new line. A playlist link works too.').pack(anchor='w', pady=(0, 12))
        ttk.Label(frame, text='2. Check your USB drive', style='Step.TLabel').pack(anchor='w')
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=8)
        self.drive = ttk.Combobox(row, font=('Segoe UI', 14))
        self.refresh_drives()
        self.drive.pack(side='left', fill='x', expand=True)
        self.refresh_button = ttk.Button(row, text='Find USB', command=self.refresh_drives)
        self.refresh_button.pack(side='left', padx=8)
        self.browse_button = ttk.Button(row, text='Choose folder', command=self.browse)
        self.browse_button.pack(side='left')
        ttk.Label(frame, text='One USB drive is selected automatically. Songs go directly onto it.').pack(anchor='w', pady=(0, 12))
        ttk.Label(frame, text='3. Start copying your songs', style='Step.TLabel').pack(anchor='w')
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x', pady=8)
        self.button = ttk.Button(buttons, text='Start', style='Start.TButton', command=self.start)
        self.button.pack(side='left', fill='x', expand=True, padx=(0, 6))
        self.cancel_button = ttk.Button(buttons, text='Cancel', style='Stop.TButton', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='left', fill='x', expand=True, padx=(6, 0))
        self.progress_label = ttk.Label(frame, text='Ready. Paste a link above to begin.', style='Step.TLabel', wraplength=620)
        self.progress_label.pack(anchor='w', pady=(10, 4))
        self.progress_bar = ttk.Progressbar(frame, maximum=100, mode='determinate')
        self.progress_bar.pack(fill='x', pady=(4, 8))
        ttk.Label(frame, text='Keep the USB plugged in until copying or cancelling has finished.').pack(anchor='w')
        ttk.Label(frame, text='Only download songs you own or have permission to download.').pack(anchor='w', pady=(4, 6))
        self.details_visible = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text='Show details', variable=self.details_visible, command=self.toggle_details).pack(anchor='w')
        self.details_frame = ttk.Frame(frame)
        self.log = tk.Text(self.details_frame, height=5, state='disabled', wrap='word',
                           font=('Segoe UI', 12), background='white', foreground='#18283b')
        scroll = ttk.Scrollbar(self.details_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<Alt-s>', lambda event: self.start() if not self.busy else None)
        root.bind('<Escape>', lambda event: self.cancel())
        root.after(100, self.poll)
        self.urls.focus_set()

    def toggle_details(self):
        if self.details_visible.get():
            self.details_frame.pack(fill='both', expand=True, pady=(6, 0))
        else:
            self.details_frame.pack_forget()

    def paste_links(self):
        try:
            text = self.root.clipboard_get().strip()
            if text:
                if self.urls.get('1.0', 'end').strip():
                    self.urls.insert('end', '\n')
                self.urls.insert('end', text + '\n')
        except tk.TclError:
            messagebox.showinfo('Nothing to paste', 'Copy a YouTube link first, then press Paste links.')

    def set_controls(self, busy):
        self.button.configure(state='disabled' if busy else 'normal')
        self.cancel_button.configure(state='normal' if busy else 'disabled')
        self.urls.configure(state='disabled' if busy else 'normal')
        self.drive.configure(state='disabled' if busy else 'normal')
        for button in (self.paste_button, self.refresh_button, self.browse_button):
            button.configure(state='disabled' if busy else 'normal')

    def cancel(self):
        if self.busy and not self.cancel_event.is_set():
            self.cancel_event.set()
            self.cancel_button.configure(state='disabled')
            self.show_progress('Stopping… Please keep the USB plugged in.')

    def close(self):
        if self.busy:
            if messagebox.askyesno('Stop and close?', 'Stop copying and close the app? Songs already copied will stay on your USB drive.'):
                self.close_when_done = True
                self.cancel()
        else:
            self.root.destroy()

    def browse(self):
        folder = filedialog.askdirectory(title='Select the flash drive or a folder on it')
        if folder:
            self.drive.set(folder)

    def refresh_drives(self):
        drives = removable_drives()
        current = self.drive.get()
        previous = self.drive.cget('values')
        self.drive.configure(values=drives)
        if not current or (current in previous and current not in drives):
            self.drive.set(drives[0] if len(drives) == 1 else '')

    def start(self):
        if self.busy:
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
        self.cancel_event.clear()
        self.set_controls(True)
        self.show_progress('Preparing download')
        threading.Thread(target=self.run, args=(urls, destination), daemon=True).start()

    def run(self, urls, destination):
        import tempfile
        failures = 0
        cache = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'YouTubeToUSB' / 'Downloads'
        try:
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
                                   '--progress', '--newline', '--no-colors', '--progress-delta', '0.3',
                                   '--progress-template', 'download:USB_PROGRESS:%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s',
                                   '--progress-template', 'postprocess:USB_CONVERT:%(progress.status)s',
                                   '--windows-filenames', '--no-overwrites',
                                   '--extract-audio', '--audio-format', 'mp3', '--audio-quality', '192K',
                                   '--paths', str(cache), '-o', '%(title).140B [%(id)s].%(ext)s',
                                   '--print-to-file', 'after_move:filepath', str(manifest), '--', url]
                        result = download_with_progress(command, self.report_progress, self.cancel_event)
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
                except Exception as error:
                    failures += 1
                    self.events.put('ERROR: ' + str(error))
            self.events.put(f'Finished with {failures} failed link(s). Local copies: {cache}')
            self.report_progress('Some songs could not be copied. Open Show details below.' if failures else 'Done! Your songs are on the USB drive.', None if failures else 100)
        except Cancelled:
            self.events.put('Cancelled. Completed songs are kept. Any incomplete USB copy was removed; local downloads are kept for retry.')
        except Exception as error:
            self.events.put('ERROR: ' + str(error))
            self.report_progress('Something went wrong. Open Show details below.')
        finally:
            self.events.put(None)

    def report_progress(self, stage, percent=None):
        self.events.put({'stage': stage, 'percent': percent})

    def show_progress(self, stage, percent=None):
        self.progress_bar.stop()
        if percent is None and self.busy:
            self.progress_bar.configure(mode='indeterminate')
            self.progress_bar.start(15)
        else:
            self.progress_bar.configure(mode='determinate', value=percent or 0)
        suffix = f' — {percent:.0f}%' if percent is not None else ''
        self.progress_label.configure(text=stage + suffix)

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event is None:
                    self.busy = False
                    self.progress_bar.stop()
                    self.set_controls(False)
                    if self.cancel_event.is_set():
                        self.show_progress('Stopped. Songs already copied are still on your USB.')
                    if self.close_when_done:
                        self.root.destroy()
                        return
                elif isinstance(event, dict):
                    if not self.cancel_event.is_set():
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
    root = tk.Tk()
    App(root)
    root.mainloop()
