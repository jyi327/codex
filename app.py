"""Windows YouTube audio downloader and verified USB copier."""
import ctypes
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


def copy_verified(source, folder):
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / source.name
    if target.exists():
        if digest(source) == digest(target):
            return 'Already present: ' + source.name
        raise FileExistsError('Different file already exists: ' + str(target))
    if shutil.disk_usage(folder).free < source.stat().st_size + 1024 * 1024:
        raise OSError('Not enough free space on the selected drive.')
    # Exclusive creation prevents a race from overwriting another file.
    created = False
    try:
        with target.open('xb') as output:
            created = True
            with source.open('rb') as incoming:
                shutil.copyfileobj(incoming, output)
            output.flush()
            os.fsync(output.fileno())
        if digest(source) != digest(target):
            raise OSError('Copy verification failed: ' + source.name)
    except Exception:
        if created:
            target.unlink(missing_ok=True)
        raise
    return 'Copied and verified: ' + source.name


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
        root.geometry('720x540')
        frame = ttk.Frame(root, padding=18)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='YouTube links (one per line; playlists are supported)').pack(anchor='w')
        self.urls = tk.Text(frame, height=7)
        self.urls.pack(fill='x', pady=8)
        ttk.Label(frame, text='USB drive destination (MP3s go into its Music folder)').pack(anchor='w')
        row = ttk.Frame(frame)
