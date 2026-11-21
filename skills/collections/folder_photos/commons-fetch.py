"""Fetch 800px-wide Commons thumbnails for a fixed list into an EMPTY dir; record license + author + source.
Polite (2 s/req, 429 backoff), https only, <= 5 MB, PNG/JPEG by magic bytes; files are only ever read as images."""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "cabinet-art/1.0 (personal, non-commercial)"}
OUT = sys.argv[1]
WANT = {  # cabinet system dir -> Commons file
    "nes": "File:NES-Console-Set.png", "snes": "File:SNES-Mod1-Console-Set.png",
    "genesis": "File:Sega-Genesis-Mod1-Set.png", "segacd": "File:Sega-CD-Model1-Set.jpg",
    "saturn": "File:Sega-Saturn-Console-Set-Mk1.png", "dreamcast": "File:Dreamcast-Console-Set.png",
    "n64": "File:N64-Console-Set.png", "psx": "File:PSX-Console-wController.png",
    "pcengine": "File:PC-Engine-Console-Set.png", "neogeo": "File:Neo-Geo-AES-Console-Set.png",
    "cps1-candidate": "File:AN-CPS-1 MEW.jpg", "cps2-candidate": "File:New CPS 2.jpg",
}
os.makedirs(OUT, exist_ok=True)
assert not [f for f in os.listdir(OUT) if not f.startswith(".")], "OUT must be empty"


def get(url, raw=False):
    assert url.startswith("https://"), url
    for i in range(5):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60)
            data = r.read(5 * 1024 * 1024 + 1)
            assert len(data) <= 5 * 1024 * 1024, "too big"
            return data if raw else json.loads(data)
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(10 * (i + 1))
    raise SystemExit("rate limited")


credits = []
for key, title in WANT.items():
    time.sleep(2)
    d = get("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "titles": title, "prop": "imageinfo",
        "iiprop": "url|extmetadata", "iiurlwidth": 800}))
    p = list(d["query"]["pages"].values())[0]
    ii = p["imageinfo"][0]
    m = ii["extmetadata"]
    time.sleep(2)
    data = get(ii["thumburl"], raw=True)
    ext = ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg" if data[:3] == b"\xff\xd8\xff" else None
    if not ext:
        print("REFUSED (not PNG/JPEG):", key)
        continue
    open(os.path.join(OUT, key + ext), "wb").write(data)
    lic = m.get("LicenseShortName", {}).get("value", "?")
    credits.append("%s\t%s\t%s\t%s" % (key + ext, title, lic, ii["descriptionurl"]))
    print("ok %-15s %-5s %7d B  %s" % (key, ext, len(data), lic))
open(os.path.join(OUT, "CREDITS.tsv"), "w").write("file\tcommons title\tlicense\tsource\n" + "\n".join(credits) + "\n")
