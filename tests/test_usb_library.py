import os
from pathlib import Path
import stat
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import usb_library as library


class USBLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, content=b'audio'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_filters_and_counts_nested_audio_without_extensions(self):
        for name in ['Song.MP3', 'track.live.flac', 'Classics/Old.wav', 'Classics/Deep/New.m4a']:
            self.write(name)
        for name in ['Notes.txt', 'Movie.mp4', '.hidden.mp3', '.secret/song.mp3', 'Empty/readme.txt', 'Classics/.ignore.mp3']:
            self.write(name)
        snapshot = library.scan(self.root)
        self.assertEqual(snapshot.count, 4)
        self.assertEqual([e.name for e in snapshot.entries], ['Song', 'track.live', 'Classics'])
        folder = snapshot.entries[-1]
        self.assertTrue(folder.is_folder)
        self.assertEqual(folder.count, 2)
        self.assertEqual(folder.children[-1].name, 'Deep')
        self.assertIsNone(library.find_entry(snapshot.entries, ('Empty',)))
        self.assertIsNone(library.find_entry(snapshot.entries, ('.secret',)))
        self.assertEqual(library.find_entry(snapshot.entries, ('Classics', 'Deep', 'New.m4a')).name, 'New')

    def test_windows_hidden_system_and_reparse_attributes_are_excluded(self):
        for attributes in (0x2, 0x4, 0x400):
            info = SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=attributes)
            self.assertTrue(library.excluded('Song.mp3', info))
        self.assertFalse(library.excluded('Song.mp3', SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x20)))

    @unittest.skipUnless(os.name == 'nt', 'Windows file attributes')
    def test_real_windows_hidden_files_and_folders_are_omitted(self):
        import ctypes
        for name in ['Hidden.mp3', 'Secret/inside.mp3', 'Visible.mp3']:
            self.write(name)
        set_attributes = ctypes.windll.kernel32.SetFileAttributesW
        self.assertTrue(set_attributes(str(self.root / 'Hidden.mp3'), 2))
        self.assertTrue(set_attributes(str(self.root / 'Secret'), 2))
        self.assertEqual([entry.name for entry in library.scan(self.root).entries], ['Visible'])

    def test_links_are_not_followed(self):
        self.write('Folder/song.mp3')
        try:
            os.symlink(self.root / 'Folder', self.root / 'Linked', target_is_directory=True)
            os.symlink(self.root / 'Folder/song.mp3', self.root / 'linked.mp3')
        except OSError:
            self.skipTest('Creating links is unavailable')
        snapshot = library.scan(self.root)
        self.assertEqual(snapshot.count, 1)
        self.assertEqual([entry.name for entry in snapshot.entries], ['Folder'])

    def test_refresh_detects_external_additions_deletions_and_empty_folders(self):
        first = self.write('Folder/old.mp3')
        self.assertEqual(library.scan(self.root).count, 1)
        self.write('new.ogg')
        self.assertEqual(library.scan(self.root).count, 2)
        first.unlink()
        snapshot = library.scan(self.root)
        self.assertEqual(snapshot.count, 1)
        self.assertEqual([entry.name for entry in snapshot.entries], ['new'])

    def test_scan_and_delete_are_cancellable(self):
        path = self.write('song.mp3')
        snapshot = library.scan(self.root)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(library.Cancelled):
            library.scan(self.root, cancel)
        with self.assertRaises(library.Cancelled):
            library.delete_song(snapshot, snapshot.entries[0], cancel)
        self.assertTrue(path.exists())

    def test_delete_removes_only_the_confirmed_nested_song(self):
        wanted = self.write('Folder/song.mp3')
        other = self.write('Folder/keep.wav')
        note = self.write('Folder/notes.txt')
        snapshot = library.scan(self.root)
        entry = library.find_entry(snapshot.entries, ('Folder', 'song.mp3'))
        library.delete_song(snapshot, entry)
        self.assertFalse(wanted.exists())
        self.assertTrue(other.exists())
        self.assertTrue(note.exists())
        self.assertTrue(wanted.parent.is_dir())
        self.assertEqual(library.scan(self.root).count, 1)

    def test_duplicate_display_names_delete_the_correct_format(self):
        self.write('Song.mp3')
        keep = self.write('Song.flac')
        snapshot = library.scan(self.root)
        self.assertEqual([entry.name for entry in snapshot.entries], ['Song', 'Song'])
        library.delete_song(snapshot, library.find_entry(snapshot.entries, ('Song.mp3',)))
        self.assertTrue(keep.exists())

    def test_replaced_or_modified_file_is_not_deleted(self):
        for replace in (True, False):
            path = self.write('song.mp3')
            snapshot = library.scan(self.root)
            if replace:
                path.unlink()
            path.write_bytes(b'different song with a different size')
            with self.assertRaises(OSError):
                library.delete_song(snapshot, snapshot.entries[0])
            self.assertTrue(path.exists())

    def test_replacement_drive_is_not_deleted(self):
        path = self.write('song.mp3')
        snapshot = library.scan(self.root)
        with patch.object(library, 'identity', return_value=('another drive',)):
            with self.assertRaises(OSError):
                library.delete_song(snapshot, snapshot.entries[0])
        self.assertTrue(path.exists())

    def test_folder_non_audio_and_path_traversal_are_refused(self):
        path = self.write('Folder/song.mp3')
        snapshot = library.scan(self.root)
        for entry in [snapshot.entries[0], library.Entry(('..', 'outside.mp3'), 'outside'), library.Entry(('notes.txt',), 'notes')]:
            with self.assertRaises(ValueError):
                library.delete_song(snapshot, entry)
        self.assertTrue(path.exists())

    def test_replaced_folder_link_is_not_followed_during_deletion(self):
        self.write('Folder/song.mp3')
        snapshot = library.scan(self.root)
        entry = library.find_entry(snapshot.entries, ('Folder', 'song.mp3'))
        (self.root / 'Folder').rename(self.root / 'Original')
        outside = self.write('Outside/song.mp3', b'other audio')
        try:
            os.symlink(self.root / 'Outside', self.root / 'Folder', target_is_directory=True)
        except OSError:
            self.skipTest('Creating links is unavailable')
        with self.assertRaises(OSError):
            library.delete_song(snapshot, entry)
        self.assertTrue(outside.exists())
        self.assertTrue((self.root / 'Original/song.mp3').exists())
