"""Windows YouTube audio downloader and verified USB copier."""
import ctypes
from collections import deque
import hashlib
import os
from pathlib import Path
import queue
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


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.digest()


def copy_verified(source, folder, progress=None):
    def report(stage, percent=None):
        if progress:
            progress(stage, percent)

    folder.mkdir(parents=True, exist_ok=True)
    target = folder / source.name
    if target.exists():
        report('Checking existing file')
        if digest(source) == digest(target):
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
                    output.write(chunk)
                    copied += len(chunk)
                    percent = min(99, int(copied * 100 / total)) if total else 99
                    if percent != previous:
                        report('Copying to USB', percent)
                        previous = percent
            output.flush()
            os.fsync(output.fileno())
        report('Verifying USB copy')
        if digest(source) != digest(target):
            raise OSError('Copy verification failed: ' + source.name)
    except Exception:
        if created:
            target.unlink(missing_ok=True)
        raise
    report('Copy verified', 100)
    return 'Copied and verified: ' + source.name


def download_with_progress(command, progress):
    """Stream progress without buffering the entire downloader output."""
    recent = deque(maxlen=40)
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding='utf-8', errors='replace',
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
        for line in process.stdout:
            if line.startswith('USB_PROGRESS:'):
                parts = line.strip().split(':', 1)[1].split('|')
                try:
                    downloaded = float(parts[0])
                    total = next(float(value) for value in parts[1:] if value not in ('NA', 'None', '') and float(value) > 0)
                    progress('Downloading current track', max(0, min(100, downloaded * 100 / total)))
                except (ValueError, StopIteration, IndexError):
                    progress('Downloading current track')
            elif line.startswith('USB_CONVERT:') or line.startswith('[ExtractAudio]'):
                progress('Converting to MP3')
            else:
                recent.append(line.rstrip())
        return subprocess.CompletedProcess(command, process.wait(), '', '\n'.join(recent))


def removable_drives():
    if sys.platform != 'win32':
        return []
    return [f'{letter}:\\' for letter in 'DEFGHIJKLMNOPQRSTUVWXYZ'
            if ctypes.windll.kernel32.GetDriveTypeW(f'{letter}:\\') == 2]


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        root.title('YouTube to USB MP3')
        root.geometry('760x600')
        frame = ttk.Frame(root, padding=18)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='YouTube links (one per line; playlists are supported)').pack(anchor='w')
        self.urls = tk.Text(frame, height=7)
        self.urls.pack(fill='x', pady=8)
        ttk.Label(frame, text='USB destination (MP3s go directly here; one detected drive is selected automatically)').pack(anchor='w')
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=8)
        self.drive = ttk.Combobox(row)
        self.refresh_drives()
        self.drive.pack(side='left', fill='x', expand=True)
        ttk.Button(row, text='Refresh', command=self.refresh_drives).pack(side='left', padx=6)
        ttk.Button(row, text='Browse…', command=self.browse).pack(side='left')
        ttk.Label(frame, text='Use only audio you own or have permission to download.').pack(anchor='w')
        self.button = ttk.Button(frame, text='Convert and copy', command=self.start)
        self.button.pack(pady=12)
        self.progress_label = ttk.Label(frame, text='Ready')
        self.progress_label.pack(anchor='w')
        self.progress_bar = ttk.Progressbar(frame, maximum=100, mode='determinate')
        self.progress_bar.pack(fill='x', pady=(4, 10))
        self.log = tk.Text(frame, height=12, state='disabled', wrap='word')
        self.log.pack(fill='both', expand=True)
        self.busy = False
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)

    def close(self):
        if self.busy:
            messagebox.showinfo('Working', 'Please wait for the transfer to finish before closing.')
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
        self.button.configure(state='disabled')
        self.show_progress('Preparing download')
        threading.Thread(target=self.run, args=(urls, destination), daemon=True).start()

    def run(self, urls, destination):
        import tempfile
        failures = 0
        cache = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'YouTubeToUSB' / 'Downloads'
        try:
            cache.mkdir(parents=True, exist_ok=True)
            for link_index, url in enumerate(urls, 1):
                try:
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
                        result = download_with_progress(command, self.report_progress)
                        # Even a partially failed playlist may contain completed tracks.
                        files = manifest.read_text(encoding='utf-8').splitlines() if manifest.exists() else []
                        for filename in dict.fromkeys(files):
                            source = Path(filename).resolve()
                            if source.parent != cache.resolve() or source.suffix.lower() != '.mp3':
                                raise ValueError('Unexpected downloader output path.')
                            if not destination.is_dir():
                                raise OSError('Destination disconnected. Local MP3 files are retained.')
                            self.report_progress('Copying: ' + source.name, 0)
                            self.events.put(copy_verified(source, destination, self.report_progress))
                        if result.returncode:
                            raise RuntimeError(result.stderr.strip()[-2500:] or 'Download failed.')
                        if not files:
                            raise RuntimeError('No MP3 files were produced.')
                except Exception as error:
                    failures += 1
                    self.events.put('ERROR: ' + str(error))
            self.events.put(f'Finished with {failures} failed link(s). Local copies: {cache}')
            self.report_progress('Finished with errors' if failures else 'All files copied and verified', None if failures else 100)
        except Exception as error:
            self.events.put('ERROR: ' + str(error))
            self.report_progress('Stopped with an error')
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
                    self.button.configure(state='normal')
                elif isinstance(event, dict):
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
