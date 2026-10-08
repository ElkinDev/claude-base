#!/usr/bin/env python3
"""Clear what a file says about the program, the person or the machine that made it, before it is shared.

The rule it serves has stood since the kit's first commit (project-template/CLAUDE.md): a generated
Office document leaves with its core properties cleared. It covers every kind the house publishes:

  .docx .xlsx .pptx .docm .xlsm .pptm  docProps/core.xml (creator, lastModifiedBy, title, subject,
        description, keywords, category, contentStatus, version) and docProps/app.xml (Application,
        AppVersion, Company, Manager, Template, HyperlinkBase). Every other part is copied byte for
        byte, in its order, with its own compression. No dependency.
  .pdf  the document information dictionary and the XMP metadata stream. Needs pypdf.
  .png  the tEXt, zTXt, iTXt, tIME and eXIf chunks. No dependency, no re-encode.
  .jpg .jpeg  the APP1 (EXIF, XMP), APP13 (IPTC) and COM segments. No dependency, no re-encode. A photo
        whose EXIF orientation is not 1 is refused and left as it is: without the tag it would show
        turned, so rotate it first.
  .mp4 .mov .m4a .m4v  the container and stream tags, the encoder tags, and on H.264 the SEI units
        where x264 writes its name and settings, by an ffmpeg stream copy (no re-encode). Needs
        ffmpeg and ffprobe on PATH. A name written inside the audio frames (ffmpeg's own AAC encoder does
        this unless the encode took -flags +bitexact) is out of a stream copy's reach: such a file is
        refused and left as it is.

    python meta-scrub.py <file or folder>...           # clear in place, one line per file changed
    python meta-scrub.py --check <file or folder>...   # change nothing, one line per file that still
                                                       # names something; exit 1 if any

A folder is walked whole, skipping .git, node_modules and .venv; other extensions are ignored. A file
is replaced only through a temporary file in its own folder, checked again before a rename, so a run
that stops halfway, or a clear that would leave a name behind, leaves the original. Exit codes: 0 done or clean, 1 --check found something, 2 a file could not be
read, written or handled (a missing tool, a refused photo), named on stderr.
"""
import os
import re
import shutil
import subprocess
import sys
import zipfile

OFFICE = {".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm"}
VIDEO = {".mp4", ".mov", ".m4a", ".m4v"}
KINDS = OFFICE | VIDEO | {".pdf", ".png", ".jpg", ".jpeg"}
SKIP_DIRS = {".git", "node_modules", ".venv"}

CORE_PART = "docProps/core.xml"
APP_PART = "docProps/app.xml"
CORE_FIELDS = ("creator", "lastModifiedBy", "title", "subject", "description", "keywords",
               "category", "contentStatus", "version")
APP_FIELDS = ("Application", "AppVersion", "Company", "Manager", "Template", "HyperlinkBase")

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_DROP = {b"tEXt", b"zTXt", b"iTXt", b"tIME", b"eXIf"}
JPEG_DROP = {0xE1: "APP1", 0xED: "APP13", 0xFE: "COM"}

# tags an ffmpeg stream copy keeps that name nothing: the file brand and the generic handler names
VIDEO_PLAIN_FORMAT_TAGS = {"major_brand", "minor_version", "compatible_brands"}
VIDEO_PLAIN_HANDLERS = {"", "VideoHandler", "SoundHandler"}
VIDEO_NOTES = (b"x264 - core", b"x265 (build", b"Lavf", b"Lavc")


class Refused(Exception):
    """The file is left as it is, for the reason given."""


def element(name):
    # an element by its local name, under any prefix: open and close, or self-closed
    return re.compile(r"<((?:[\w.-]+:)?%s)(\s[^>]*?)?(?:/>|>(.*?)</\1\s*>)" % re.escape(name), re.S)


def office_named(xml, fields):
    found = []
    for field in fields:
        for m in element(field).finditer(xml):
            if (m.group(3) or "").strip():
                found.append(field)
                break
    return found


def office_cleared(xml, fields):
    for field in fields:
        xml = element(field).sub(lambda m: "<%s%s/>" % (m.group(1), m.group(2) or ""), xml)
    return xml


def office_parts(path):
    with zipfile.ZipFile(path) as z:
        names = {i.filename for i in z.infolist()}
        return {part: z.read(part).decode("utf-8") for part in (CORE_PART, APP_PART) if part in names}


def office_check(path):
    parts = office_parts(path)
    return (office_named(parts.get(CORE_PART, ""), CORE_FIELDS)
            + office_named(parts.get(APP_PART, ""), APP_FIELDS))


def office_scrub(path, tmp):
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename == CORE_PART:
                data = office_cleared(data.decode("utf-8"), CORE_FIELDS).encode("utf-8")
            elif info.filename == APP_PART:
                data = office_cleared(data.decode("utf-8"), APP_FIELDS).encode("utf-8")
            zout.writestr(info, data, compress_type=info.compress_type)


