USB design demo source
======================

This renders a simulated interface and never accesses YouTube or USB devices. The USB browser and automatic refresh are now implemented in the app; this video remains a simulated walkthrough.

Requires Python, Pillow, FFmpeg, and Noto Sans CJK fonts at the paths in the script. Run `python docs/demo/render_usb_design_demo.py /tmp/usb-design-demo` from the repository root. The output is a silent MP4 with burned-in Chinese instructions next to the focused control, a fixed full-window view with no camera movement, and slower character-by-character song-name entry, an SRT, a timeline, and review frames. A USB insertion illustration precedes drive detection. Click markers, captions, cursor arrival, and interface responses share an integer-frame timeline; rendering checks all 20 click events before encoding.

USB refresh behavior: while a USB is attached, refresh its visible song list automatically (about every 1.5 seconds), including changes made outside the app. Refresh immediately after each completed copy or confirmed deletion, keep the folder expansion/scroll position when possible, and discard stale scans if the drive disconnects or changes. The video shows song counts updating after deletion and each copied song; it does not narrate this background work.

The demo displays USB songs as a flat list, including songs from subfolders, with no folder expansion action.

Show a successful download before demonstrating CANCEL. After completion, explain DONE, click DOWNLOAD MORE, select a new song, clear it with CANCEL, and close with the window close control.
