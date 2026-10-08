#!/usr/bin/env python3
"""Clear what a file says about the program, the person or the machine that made it, before it is shared.

The rule it serves has stood since the kit's first commit (project-template/CLAUDE.md): a generated
Office document leaves with its core properties cleared. It covers every kind the house publishes:

  .docx .xlsx .pptx .docm .xlsm .pptm  docProps/core.xml (creator, lastModifiedBy, title, subject,
        description, keywords, category, contentStatus, version), docProps/app.xml (Application,
        AppVersion, Company, Manager, Template, HyperlinkBase), the custom properties part, and the
        saved folder path a workbook keeps (x15ac:absPath). Every other part is copied byte for byte,
        in its order, with its own compression. No dependency. A signed package is refused.
  .pdf  the document information dictionary and every XMP and PieceInfo entry, on the catalog, the
        pages, the embedded images and any other object. Needs pypdf. A signed or encrypted PDF is
        refused.
  .png  the tEXt, zTXt, iTXt, tIME, eXIf and caBX (content credentials) chunks. No dependency, no
        re-encode.
  .jpg .jpeg  the APP1 (EXIF, XMP), APP2 MPF, APP11 (content credentials), APP13 (IPTC) and COM
        segments, and any image a phone appends after the end of the picture. No dependency, no
        re-encode. A photo whose EXIF orientation is not 1 is refused and left as it is: without the
        tag it would show turned, so rotate it first.
  .mp4 .mov .m4a .m4v  the container and stream tags, the encoder tags, and on H.264 the SEI units
        where x264 writes its name and settings, by an ffmpeg stream copy (no re-encode). Needs
        ffmpeg and ffprobe on PATH. A name written inside the frames is out of a stream copy's
        reach (ffmpeg's own AAC encoder writes one unless the encode took -flags +bitexact, x265
        writes one unless it took -x265-params info=0): such a file is refused and left as it is.

Other media and document kinds (.webm, .mkv, .gif, .webp, .heic, .svg, .mp3, .doc and the like) are
not read: each one is named as not read and the run exits 2, so a check never passes a file it did not
open. Text, code and data files are ignored.

    python meta-scrub.py <file or folder>...           # clear in place, one line per file changed
    python meta-scrub.py --check <file or folder>...   # change nothing; one line per file that still
                                                       # names something or could not be read

A folder is walked whole, skipping .git, node_modules and .venv. A file is replaced only through a
temporary file in its own folder, checked again before a rename, so a run that stops halfway, or a
clear that would leave a name behind, leaves the original. Exit codes: 0 every file read is clean
(--check) or cleared; 1 --check found a name; 2 a file was not read, refused or failed, which wins
over 1. Under --check every line goes to stdout; a clear names its failures on stderr.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

OFFICE = {".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm"}
VIDEO = {".mp4", ".mov", ".m4a", ".m4v"}
KINDS = OFFICE | VIDEO | {".pdf", ".png", ".jpg", ".jpeg"}
# kinds that can carry a name too, which this tool does not clear: named, never passed in silence
NOT_READ = {".webm", ".mkv", ".avi", ".wmv", ".flv", ".gif", ".webp", ".heic", ".heif", ".avif", ".tif",
            ".tiff", ".bmp", ".svg", ".psd", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".aac", ".odt",
            ".ods", ".odp", ".doc", ".xls", ".ppt", ".rtf", ".epub"}
SKIP_DIRS = {".git", "node_modules", ".venv"}

CORE_PART = "docProps/core.xml"
APP_PART = "docProps/app.xml"
CUSTOM_PART = "docProps/custom.xml"
WORKBOOK_PART = "xl/workbook.xml"
CORE_FIELDS = ("creator", "lastModifiedBy", "title", "subject", "description", "keywords",
               "category", "contentStatus", "version")
APP_FIELDS = ("Application", "AppVersion", "Company", "Manager", "Template", "HyperlinkBase")
ABS_PATH = re.compile(r"<(?:[\w.-]+:)?absPath\b[^>]*?(?:/>|>.*?</(?:[\w.-]+:)?absPath\s*>)", re.S)
# Excel wraps the path in a markup-compatibility block, which goes whole so no empty choice is left
ABS_PATH_BLOCK = re.compile(r"<((?:[\w.-]+:)?AlternateContent)\b[^>]*>(?:(?!</\1\s*>).)*?absPath"
                            r"(?:(?!</\1\s*>).)*</\1\s*>", re.S)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_DROP = {b"tEXt", b"zTXt", b"iTXt", b"tIME", b"eXIf", b"caBX"}
JPEG_DROP = {0xE1: "APP1", 0xEB: "APP11", 0xED: "APP13", 0xFE: "COM"}

# keys that carry a generator or its private data: on the catalog (an /Info there is not standard, but
# some writers put one) and on any other object
PDF_ROOT_KEYS = ("/Metadata", "/PieceInfo", "/Info")
PDF_OBJECT_KEYS = ("/Metadata", "/PieceInfo")

# tags an ffmpeg stream copy keeps that name nothing: the file brand and the generic handler names
VIDEO_PLAIN_FORMAT_TAGS = {"major_brand", "minor_version", "compatible_brands"}
VIDEO_PLAIN_HANDLERS = {"", "VideoHandler", "SoundHandler"}
# names written inside the streams or in boxes a stream copy drops, matched whole so a frame's random
# bytes do not read as one: a versioned ffmpeg name, the x264 and x265 notes, a C2PA manifest box
VIDEO_NOTES = {"x264": re.compile(rb"x264 - core"), "x265": re.compile(rb"x265 \(build"),
               "Lavf": re.compile(rb"Lavf\d+\.\d+\.\d+"), "Lavc": re.compile(rb"Lavc\d+\.\d+\.\d+"),
               "c2pa": re.compile(rb"jumdc2pa")}
REMEDIES = {"Lavc": "an ffmpeg encode takes -flags +bitexact on every stream",
            "Lavf": "an ffmpeg encode takes -fflags +bitexact",
            "x265": "an x265 encode takes -x265-params info=0",
            "x264": "an x264 encode leaves it in its SEI note, which a clear removes on H.264 only"}


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


def office_open(path):
    z = zipfile.ZipFile(path)
    names = {i.filename for i in z.infolist()}
    if any(n.startswith("_xmlsignatures/") for n in names):
        z.close()
        raise Refused("signed document: clearing it breaks the signature, so clear it before it is signed")
    return z, names


def office_check(path):
    z, names = office_open(path)
    with z:
        part = lambda p: z.read(p).decode("utf-8") if p in names else ""
        found = office_named(part(CORE_PART), CORE_FIELDS) + office_named(part(APP_PART), APP_FIELDS)
        if CUSTOM_PART in names:
            found.append("custom properties")
        if ABS_PATH.search(part(WORKBOOK_PART)):
            found.append("saved folder path")
    return found


def office_scrub(path, tmp):
    z, names = office_open(path)
    with z as zin, zipfile.ZipFile(tmp, "w") as zout:
        for info in zin.infolist():
            name = info.filename
            if name == CUSTOM_PART:
                continue
            data = zin.read(info)
            if name == CORE_PART:
                data = office_cleared(data.decode("utf-8"), CORE_FIELDS).encode("utf-8")
            elif name == APP_PART:
                data = office_cleared(data.decode("utf-8"), APP_FIELDS).encode("utf-8")
            elif name == WORKBOOK_PART:
                data = ABS_PATH.sub("", ABS_PATH_BLOCK.sub("", data.decode("utf-8"))).encode("utf-8")
            elif CUSTOM_PART in names and name in ("_rels/.rels", "[Content_Types].xml"):
                # the custom part goes, so its relationship and its content type go with it
                xml = data.decode("utf-8")
                xml = re.sub(r"<Relationship\b[^>]*Target=[\"']/?docProps/custom\.xml[\"'][^>]*/>", "", xml)
                xml = re.sub(r"<Override\b[^>]*PartName=[\"']/docProps/custom\.xml[\"'][^>]*/>", "", xml)
                data = xml.encode("utf-8")
            zout.writestr(info, data, compress_type=info.compress_type)


def pypdf_module():
    try:
        import pypdf
    except ImportError:
        raise Refused("needs pypdf (pip install pypdf)")
    return pypdf


def pdf_signed(root):
    """True when the PDF carries a signature: the signatures flag, a permissions entry, or a filled
    signature field (a blank one to be signed later is no signature yet)."""
    if "/Perms" in root:
        return True
    form = root.get("/AcroForm")
    if form is None:
        return False
    form = form.get_object()
    if int(form.get("/SigFlags", 0)) & 1:
        return True
    fields = list(form.get("/Fields") or [])
    for _ in range(100000):
        if not fields:
            break
        field = fields.pop().get_object()
        if field.get("/FT") == "/Sig" and "/V" in field:
            return True
        fields.extend(field.get("/Kids") or [])
    return False


def pdf_open(path):
    pypdf = pypdf_module()
    reader = pypdf.PdfReader(path)
    if reader.is_encrypted:
        raise Refused("encrypted PDF")
    if pdf_signed(reader.trailer["/Root"].get_object()):
        raise Refused("signed PDF: clearing it breaks the signature, so clear it before it is signed")
    writer = pypdf.PdfWriter(clone_from=reader)
    return reader, writer


def pdf_objects(writer):
    for obj in writer._objects:
        if obj is not None and hasattr(obj, "keys"):
            yield obj


def pdf_check(path):
    reader, writer = pdf_open(path)
    found = ["info " + str(k).lstrip("/") for k, v in (reader.metadata or {}).items() if str(v).strip()]
    root = writer._root_object
    found += ["catalog " + k.lstrip("/") for k in PDF_ROOT_KEYS if k in root]
    others = {k.lstrip("/") for obj in pdf_objects(writer) if obj is not root
              for k in PDF_OBJECT_KEYS if k in obj}
    return found + ["object " + k for k in sorted(others)]


def pdf_scrub(path, tmp):
    _, writer = pdf_open(path)
    writer.metadata = None  # no information dictionary at all, so pypdf adds no Producer of its own
    root = writer._root_object
    for key in PDF_ROOT_KEYS:
        if key in root:
            del root[key]
    for obj in pdf_objects(writer):
        for key in PDF_OBJECT_KEYS:
            if key in obj:
                del obj[key]
    # a cloned file keeps every object, the XMP streams just unlinked included, until they go
    writer.compress_identical_objects(remove_duplicates=False, remove_unreferenced=True)
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


def jpeg_parts(data):
    """(the picture's segments as (marker, bytes) through its end marker, what follows the picture).

    Every marker is walked, the ones between progressive scans included; a scan segment runs on through
    its entropy-coded bytes, where FF is only ever followed by 00, a restart marker or another FF."""
    if not data.startswith(b"\xff\xd8"):
        raise Refused("not a JPEG file")
    segments = [(0xD8, data[:2])]
    i, n = 2, len(data)
    while i + 1 < n:
        if data[i] != 0xFF:
            raise Refused("damaged JPEG at byte %d" % i)
        while i + 1 < n and data[i + 1] == 0xFF:
            i += 1  # fill bytes
        marker = data[i + 1]
        if marker == 0xD9:
            segments.append((marker, data[i:i + 2]))
            return segments, data[i + 2:]
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            segments.append((marker, data[i:i + 2]))
            i += 2
            continue
        end = i + 2 + int.from_bytes(data[i + 2:i + 4], "big")
        if marker == 0xDA:
            while True:
                end = data.find(b"\xff", end)
                if end < 0 or end + 1 >= n:
                    raise Refused("JPEG with no end of image")
                follow = data[end + 1]
                if follow == 0xFF:
                    end += 1
                elif follow == 0x00 or 0xD0 <= follow <= 0xD7:
                    end += 2
                else:
                    break
        segments.append((marker, data[i:end]))
        i = end
    raise Refused("JPEG with no end of image")


def jpeg_dropped(marker, segment):
    """The name of a segment that goes, None for one that stays."""
    if marker in JPEG_DROP:
        return JPEG_DROP[marker]
    if marker == 0xE2 and segment[4:8] == b"MPF\x00":
        return "APP2 MPF"  # the index of the images a phone appends, which go too
    return None


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
    segments, after = jpeg_parts(data)
    found = {jpeg_dropped(m, s) for m, s in segments} - {None}
    if after.rstrip(b"\x00"):
        found.add("data after the picture")
    return sorted(found)


def jpeg_scrub(path, tmp):
    with open(path, "rb") as f:
        data = f.read()
    segments, _ = jpeg_parts(data)
    for marker, segment in segments:
        if marker == 0xE1 and jpeg_orientation(segment) != 1:
            raise Refused("EXIF orientation %d: rotate the photo first, the tag is all that turns it"
                          % jpeg_orientation(segment))
    with open(tmp, "wb") as f:
        f.write(b"".join(s for m, s in segments if jpeg_dropped(m, s) is None))


def tool(name):
    found = shutil.which(name)
    if not found:
        raise Refused("needs %s on PATH" % name)
    return found


def video_probe(path):
    out = subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries",
                          "format_tags:stream=index,codec_name:stream_tags", "-of", "json", path],
                         capture_output=True, encoding="utf-8", errors="replace", check=True).stdout
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
            found.update(name for name, note in VIDEO_NOTES.items() if note.search(window))
            tail = block[-32:]
    return sorted(found)


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
    done = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise Refused("ffmpeg failed: " + (done.stderr.strip().splitlines() or ["no message"])[-1])


HANDLERS = {".pdf": (pdf_check, pdf_scrub), ".png": (png_check, png_scrub),
            ".jpg": (jpeg_check, jpeg_scrub), ".jpeg": (jpeg_check, jpeg_scrub)}
HANDLERS.update({ext: (office_check, office_scrub) for ext in OFFICE})
HANDLERS.update({ext: (video_check, video_scrub) for ext in VIDEO})


def kind(path):
    return os.path.splitext(path)[1].lower()


def files(targets):
    for target in targets:
        if os.path.isdir(target):
            for root, dirs, names in os.walk(target):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                for name in sorted(names):
                    if kind(name) in KINDS or kind(name) in NOT_READ:
                        yield os.path.join(root, name)
        elif os.path.isfile(target):
            yield target  # named directly: read, or named as not read, whatever its kind
        else:
            raise Refused("%s: no such file or folder" % target)


def remedy(left):
    said = [REMEDIES[k] for k in REMEDIES if any(k in item for item in left)]
    return "; ".join(said) if said else "produce it again without that"


def scrub(path):
    """Clear one file in place; returns the fields it named, [] when it was already clean."""
    check, clear = HANDLERS[kind(path)]
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
            raise Refused("cleared %s but it would still name %s; produce it again without that (%s)"
                          % (", ".join(found), ", ".join(left), remedy(left)))
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

    def fail(path, why):
        # under --check the line goes to stdout too, so a check never reads clean on a file it skipped
        if check_only:
            print("%s: not read, %s" % (path, why))
        else:
            sys.stderr.write("meta-scrub: %s: %s\n" % (path, why))

    for path in paths:
        if kind(path) not in HANDLERS:
            failed += 1
            fail(path, "meta-scrub does not handle %s files" % (kind(path) or "extensionless"))
            continue
        try:
            if check_only:
                found = HANDLERS[kind(path)][0](path)
                if found:
                    named += 1
                    print("%s: names %s" % (path, ", ".join(found)))
            else:
                found = scrub(path)
                if found:
                    print("%s: cleared %s" % (path, ", ".join(found)))
        except (Refused, OSError, ValueError, zipfile.BadZipFile, subprocess.CalledProcessError) as e:
            failed += 1
            fail(path, e)
        except Exception as e:  # a parser's own error on a damaged file: name it, go on with the rest
            failed += 1
            fail(path, "%s: %s" % (type(e).__name__, e))
    if failed:
        return 2
    return 1 if named else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
