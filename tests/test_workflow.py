import tempfile
import subprocess
import unittest
from unittest.mock import patch, MagicMock
import queue
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
        with patch.object(app, 'removable_drives', return_value=['E:\\']):
            instance.refresh_drives()
        instance.drive.set.assert_not_called()

    def test_download_is_copied_to_destination_root(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            usb = root / 'usb'
            usb.mkdir()
            instance = app.App.__new__(app.App)
            instance.events = queue.Queue()

            def download(command, progress):
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
        self.assertEqual(updates, [('Downloading current track', 25), ('Downloading current track', 75), ('Downloading current track', None), ('Converting to MP3', None)])

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
