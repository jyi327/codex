"""Exercise only files created by this check on an independently verified test VHD."""
from pathlib import Path
import os
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import usb_library

original_validate = usb_library.validate_file

def diagnose_validate(info, entry):
    try:
        original_validate(info, entry)
    except OSError as error:
        raise OSError(f'{error} Expected identity={entry.file_id}; observed={usb_library.fingerprint(info)}') from error

usb_library.validate_file = diagnose_validate
root = Path(sys.argv[1])
with tempfile.TemporaryDirectory(prefix='YouTubeToUSB-owned-fixture-', dir=root) as work:
    folder = Path(work)
    for name in ['song.mp3', '选择.mp3', 'Folder/小城故事.wav']:
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'owned audio fixture')
    keep = folder / 'keep.flac'
    keep.write_bytes(b'keep this fixture')
    for relative in [('song.mp3',), ('选择.mp3',), ('Folder', '小城故事.wav')]:
        snapshot = usb_library.scan(folder)
        entry = usb_library.find_entry(snapshot.entries, relative)
        usb_library.delete_song(snapshot, entry)
        assert not folder.joinpath(*relative).exists()
        assert keep.read_bytes() == b'keep this fixture'
    assert usb_library.scan(folder).count == 1
    print(f'{sys.argv[2]} root, Unicode and nested-song deletion passed.')
