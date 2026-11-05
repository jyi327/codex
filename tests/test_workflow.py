import tempfile
import subprocess
import unittest
from unittest.mock import patch, MagicMock
import queue
import threading
import time
import os
from pathlib import Path
import app


class WorkflowTests(unittest.TestCase):
    def test_automatic_drive_selection(self):
        instance = app.App.__new__(app.App)
        instance.drive = MagicMock()
        instance.drive.get.return_value = ''
        instance.drive.cget.return_value = ()
        with patch.object(app, 'removable_drives', return_value=['E:\\']):
            instance.refresh_drives()
        instance.drive.set.assert_called_once_with('E:\\')
        for drives in ([], ['E:\\', 'F:\\']):
            instance.drive.set.reset_mock()
            with patch.object(app, 'removable_drives', return_value=drives):
                instance.refresh_drives()
            instance.drive.set.assert_called_once_with('')

    def test_explicit_destination_is_preserved(self):
        instance = app.App.__new__(app.App)
        instance.drive = MagicMock()
        instance.drive.get.return_value = 'G:\\'
        instance.drive.cget.return_value = ()
        with patch.object(app, 'removable_drives', return_value=['E:\\']), patch.object(Path, 'is_dir', return_value=True):
            instance.refresh_drives()
        instance.drive.set.assert_not_called()

    def test_download_is_copied_to_destination_root(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            usb = root / 'usb'
            usb.mkdir()
            instance = app.App.__new__(app.App)
            instance.events = queue.Queue()
            instance.cancel_event = threading.Event()

            def download(command, progress, cancel, detail):
                cache = Path(command[command.index('--paths') + 1])
                source = cache / 'song.mp3'
                source.write_bytes(b'downloaded audio')
                manifest = Path(command[command.index('--print-to-file') + 2])
                manifest.write_text(str(source) + '\n', encoding='utf-8')
                return subprocess.CompletedProcess(command, 0, '', '')

            with patch.dict(app.os.environ, {'LOCALAPPDATA': str(root)}), patch.object(app, 'download_with_progress', side_effect=download):
                instance.run(['https://youtu.be/example'], usb)
            self.assertEqual((usb / 'song.mp3').read_bytes(), b'downloaded audio')
            self.assertFalse((usb / 'Music').exists())

    def test_streamed_download_percentage_and_conversion(self):
        updates = []
        code = "print('USB_PROGRESS:25|100|NA'); print('USB_PROGRESS:75|NA|100'); print('USB_PROGRESS:80|NA|NA'); print('USB_CONVERT:started'); print('test error'); raise SystemExit(2)"
        result = app.download_with_progress([app.sys.executable, '-c', code], lambda stage, percent=None: updates.append((stage, percent)))
        self.assertEqual(result.returncode, 2)
        self.assertIn('test error', result.stderr)
        self.assertEqual(updates, [('Starting downloader…', None), ('Downloading this song', 25), ('Downloading this song', 75), ('Downloading this song', None), ('Making the MP3 file', None)])

    def test_cancel_stops_a_silent_download(self):
        cancel = threading.Event()
        timer = threading.Timer(0.3, cancel.set)
        started = time.monotonic()
        timer.start()
        try:
            with self.assertRaises(app.Cancelled):
                app.download_with_progress([app.sys.executable, '-c', 'import time; time.sleep(10)'], lambda *args: None, cancel)
        finally:
            timer.cancel()
        self.assertLess(time.monotonic() - started, 6)

    def test_preparation_output_is_reported_live(self):
        logs, stages = [], []
        with tempfile.TemporaryDirectory() as work:
            finished = Path(work) / 'finished'
            code = f"import time; from pathlib import Path; print('[youtube] abc: Downloading webpage', flush=True); time.sleep(.2); Path({str(finished)!r}).touch(); print('WARNING: Retrying (1/3)', flush=True)"

            def detail(line):
                logs.append(line)
                if 'Downloading webpage' in line:
                    self.assertFalse(finished.exists(), 'Details must be delivered before the child finishes')

            result = app.download_with_progress([app.sys.executable, '-c', code], lambda stage, percent=None: stages.append(stage), detail=detail)
        self.assertEqual(result.returncode, 0)
        self.assertIn('[youtube] abc: Downloading webpage', logs)
        self.assertIn('WARNING: Retrying (1/3)', logs)
        self.assertIn('Reading YouTube link…', stages)
        self.assertIn('Connection slow. Retrying…', stages)

    def test_silent_downloader_reports_wait_without_fake_percentage(self):
        stages, logs = [], []
        with patch.object(app, 'QUIET_NOTICE_SECONDS', .1):
            app.download_with_progress([app.sys.executable, '-c', 'import time; time.sleep(.35)'], lambda stage, percent=None: stages.append((stage, percent)), detail=logs.append)
        self.assertIn(('Still waiting… You can cancel.', None), stages)
        self.assertTrue(any('No new downloader output' in line for line in logs))

    def test_cancel_stops_conversion_child_process(self):
        with tempfile.TemporaryDirectory() as work:
            ready = Path(work) / 'ready'
            survived = Path(work) / 'survived'
            child_code = f"from pathlib import Path; import time; Path({str(ready)!r}).touch(); time.sleep(1); Path({str(survived)!r}).touch()"
            parent_code = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child_code!r}]); time.sleep(8)"
            cancel = threading.Event()
            done = threading.Event()

            def cancel_when_started():
                for _ in range(200):
                    if ready.exists():
                        cancel.set()
                        return
                    if done.wait(0.02):
                        return
                cancel.set()

            watcher = threading.Thread(target=cancel_when_started)
            watcher.start()
            try:
                with self.assertRaises(app.Cancelled):
                    app.download_with_progress([app.sys.executable, '-c', parent_code], lambda *args: None, cancel)
            finally:
                done.set()
                watcher.join()
            self.assertTrue(ready.exists())
            threading.Event().wait(1.1)
            self.assertFalse(survived.exists(), 'The conversion child must also stop')

    def test_cancel_removes_incomplete_copy_and_keeps_other_files(self):
        for cancel_stage in ('Copying to USB', 'Verifying USB copy'):
            with self.subTest(stage=cancel_stage), tempfile.TemporaryDirectory() as work:
                root = Path(work)
                source = root / 'song.mp3'
                source.write_bytes(b'x' * (3 * 1024 * 1024))
                destination = root / 'usb'
                destination.mkdir()
                existing = destination / 'completed.mp3'
                existing.write_bytes(b'keep me')
                cancel = threading.Event()

                def progress(stage, percent=None):
                    if stage == cancel_stage and (percent is None or percent > 0):
                        cancel.set()

                with self.assertRaises(app.Cancelled):
                    app.copy_verified(source, destination, progress, cancel)
                self.assertFalse((destination / source.name).exists())
                self.assertEqual(existing.read_bytes(), b'keep me')
                self.assertTrue(source.exists())

    def test_cancel_does_not_start_next_link(self):
        instance = app.App.__new__(app.App)
        instance.events = queue.Queue()
        instance.cancel_event = threading.Event()

        def download(command, progress, cancel, detail):
            cancel.set()
            raise app.Cancelled()

        with tempfile.TemporaryDirectory() as work, patch.dict(app.os.environ, {'LOCALAPPDATA': work}), patch.object(app, 'download_with_progress', side_effect=download) as downloader:
            instance.run(['https://youtu.be/one', 'https://youtu.be/two'], Path(work))
        self.assertEqual(downloader.call_count, 1)
        events = list(instance.events.queue)
        self.assertTrue(any(isinstance(event, str) and event.startswith('Cancelled.') for event in events))
        self.assertFalse(any(isinstance(event, dict) and event.get('percent') == 100 for event in events))
        self.assertIsNone(events[-1])

    def test_cancel_flushes_before_removing_only_its_temporary_file(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            source = root / 'song.mp3'
            source.write_bytes(b'x' * (3 * 1024 * 1024))
            destination = root / 'usb'
            destination.mkdir()
            unrelated = destination / '.someone-elses.partial'
            unrelated.write_bytes(b'keep')
            cancel = threading.Event()
            events = []
            real_fsync = app.os.fsync
            real_unlink = Path.unlink

            def flush(descriptor):
                events.append('flush')
                return real_fsync(descriptor)

            def unlink(path, *args, **kwargs):
                if path.name.startswith('.youtubetousb-'):
                    events.append('remove')
                return real_unlink(path, *args, **kwargs)

            def progress(stage, percent=None):
                if stage == 'Copying to USB' and percent:
                    self.assertFalse((destination / source.name).exists())
                    cancel.set()

            with patch.object(app.os, 'fsync', side_effect=flush), patch.object(Path, 'unlink', unlink):
                with self.assertRaises(app.Cancelled):
                    app.copy_verified(source, destination, progress, cancel)
            self.assertLess(events.index('flush'), events.index('remove'))
            self.assertEqual(list(destination.iterdir()), [unrelated])
            self.assertEqual(unrelated.read_bytes(), b'keep')

    def test_publish_does_not_overwrite_a_file_created_during_copy(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            source = root / 'song.mp3'
            source.write_bytes(b'new audio')
            destination = root / 'usb'

            def progress(stage, percent=None):
                if stage == 'Verifying USB copy':
                    (destination / source.name).write_bytes(b'other app created this')

            with self.assertRaises(OSError):
                app.copy_verified(source, destination, progress)
            self.assertEqual((destination / source.name).read_bytes(), b'other app created this')
            self.assertEqual(list(destination.iterdir()), [destination / source.name])

    def test_failed_verification_does_not_publish_an_mp3(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            source = root / 'song.mp3'
            source.write_bytes(b'new audio')
            destination = root / 'usb'
            with patch.object(app, 'digest', side_effect=[b'original', b'corrupt']):
                with self.assertRaisesRegex(OSError, 'verification failed'):
                    app.copy_verified(source, destination)
            self.assertEqual(list(destination.iterdir()), [])

    def test_cleanup_failure_is_not_reported_as_normal_cancellation(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            source = root / 'song.mp3'
            source.write_bytes(b'x' * (2 * 1024 * 1024))
            destination = root / 'usb'
            cancel = threading.Event()
            real_unlink = Path.unlink

            def unlink(path, *args, **kwargs):
                if path.name.startswith('.youtubetousb-'):
                    raise PermissionError('USB is unavailable')
                return real_unlink(path, *args, **kwargs)

            def progress(stage, percent=None):
                if stage == 'Copying to USB' and percent:
                    cancel.set()

            with patch.object(Path, 'unlink', unlink):
                with self.assertRaises(app.TransferCleanupError):
                    app.copy_verified(source, destination, progress, cancel)
            self.assertFalse((destination / source.name).exists())

    @unittest.skipUnless(app.sys.platform == 'win32' or os.environ.get('DISPLAY'), 'GUI requires Windows or a display')
    def test_gui_cancel_and_restart_controls(self):
        root = app.tk.Tk()
        root.withdraw()
        try:
            with patch.object(app, 'removable_drives', return_value=['E:\\']):
                instance = app.App(root)
            self.assertEqual(instance.drive.get(), 'E:\\')
            instance.busy = True
            instance.prepare_exit = MagicMock()
            instance.set_controls(True)
            instance.cancel()
            self.assertTrue(instance.cancel_event.is_set())
            self.assertEqual(str(instance.cancel_button.cget('state')), 'disabled')
            instance.events.put(None)
            instance.poll()
            self.assertFalse(instance.busy)
            self.assertEqual(str(instance.button.cget('state')), 'normal')
            self.assertIn('Stopped', instance.progress_label.cget('text'))
            instance.busy = True
            instance.transfer_cleanup_failed = True
            instance.close_when_done = True
            instance.events.put(None)
            instance.poll()
            self.assertFalse(instance.close_when_done)
            self.assertIn('cleanup could not finish', instance.progress_label.cget('text'))
        finally:
            root.destroy()

    @unittest.skipUnless(app.sys.platform == 'win32' or os.environ.get('DISPLAY'), 'GUI requires Windows or a display')
    def test_paste_adds_newline_and_done_is_hidden_until_complete(self):
        root = app.tk.Tk()
        root.withdraw()
        try:
            with patch.object(app, 'removable_drives', return_value=['E:\\']):
                instance = app.App(root)
            self.assertEqual(instance.done_button.winfo_manager(), '')
            with patch.object(root, 'clipboard_get', return_value='https://youtu.be/one'):
                self.assertEqual(instance.paste_links(), 'break')
            with patch.object(root, 'clipboard_get', return_value='https://youtu.be/two'):
                instance.paste_links()
            self.assertEqual(instance.urls.get('1.0', 'end-1c'), 'https://youtu.be/one\nhttps://youtu.be/two\n')
            instance.show_done(True)
            self.assertEqual(instance.done_button.winfo_manager(), 'pack')
            self.assertEqual(instance.action_frame.winfo_manager(), '')
            instance.show_done(False)
            self.assertEqual(instance.done_button.winfo_manager(), '')
        finally:
            root.destroy()

    @unittest.skipUnless(app.sys.platform == 'win32' or os.environ.get('DISPLAY'), 'GUI requires Windows or a display')
    def test_start_requires_usb_and_updates_on_connection(self):
        root = app.tk.Tk()
        root.withdraw()
        try:
            with patch.object(app, 'removable_drives', return_value=[]):
                instance = app.App(root)
            self.assertEqual(instance.button.cget('state'), 'disabled')
            self.assertEqual(instance.progress_label.cget('text'), 'No USB connected')
            with patch.object(app, 'removable_drives', return_value=['E:\\']):
                instance.refresh_drives()
            self.assertEqual(instance.button.cget('state'), 'normal')
            self.assertEqual(instance.drive.get(), 'E:\\')
            with patch.object(app, 'removable_drives', return_value=[]), patch.object(Path, 'is_dir', return_value=False):
                instance.refresh_drives()
            self.assertEqual(instance.button.cget('state'), 'disabled')
            self.assertEqual(instance.drive.get(), '')
        finally:
            root.destroy()

    def test_copy_percentage_finishes_after_verification(self):
        updates = []
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / 'song.mp3'
            source.write_bytes(b'x' * (2 * 1024 * 1024 + 1))
            app.copy_verified(source, Path(work) / 'usb', lambda stage, percent=None: updates.append((stage, percent)))
        copying = [percent for stage, percent in updates if stage == 'Copying to USB']
        self.assertEqual(copying, sorted(copying))
        self.assertTrue(all(0 <= percent < 100 for percent in copying))
        self.assertEqual(updates[-2:], [('Verifying USB copy', None), ('Copy verified', 100)])

    def test_portable_downloader_uses_bundled_executable(self):
        with tempfile.TemporaryDirectory() as work:
            tools = Path(work) / 'tools'
            tools.mkdir()
            with patch.object(app.sys, 'frozen', True, create=True), patch.object(app.sys, '_MEIPASS', work, create=True):
                with self.assertRaises(FileNotFoundError):
                    app.downloader_command()
                (tools / 'yt-dlp.exe').touch()
                self.assertEqual(app.downloader_command(), [str(tools / 'yt-dlp.exe')])

    def test_youtube_links(self):
        self.assertEqual(len(app.validate_urls('https://youtu.be/abc\nhttps://www.youtube.com/watch?v=123')), 2)
        for value in ('', 'file:///tmp/audio', 'https://youtube.com.evil.org/x', '--exec=cmd'):
            with self.assertRaises(ValueError):
                app.validate_urls(value)

    def test_real_conversion_and_verified_copy(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            source = root / 'tone.mp3'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'sine=frequency=440:duration=0.2', '-b:a', '192k', str(source)], check=True)
            codec = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
                                             '-show_entries', 'stream=codec_name', '-of',
                                             'default=nw=1:nk=1', str(source)], text=True)
            self.assertEqual(codec.strip(), 'mp3')
            destination = root / 'usb' / 'Music'
            self.assertIn('Copied and verified', app.copy_verified(source, destination))
            self.assertEqual(app.digest(source), app.digest(destination / source.name))
            self.assertIn('Already present', app.copy_verified(source, destination))
            (destination / source.name).write_bytes(b'existing unrelated music')
            with self.assertRaises(FileExistsError):
                app.copy_verified(source, destination)
            self.assertEqual((destination / source.name).read_bytes(), b'existing unrelated music')


if __name__ == '__main__':
    unittest.main()
