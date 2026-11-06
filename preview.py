"""Cancellable, audio-only previews. No downloads or USB writes."""
import json
import re
import subprocess
import sys
import time
from urllib.parse import urlparse


class PreviewCancelled(Exception):
    pass


def run_process(command, cancel, stop_process, timeout, reject_stderr=False):
    if cancel.is_set():
        raise PreviewCancelled()
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                          start_new_session=sys.platform != 'win32',
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
        deadline = time.monotonic() + timeout
        try:
            while True:
                if cancel.is_set():
                    raise PreviewCancelled()
                if time.monotonic() >= deadline:
                    raise TimeoutError('Preview timed out. Please try again.')
                try:
                    output, error = process.communicate(timeout=.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:
                try:
                    stop_process(process)
                except (OSError, subprocess.SubprocessError):
                    # The process can exit between poll() and Windows taskkill.
                    if process.poll() is None:
                        process.kill()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
        if cancel.is_set():
            raise PreviewCancelled()
        if process.returncode or (reject_stderr and error.strip()):
            # Do not expose signed media URLs or network headers in the UI/log.
            raise RuntimeError('Preview unavailable. Please try another song.')
        return output


def stream_options(output):
    info = json.loads(output)
    url = info.get('url', '')
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.googlevideo.com'):
        raise ValueError('Preview audio is unavailable for this song.')
    headers = info.get('http_headers') or {}
    lines = []
    for name, value in headers.items():
        if not re.fullmatch(r'[A-Za-z0-9-]+', name) or not isinstance(value, str) or '\r' in value or '\n' in value:
            raise ValueError('Invalid preview response.')
        lines.append(f'{name}: {value}\r\n')
    return url, ''.join(lines)


def play(song_url, downloader, player, cancel, stop_process, notify, seconds=30):
    if not re.fullmatch(r'https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}', song_url):
        raise ValueError('Choose a song from the search results.')
    output = run_process(downloader + [
        '--ignore-config', '--encoding', 'utf-8', '--no-playlist', '--skip-download',
        '--dump-single-json', '--no-warnings', '--socket-timeout', '10',
        '--extractor-retries', '1', '--retries', '1', '-f', 'bestaudio/best', '--', song_url,
    ], cancel, stop_process, timeout=40)
    url, headers = stream_options(output)
    if cancel.is_set():
        raise PreviewCancelled()
    notify('playing')
    # ffplay streams directly to the sound device; the 30-second sample is not saved.
    run_process([player, '-hide_banner', '-loglevel', 'error', '-nodisp', '-autoexit',
                 '-vn', '-volume', '70', '-rw_timeout', '10000000',
                 '-tls_verify', '1', '-headers', headers, '-i', url, '-t', str(seconds)],
                cancel, stop_process, timeout=50, reject_stderr=True)


def smoke_test(player):
    """Verify packaged playback offline without needing speakers or a USB."""
    import math
    import os
    from pathlib import Path
    import struct
    import tempfile
    import wave
    with tempfile.TemporaryDirectory(prefix='YouTubeToUSB preview ') as work:
        tone = Path(work) / 'tone.wav'
        with wave.open(str(tone), 'wb') as audio:
            audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b''.join(struct.pack('<h', int(3000 * math.sin(2 * math.pi * 440 * i / 16000)))
                                      for i in range(4000)))
        environment = dict(os.environ, SDL_AUDIODRIVER='dummy')
        result = subprocess.run([player, '-hide_banner', '-loglevel', 'error', '-nodisp', '-autoexit',
                        '-vn', '-i', str(tone)], env=environment, check=True,
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=15)
        if result.stderr.strip():
            raise RuntimeError('Preview player could not decode the test audio.')
