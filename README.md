# Kaleidoscope Online station engine

Cloud copy of the Kaleidoscope radio engine for blind listeners in South Africa and overseas.

Songs are streamed straight from the public GitHub release (no music stored here, no token needed),
so the container stays small and starts fast. The HTTP port comes from `$PORT`.

Endpoints:
- `/` landing page, `/stream` live mp3 stream
- `/np` now playing, `/library` song list, `/dj` station state
- `POST /ctl` remote control (pause, resume, skip, shuffle, program, playnext, upchunk, upend)
- `POST /upload?name=file.mp3` direct file upload
