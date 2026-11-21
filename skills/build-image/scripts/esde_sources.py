#!/usr/bin/env python3
"""Keep a copy of the Ubuntu source packages the ES-DE AppImage's libraries owe (#276, #287, COMPLIANCE.md section 6).

The ES-DE AppImage bundles Ubuntu 24.04 libraries; each copyleft one (owes = source in licences/esde-libraries.tsv)
needs its exact source next to the image. Launchpad keeps every published Ubuntu source version, so this fetches
each (source, source_version) once from it.

    esde_sources.py --libraries esde-libraries.tsv --out DIR [--apply]

Dry run (the default) asks Launchpad's API for each package's file list (a few KB each) and prints one line per
source package with its size, and the total; it writes nothing. --apply downloads every file into
DIR/<source>_<version>/ (a .part file, renamed only when its sha256 and size match; a matching file already there is
kept), checks the .tar files against the .dsc's Checksums-Sha256 too (a .dsc without them, or no .dsc, fails), then
writes DIR/SOURCES.tsv (source, version, file, size, sha256, url) and DIR/SHA256SUMS.

How each file is verified: Launchpad's API (https) gives each file's size and sha256 (sourceFileUrls include_meta);
the download must match them, and every non-.dsc file must also match the .dsc. Exit: 0 OK, 1 a package has no
Launchpad publication or a file failed its check, 2 refused (bad input).
Not legal advice; I'm not a lawyer.
"""
import argparse
import json
import os
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rpi_sources as R  # noqa: E402  (the shared download/verify helpers: fetch_file, deb822, sha256_list)

API = "https://api.launchpad.net/1.0/ubuntu/+archive/primary"
SERIES = "noble"          # the ES-DE 3.5.0 AppImage's libraries are Ubuntu 24.04 (esde-libraries.tsv header)
Refused = R.Refused


def read_libraries(path):
    """(source, source_version) of every row that owes source, sorted, unique."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    head = next((ln for ln in lines if ln.startswith("# file\t")), None)
    if head is None:
        raise Refused("%s: no '# file<TAB>...' header (is it licences/esde-libraries.tsv?)" % path)
    cols = head.lstrip("# ").split("\t")
    for need in ("source", "source_version", "owes"):
        if need not in cols:
            raise Refused("%s: no %s column" % (path, need))
    out = set()
    for n, ln in enumerate(lines, 1):
        if not ln.strip() or ln.startswith("#"):
            continue
        row = dict(zip(cols, ln.split("\t")))
        if row.get("owes") != "source":
            continue
        src, ver = row.get("source", ""), row.get("source_version", "")
        if not R.SRC_NAME.match(src) or not R.VERSION.match(ver):
            raise Refused("%s:%d: source %r version %r is not a Debian source name and version" % (path, n, src, ver))
        out.add((src, ver))
    return sorted(out)


def files_of(opener, src, ver):
    """[(url, size, sha256)] of a source publication on Launchpad, or None when Launchpad has none."""
    q = urllib.parse.urlencode({"ws.op": "getPublishedSources", "source_name": src, "version": ver,
                                "exact_match": "true"})
    pubs = json.loads(R.read_all(opener, "%s?%s" % (API, q)).decode()).get("entries", [])
    pubs = [p for p in pubs if p.get("source_package_version") in (None, ver)]
    if not pubs:
        return None
    pubs.sort(key=lambda p: not str(p.get("distro_series_link", "")).endswith("/" + SERIES))
    meta = json.loads(R.read_all(opener, pubs[0]["self_link"] + "?ws.op=sourceFileUrls&include_meta=true").decode())
    out = []
    for m in meta:
        if not (isinstance(m, dict) and str(m.get("url", "")).startswith("https://") and R.SHA.match(m.get("sha256", ""))
                and isinstance(m.get("size"), int)):
            raise Refused("%s %s: Launchpad gave a file entry without https url, size and sha256: %r" % (src, ver, m))
        out.append((m["url"], m["size"], m["sha256"]))
    return out


def run(pairs, out, opener=None, apply=False):
    opener = opener or R.default_opener
    plan, missing = [], []
    for src, ver in pairs:
        files = files_of(opener, src, ver)
        if not files:
            missing.append("%s %s" % (src, ver))
            R.say("esde-sources: MISSING %s %s (no Launchpad publication)" % (src, ver))
            continue
        plan.append((src, ver, files))
        R.say("esde-sources: %s %s: %d files, %s" % (src, ver, len(files), R.human(sum(f[1] for f in files))))
    total = sum(f[1] for _s, _v, fs in plan for f in fs)
    R.say("esde-sources: %d source packages, %s total (%d missing)" % (len(plan), R.human(total), len(missing)))
    if not apply:
        R.say("esde-sources: dry run, nothing downloaded (--apply fetches into %s)" % out)
        return 1 if missing else 0
    rows, failed = [], []
    for src, ver, files in plan:
        d = os.path.join(out, "%s_%s" % (src, R.no_epoch(ver)))
        os.makedirs(d, exist_ok=True)
        dsc_sums = {}
        for url, size, sha in sorted(files, key=lambda f: not f[0].endswith(".dsc")):   # the .dsc first
            name = os.path.basename(urllib.parse.urlparse(url).path)
            if "/" in name or name in ("", ".", ".."):
                raise Refused("bad file name in %s" % url)
            try:
                R.fetch_file(opener, url, os.path.join(d, name), sha, size)
                if name.endswith(".dsc"):
                    with open(os.path.join(d, name), encoding="utf-8", errors="replace") as f:
                        para = (R.deb822(f.read()) or [{}])[0]
                    dsc_sums = {n: (s, z) for s, z, n in R.sha256_list(para.get("Checksums-Sha256"))}
                    if not dsc_sums:
                        raise ValueError("%s: no Checksums-Sha256 in the .dsc" % name)
                elif not dsc_sums:
                    raise ValueError("%s: no .dsc checksums to check it against" % name)
                elif dsc_sums.get(name) != (sha, size):
                    raise ValueError("%s: not the file its .dsc names" % name)
            except (ValueError, OSError) as exc:
                failed.append(str(exc))
                R.say("esde-sources: FAIL %s" % exc)
                continue
            rows.append((src, ver, "%s/%s" % (os.path.basename(d), name), size, sha, url))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "SOURCES.tsv"), "w", encoding="utf-8") as f:
        f.write("# source\tversion\tfile\tsize\tsha256\turl\n")
        f.writelines("%s\t%s\t%s\t%d\t%s\t%s\n" % r for r in rows)
    with open(os.path.join(out, "SHA256SUMS"), "w", encoding="utf-8") as f:
        f.writelines("%s  %s\n" % (r[4], r[2]) for r in rows)
    ok = not failed and not missing
    R.say("esde-sources: %s, %d files in %s, %d failed, %d missing" % ("OK" if ok else "FAIL", len(rows), out,
                                                                       len(failed), len(missing)))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--libraries", required=True, help="licences/esde-libraries.tsv (or the image's es-de/libraries.tsv)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--apply", action="store_true", help="download (default: dry run)")
    a = ap.parse_args(argv)
    try:
        return run(read_libraries(a.libraries), a.out, apply=a.apply)
    except (Refused, OSError, ValueError) as exc:
        print("esde-sources: REFUSED: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
