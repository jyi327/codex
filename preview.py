"""Cancellable, audio-only previews. No downloads or USB writes."""
import json
import re
import subprocess
import sys
import time
from urllib.parse import urlparse


class PreviewCancelled(Exception):
    pass


def player_errors(output):
    if isinstance(output, bytes):
        output = output.decode('utf-8', errors='replace')
    # Windows ffplay can clear its terminal line even with loglevel=error.
    return re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]|\x9b[0-?]*[ -/]*[@-~]', '', output).strip()


def failure_reason(error, playback):
    """Diagnostics without including signed URLs, cookies, or HTTP headers."""
    text = player_errors(error).lower()
    for words, reason in [
        (('certificate', 'tls', 'ssl'), 'Secure media connection failed.'),
        (('403', 'forbidden'), 'YouTube refused the audio request (403).'),
        (('429', 'too many requests'), 'YouTube rate-limited the audio request.'),
        (('sign in', 'not a bot'), 'YouTube requires sign-in on this connection.'),
        (('requested format', 'no video formats'), 'YouTube supplied no playable audio format.'),
        (('audio device', 'audio open', 'audio subsystem'), 'The audio device could not be opened.'),
        (('option not found', 'unrecognized option'), 'The player does not support an audio option.'),
        (('timed out', 'timeout'), 'The audio connection timed out.'),
        (('failed to open', 'invalid data'), 'The player could not open the audio stream.'),
    ]:
        if any(word in text for word in words):
            return reason
    return 'Audio playback failed.' if playback else 'Audio link resolution failed.'


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
        if process.returncode or (reject_stderr and player_errors(error)):
            # Do not expose signed media URLs or network headers in the UI/log.
            raise RuntimeError('Preview unavailable. ' + failure_reason(error, reject_stderr))
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
    run_process([player, '-hide_banner', '-loglevel', 'error', '-nostats', '-nodisp', '-autoexit',
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
        # SDL 2 and SDL 3 use different spellings for the audio-driver hint.
        environment = dict(os.environ, SDL_AUDIODRIVER='dummy', SDL_AUDIO_DRIVER='dummy')
        result = subprocess.run([player, '-hide_banner', '-loglevel', 'error', '-nostats', '-nodisp', '-autoexit',
                        '-vn', '-i', str(tone)], env=environment, check=True,
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=15)
        if player_errors(result.stderr):
            # This offline fixture contains no media URLs or authentication headers.
            raise RuntimeError('Preview player could not decode the test audio: ' +
                               player_errors(result.stderr))