def pypdf_module():
    try:
        import pypdf
    except ImportError:
        raise Refused("needs pypdf (pip install pypdf)")
    return pypdf


# keys that carry a generator or its private data: on the catalog (an /Info there is not standard, but
# some writers put one) and on each page
PDF_ROOT_KEYS = ("/Metadata", "/PieceInfo", "/Info")
PDF_PAGE_KEYS = ("/Metadata", "/PieceInfo")


def pdf_check(path):
    pypdf = pypdf_module()
    reader = pypdf.PdfReader(path)
    if reader.is_encrypted:
        raise Refused("encrypted PDF")
    found = ["info " + str(k).lstrip("/") for k, v in (reader.metadata or {}).items() if str(v).strip()]
    root = reader.trailer["/Root"]
    found += ["catalog " + k.lstrip("/") for k in PDF_ROOT_KEYS if k in root]
    pages = {k.lstrip("/") for page in reader.pages for k in PDF_PAGE_KEYS if k in page}
    return found + ["page " + k for k in sorted(pages)]


def pdf_scrub(path, tmp):
    pypdf = pypdf_module()
    reader = pypdf.PdfReader(path)
    if reader.is_encrypted:
        raise Refused("encrypted PDF")
    writer = pypdf.PdfWriter(clone_from=reader)
    writer.metadata = None  # no information dictionary at all, so pypdf adds no Producer of its own
    root = writer._root_object
    for key in PDF_ROOT_KEYS:
        if key in root:
            del root[key]
    for page in writer.pages:
        for key in PDF_PAGE_KEYS:
            if key in page:
                del page[key]
    # a cloned file keeps every object, the XMP stream just unlinked included, until the orphans go
    writer.compress_identical_objects(remove_identicals=False, remove_orphans=True)
    with open(tmp, "wb") as f:
        writer.write(f)


def png_chunks(data):
    if not data.startswith(PNG_SIGNATURE):
        raise Refused("not a PNG file")
    i = len(PNG_SIGNATURE)
    while i + 8 <= len(data):
        length = int.from_bytes(data[i:i + 4], "big")
        kind = data[i + 4:i + 8]
        end = i + 12 + length
        yield kind, data[i:end]
        i = end
        if kind == b"IEND":
            break


def png_check(path):
    with open(path, "rb") as f:
        data = f.read()
    return sorted({kind.decode("ascii") for kind, _ in png_chunks(data) if kind in PNG_DROP})


def png_scrub(path, tmp):
    with open(path, "rb") as f:
        data = f.read()
    kept = [chunk for kind, chunk in png_chunks(data) if kind not in PNG_DROP]
    with open(tmp, "wb") as f:
        f.write(PNG_SIGNATURE + b"".join(kept))


def jpeg_segments(data):
    """(marker, bytes) for each segment up to the scan; the scan and the rest come last with marker None."""
    if not data.startswith(b"\xff\xd8"):
        raise Refused("not a JPEG file")
    yield 0xD8, data[:2]
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            raise Refused("damaged JPEG at byte %d" % i)
        while i + 1 < len(data) and data[i + 1] == 0xFF:
            i += 1  # fill bytes
        marker = data[i + 1]
        if marker == 0xDA:
            yield None, data[i:]
            return
        if 0xD0 <= marker <= 0xD7 or marker in (0x01, 0xD8):
            yield marker, data[i:i + 2]
            i += 2
            continue
        length = int.from_bytes(data[i + 2:i + 4], "big")
        yield marker, data[i:i + 2 + length]
        i += 2 + length


def jpeg_orientation(segment):
    """The EXIF orientation of an APP1 segment, 1 when it carries none."""
    body = segment[4:]
    if not body.startswith(b"Exif\x00\x00"):
        return 1
    tiff = body[6:]
    order = "little" if tiff[:2] == b"II" else "big"
    ifd = int.from_bytes(tiff[4:8], order)
    count = int.from_bytes(tiff[ifd:ifd + 2], order)
    for n in range(count):
        entry = tiff[ifd + 2 + 12 * n:ifd + 14 + 12 * n]
        if int.from_bytes(entry[:2], order) == 0x0112:
            return int.from_bytes(entry[8:10], order)
    return 1


def jpeg_check(path):
    with open(path, "rb") as f:
        data = f.read()
    return sorted({JPEG_DROP[m] for m, _ in jpeg_segments(data) if m in JPEG_DROP})


def jpeg_scrub(path, tmp):
    with open(path, "rb") as f:
        data = f.read()
    segments = list(jpeg_segments(data))
    for marker, segment in segments:
        if marker == 0xE1 and jpeg_orientation(segment) != 1:
            raise Refused("EXIF orientation %d: rotate the photo first, the tag is all that turns it"
                          % jpeg_orientation(segment))
    with open(tmp, "wb") as f:
        f.write(b"".join(segment for marker, segment in segments if marker not in JPEG_DROP))


def tool(name):
    found = shutil.which(name)
    if not found:
        raise Refused("needs %s on PATH" % name)
    return found


