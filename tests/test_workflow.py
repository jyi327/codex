import tempfile
import subprocess
import unittest
from unittest.mock import patch
from pathlib import Path
import app


class WorkflowTests(unittest.TestCase):
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
