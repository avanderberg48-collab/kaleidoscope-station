#!/usr/bin/env python3
"""Put the station's schedule, programmes, breaks and playlists in place on a
fresh cloud container (all files come from the public radio-meta release)."""
import os, tarfile, urllib.request

BASE = "https://github.com/avanderberg48-collab/mandy-radio-library/releases/download/radio-meta/"
RADIO = os.environ.get("RADIO_DIR", "/app/radio")
MAP = {
    "meta_sched.json": "sched.json",
    "meta_programs.json": "programs.json",
    "meta_break.json": "break.json",
    "meta_story.json": "story.json",
}


def main():
    os.makedirs(RADIO, exist_ok=True)
    for src, dst in MAP.items():
        dest = os.path.join(RADIO, dst)
        try:
            data = urllib.request.urlopen(BASE + src, timeout=40).read()
            if len(data) > 2:
                with open(dest, "wb") as f:
                    f.write(data)
                print("restored", dst, len(data), flush=True)
        except Exception as e:
            print("skip", dst, str(e)[:80], flush=True)
    try:
        tgz = "/tmp/playlists.tar.gz"
        with urllib.request.urlopen(BASE + "meta_playlists.tar.gz", timeout=40) as r, open(tgz, "wb") as f:
            f.write(r.read())
        with tarfile.open(tgz) as t:
            t.extractall(RADIO + "/playlists")
        print("restored playlists", flush=True)
    except Exception as e:
        print("playlists skip", str(e)[:80], flush=True)


if __name__ == "__main__":
    main()