def video_probe(path):
    import json
    out = subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries",
                          "format_tags:stream=index,codec_name:stream_tags", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def video_notes(path):
    found = set()
    tail = b""
    with open(path, "rb") as f:
        while True:
            block = f.read(8 << 20)
            if not block:
                break
            window = tail + block
            found.update(note for note in VIDEO_NOTES if note in window)
            tail = block[-32:]
    return sorted(note.decode("ascii").split(" ")[0] for note in found)


def video_check(path):
    probe = video_probe(path)
    found = ["format " + k for k in (probe.get("format", {}).get("tags") or {})
             if k not in VIDEO_PLAIN_FORMAT_TAGS]
    for stream in probe.get("streams", []):
        for k, v in (stream.get("tags") or {}).items():
            if k == "language" or (k == "handler_name" and v in VIDEO_PLAIN_HANDLERS):
                continue
            found.append("stream %s %s" % (stream.get("index"), k))
    return found + ["note " + n for n in video_notes(path) if not any(n in f for f in found)]


def video_scrub(path, tmp):
    probe = video_probe(path)
    command = [tool("ffmpeg"), "-v", "error", "-y", "-i", path, "-map", "0", "-map_metadata", "-1",
               "-map_chapters", "-1", "-c", "copy", "-fflags", "+bitexact", "-flags:v", "+bitexact",
               "-flags:a", "+bitexact"]
    for stream in probe.get("streams", []):
        index = stream.get("index")
        if stream.get("codec_name") == "h264":
            # NAL type 6 is SEI on H.264 only; x264 writes its name and settings there
            command += ["-bsf:%d" % index, "filter_units=remove_types=6"]
        command += ["-metadata:s:%d" % index, "handler_name=", "-metadata:s:%d" % index, "encoder="]
    command.append(tmp)
    done = subprocess.run(command, capture_output=True, text=True)
    if done.returncode != 0:
        raise Refused("ffmpeg failed: " + (done.stderr.strip().splitlines() or ["no message"])[-1])


HANDLERS = {".pdf": (pdf_check, pdf_scrub), ".png": (png_check, png_scrub),
            ".jpg": (jpeg_check, jpeg_scrub), ".jpeg": (jpeg_check, jpeg_scrub)}
HANDLERS.update({ext: (office_check, office_scrub) for ext in OFFICE})
HANDLERS.update({ext: (video_check, video_scrub) for ext in VIDEO})


def files(targets):
    for target in targets:
        if os.path.isdir(target):
            for root, dirs, names in os.walk(target):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                for name in sorted(names):
                    if os.path.splitext(name)[1].lower() in KINDS:
                        yield os.path.join(root, name)
        elif os.path.splitext(target)[1].lower() in KINDS:
            yield target
        elif not os.path.exists(target):
            raise Refused("%s: no such file or folder" % target)


def scrub(path):
    """Clear one file in place; returns the fields it named, [] when it was already clean."""
    check, clear = HANDLERS[os.path.splitext(path)[1].lower()]
    found = check(path)
    if not found:
        return []
    folder, name = os.path.split(os.path.abspath(path))
    stem, ext = os.path.splitext(name)
    tmp = os.path.join(folder, ".%s.scrub-tmp%s" % (stem, ext))  # ffmpeg reads the kind from the extension
    try:
        clear(path, tmp)
        left = check(tmp)
        if left:
            # ffmpeg's own AAC encoder, for one, writes its name inside the audio frames, which a stream
            # copy cannot reach: the file is left as it is and the producer is the place to fix it
            raise Refused("cleared %s but it would still name %s; produce it again without that (an ffmpeg"
                          " encode takes -flags +bitexact on every stream)" % (", ".join(found), ", ".join(left)))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return found


def main(argv):
    check_only = "--check" in argv
    targets = [a for a in argv if a != "--check"]
    if not targets or any(a.startswith("-") for a in targets):
        sys.stderr.write(__doc__)
        return 2
    named = failed = 0
    try:
        paths = list(files(targets))
    except Refused as e:
        sys.stderr.write("meta-scrub: %s\n" % e)
        return 2
    for path in paths:
        try:
            if check_only:
                check = HANDLERS[os.path.splitext(path)[1].lower()][0]
                found = check(path)
                if found:
                    named += 1
                    print("%s: names %s" % (path, ", ".join(found)))
            else:
                found = scrub(path)
                if found:
                    print("%s: cleared %s" % (path, ", ".join(found)))
        except (Refused, OSError, ValueError, zipfile.BadZipFile, subprocess.CalledProcessError) as e:
            failed += 1
            sys.stderr.write("meta-scrub: %s: %s\n" % (path, e))
        except Exception as e:  # a parser's own error on a damaged file: name it, go on with the rest
            failed += 1
            sys.stderr.write("meta-scrub: %s: %s: %s\n" % (path, type(e).__name__, e))
    if failed:
        return 2
    return 1 if named else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
