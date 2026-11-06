USB design demo source
======================

This renders a simulated interface and never accesses YouTube or USB devices. The USB browser is proposed, not implemented in the current EXE.

Requires Python, Pillow, FFmpeg, and Noto Sans CJK fonts at the paths in the script. Run `python docs/demo/render_usb_design_demo.py /tmp/usb-design-demo` from the repository root. The output is a silent MP4 with burned-in Chinese subtitles, an SRT, a timeline, and review frames.

USB freshness requirement for implementation: while a USB is attached, refresh its visible song list automatically (about every 1.5 seconds), including changes made outside the app. Refresh immediately after each completed copy or confirmed deletion, keep the folder expansion/scroll position when possible, and discard stale scans if the drive disconnects or changes. The video shows song counts updating after deletion and each copied song; it does not narrate this background work.
