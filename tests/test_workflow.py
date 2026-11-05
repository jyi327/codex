import unittest
from pathlib import Path
import tempfile

import app


class WorkflowTests(unittest.TestCase):
    def test_valid_youtube_link(self):
        self.assertEqual(
            app.validate_urls("https://youtu.be/example"),
            ["https://youtu.be/example"],
        )

    def test_reject_other_websites(self):
        with self.assertRaises(ValueError):
            app.validate_urls("https://example.com/song")

    def test_copy_and_skip_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "song.mp3"
            source.write_bytes(b"test audio")
            destination = root / "usb" / "Music"

            app.copy_verified(source, destination)

            self.assertEqual(
                source.read_bytes(),
                (destination / "song.mp3").read_bytes(),
            )
            self.assertIn(
                "Already present",
                app.copy_verified(source, destination),
            )
