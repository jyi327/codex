USB design demo source
======================

This renders a simulated interface and never accesses YouTube or USB devices. The USB browser is proposed, not implemented in the current EXE.

Requires Python, Pillow, FFmpeg, and Noto Sans CJK fonts at the paths in the script. Run `python docs/demo/render_usb_design_demo.py /tmp/usb-design-demo` from the repository root. The output is a silent MP4 with burned-in Chinese subtitles, an SRT, a timeline, and review frames.
