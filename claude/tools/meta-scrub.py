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
  .webp  the EXIF, XMP and C2PA chunks, with the VP8X flags that announced them, and any data after
        the picture. No dependency, no re-encode.
  .avif  a still image (ftyp, one meta box, mdat): a handler name other than the generic ones, blanked
        in place so every offset holds. An Exif or XMP item, another item kind, an item name, a box
        outside the ones a still image uses (at the top level, in meta and among the item
        properties), a padding box that is not all zero bytes, an external data reference, or an
        encoder name in the bytes (Lavc, Lavf, x264, x265, libavif, libheif, libaom, c2pa) is named
        and refused, since dropping it would move every offset: encode it again without metadata. An
        image sequence (a moov box) is refused. No dependency.
  .ply  the comment and obj_info lines of the header (Gaussian splat and mesh files alike); the data is
        copied byte for byte. No dependency.
  .sog  a splat zip: asset.generator, asset.copyright and any extras in meta.json, the EXIF, XMP and
        C2PA chunks of its WebP images, and the zip comment. An entry other than meta.json or a WebP
        image is refused. No dependency.
  .spz  version 4: the reserved header bytes; extension records are named and refused, since dropping
        them would move every stream. A legacy gzip file loses the extra, file name and comment fields
        of its gzip header; a second gzip member is named and refused. No dependency.
  .svg  comments outside CDATA, style sheet comments, metadata, a desc that says what it was created
        with, and the Inkscape and Sodipodi attributes, elements and namespaces (dc, cc and rdf with them). An embedded raster image, an
        absolute path in a link or another editor's namespace is named and refused, and a clear that
        would not parse is refused. No dependency.
  .hdr  the comment lines, SOFTWARE and any variable outside the Radiance set in the header; the
        pixels are copied byte for byte. No dependency.
  .ktx2  the KTXwriter and KTXwriterScParams values, blanked in place so every offset holds. A key
        outside the KTX set is named and refused, since its name alone can carry one. No dependency.
  .glb  in the JSON chunk, asset.generator, asset.copyright, every extras and the KHR_xmp_json_ld
        packets; the embedded PNG, JPEG, WebP and KTX2 textures, cleared as above. A texture that keeps its
        length is written back in place; one that shrinks has the binary chunk packed again, which is
        refused when an extension such as EXT_meshopt_compression points into that chunk itself, so
        clear such a model before compressing it. Geometry, Draco data included, is copied byte for byte. A local path in any name or URI is
        named and refused, since clearing it would change the model. An embedded texture of another
        kind, an image in a data URI or a chunk it does not know is refused. No dependency.
  .mp4 .mov .m4a .m4v .webm .mkv  the container and stream tags, the encoder tags, and on H.264 the SEI units
        where x264 writes its name and settings, by an ffmpeg stream copy (no re-encode). Needs
        ffmpeg and ffprobe on PATH. A name written inside the frames is out of a stream copy's
        reach (ffmpeg's own AAC encoder writes one unless the encode took -flags +bitexact, x265
        writes one unless it took -x265-params info=0): such a file is refused and left as it is.
  .ttf .otf .woff .woff2  in each version record (name ID 5, every platform), what follows "Version" and its
        number (a build tool's name, such as ";gftools[0.9.28]"), cut; the tables a font tool leaves (FFTM,
        PfEd, TTFA, DSIG, Debg, the VTT sources TSI0 to TSIB, a meta table with tags beyond dlng and slng) and a
        WOFF's extended metadata and private blocks, dropped. The credit records (copyright, designer, licence
        and the like) stay, as the SIL Open Font License asks. A local path, the login or machine name or a
        line of the optional list ~/.claude/tools/meta-scrub-names.txt in any record or in a CFF table's
        strings (its Name and String INDEX, never its glyph code; a CFF2 table holds no strings and is not
        scanned), a font tool or AI program (Claude Code, claude.ai, Anthropic, OpenAI, ChatGPT, Copilot,
        never a bare Claude, which is also a designer's name) in a record other than a credit, a version record
        with no number, an image table and a table outside the OpenType set are named and refused, and a CFF
        table that does not parse is refused as damaged: produce it again from the upstream file. Every
        other table is copied byte for byte, and before the rename a proof reads the copy back and compares it
        with the original table by table (run alone: --font-proof). A WOFF2 needs node on PATH, whose built-in
        zlib does its Brotli; without it a .woff2 is not read. A WOFF2 keeps its head.checksumAdjustment as it
        was, since the decoder rebuilds glyf and loca and computes it again. A collection (.ttc, .otc) is not
        read.

Other media, document and model kinds (.gif, .heic, .heif, .tif, .mp3, .doc, .gltf, .obj, .fbx and
the like) are not read: each one is named as not read and the run exits 2, so a check never passes a file
it did not open.

    python meta-scrub.py <file or folder>...           # clear in place, one line per file changed
    python meta-scrub.py --check <file or folder>...   # change nothing; one line per file that still
                                                       # names something or could not be read
    python meta-scrub.py --font-proof <original> <copy>  # exit 0 when the copy is the original but for
                                                       # what a clear changes; 3 and PROOF REFUSED <tag> <field>

A folder is walked whole, skipping .git, node_modules and .venv. In a folder, a text, code or data file
(no control byte in its first 8 KB) is ignored, and a binary file of a kind the tool does not know (a
font collection, an archive, a model) is named as not read, the same as when it is named directly; UTF-16 text
holds NUL bytes, so it counts as binary. node, ffprobe and ffmpeg run only from a full path on PATH; one
found in the folder the tool is started from counts as missing. A file is replaced only through a
temporary file in its own folder, checked again before a rename, so a run that stops halfway, or a
clear that would leave a name behind, leaves the original. Exit codes: 0 every file read is clean
(--check) or cleared; 1 --check found a name; 2 a file was not read, refused or failed, which wins
over 1; 3 --font-proof refused the copy. Under --check every line goes to stdout; a clear names its
failures on stderr.
"""
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import xml.etree.ElementTree
import zipfile
import zlib

OFFICE = {".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm"}
VIDEO = {".mp4", ".mov", ".m4a", ".m4v", ".webm", ".mkv"}
FONTS = {".ttf", ".otf", ".woff", ".woff2"}
KINDS = OFFICE | VIDEO | FONTS | {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".avif", ".glb", ".ktx2", ".hdr", ".ply",
                                  ".sog", ".spz", ".svg"}
# kinds that can carry a name too, which this tool does not clear: named, never passed in silence
NOT_READ = {".avi", ".wmv", ".flv", ".gif", ".heic", ".heif", ".tif",
            ".tiff", ".bmp", ".psd", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".aac", ".odt",
            ".ods", ".odp", ".doc", ".xls", ".ppt", ".rtf", ".epub", ".gltf", ".obj", ".mtl", ".fbx",
            ".blend", ".usd", ".usda", ".usdc", ".usdz", ".dae", ".stl", ".3mf"}
# bytes that never occur in a text file: every control byte but tab, line feed, form feed, carriage
# return and escape
CONTROL = bytes(set(range(32)) - {9, 10, 12, 13, 27})
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
WEBP_DROP = {b"EXIF": "EXIF", b"XMP ": "XMP", b"C2PA": "C2PA"}
WEBP_FLAGS = 0x08 | 0x04  # the VP8X bits that announce EXIF and XMP

GLB_JSON = 0x4E4F534A
GLB_BIN = 0x004E4942
GLTF_XMP = "KHR_xmp_json_ld"
LOCAL_PATH = re.compile(r"\b[A-Za-z]:[\\/]|\\\\[^\\\s]+\\|/Users/|/home/|/mnt/|/Volumes/|~/")

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


def png_names(data):
    return sorted({kind.decode("ascii") for kind, _ in png_chunks(data) if kind in PNG_DROP})


def png_clean(data):
    return PNG_SIGNATURE + b"".join(chunk for kind, chunk in png_chunks(data) if kind not in PNG_DROP)


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


def jpeg_names(data):
    segments, after = jpeg_parts(data)
    found = {jpeg_dropped(m, s) for m, s in segments} - {None}
    if after.rstrip(b"\x00"):
        found.add("data after the picture")
    return sorted(found)


def jpeg_clean(data):
    segments, _ = jpeg_parts(data)
    for marker, segment in segments:
        if marker == 0xE1 and jpeg_orientation(segment) != 1:
            raise Refused("EXIF orientation %d: rotate the photo first, the tag is all that turns it"
                          % jpeg_orientation(segment))
    return b"".join(s for m, s in segments if jpeg_dropped(m, s) is None)


def webp_parts(data):
    """(the chunks as (fourcc, bytes with their pad byte), what follows the RIFF container)."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise Refused("not a WebP file")
    end = 8 + int.from_bytes(data[4:8], "little")
    if end > len(data):
        raise Refused("damaged WebP: shorter than its header says")
    chunks, i = [], 12
    while i + 8 <= end:
        size = int.from_bytes(data[i + 4:i + 8], "little")
        if i + 8 + size > end:
            raise Refused("damaged WebP chunk at byte %d" % i)
        chunks.append((data[i:i + 4], data[i:i + 8 + size] + (b"\x00" if size & 1 else b"")))
        i += 8 + size + (size & 1)
    return chunks, data[end:]


def webp_names(data):
    chunks, after = webp_parts(data)
    found = {WEBP_DROP[fourcc] for fourcc, _ in chunks if fourcc in WEBP_DROP}
    if after.rstrip(b"\x00"):
        found.add("data after the picture")
    return sorted(found)


def webp_clean(data):
    kept = []
    for fourcc, chunk in webp_parts(data)[0]:
        if fourcc == b"VP8X":
            if len(chunk) < 18:
                raise Refused("damaged WebP: a VP8X chunk shorter than its ten bytes")
            chunk = chunk[:8] + bytes([chunk[8] & ~WEBP_FLAGS & 0xFF]) + chunk[9:]
        if fourcc not in WEBP_DROP:
            kept.append(chunk)
    body = b"WEBP" + b"".join(kept)
    return b"RIFF" + len(body).to_bytes(4, "little") + body


# A still AVIF is read box by box, each box to its exact end, and its picture item unit by unit, each AV1
# unit to its exact end. A structure a still image may use but this reader does not parse (an item
# reference, an entity group, item data kept in meta, an auxiliary image such as alpha, a layered image, an
# ICC profile, configuration units in av1C) is refused as not read. What can name something is named: a
# brand outside the known ones, a handler name other than the generic ones, an item name, an Exif, XMP or
# other non-picture item, a box of any other type, padding that is not all zero bytes, bytes after the last
# field of a box, picture bytes outside the extents of the listed items, an AV1 metadata unit, an external
# data reference, and an encoder name in the bytes, matched whole so the picture's random bytes do not read
# as one. The compressed picture inside a sequence header or frame unit is searched only for those names,
# as a video stream is: text coded into it by hand would pass.
AVIF_PLAIN_HANDLERS = {"", "PictureHandler"}
AVIF_PLAIN_ITEM_NAMES = {"", "Color", "Image"}
AVIF_PADDING = {b"free", b"skip"}
AVIF_BRANDS = {b"avif", b"avis"}
AVIF_KNOWN_BRANDS = AVIF_BRANDS | {b"avio", b"mif1", b"mif2", b"msf1", b"miaf", b"MA1A", b"MA1B", b"iso8",
                                   b"tmap"}
AVIF_SINGLE_META = {b"hdlr", b"pitm", b"iloc", b"iinf", b"iprp", b"dinf"}
# AV1 units a still picture is made of: sequence header, temporal delimiter, frame header, tile group, frame
AV1_PICTURE_UNITS = {1, 2, 3, 4, 6}
AV1_METADATA, AV1_PADDING = 5, 15
AVIF_PICTURE_ITEMS = {"av01"}
AVIF_UNPARSED_META = {b"iref", b"grpl", b"idat"}
# the item properties of a still colour image, with the length of each body (None: read below)
AVIF_PROPERTIES = {b"ispe": 12, b"pixi": None, b"av1C": None, b"colr": None, b"irot": 1, b"imir": 1,
                   b"pasp": 8, b"clap": 32, b"clli": 4, b"mdcv": 24}
AVIF_UNPARSED_PROPERTIES = {b"auxC", b"a1op", b"lsel", b"a1lx", b"amve"}
AVIF_NOTES = dict(VIDEO_NOTES, libavif=re.compile(rb"libavif"), libheif=re.compile(rb"libheif"),
                  libaom=re.compile(rb"libaom"))


def isobmff_boxes(data, start, end, where):
    """(type, start, header length, end) of each box from start to end; a box that does not fit is refused."""
    boxes, i = [], start
    while i < end:
        if i + 8 > end:
            raise Refused("damaged %s: a box header cut at byte %d" % (where, i))
        size, box_type, header = int.from_bytes(data[i:i + 4], "big"), data[i + 4:i + 8], 8
        if size == 1:
            if i + 16 > end:
                raise Refused("damaged %s: a box header cut at byte %d" % (where, i))
            size, header = int.from_bytes(data[i + 8:i + 16], "big"), 16
        elif size == 0:
            size = end - i
        if size < header or i + size > end:
            raise Refused("damaged %s: box %r at byte %d" % (where, box_type.decode("latin-1"), i))
        boxes.append((box_type, i, header, i + size))
        i += size
    return boxes


def zero_terminated(data, start, end):
    """(the text up to the next zero byte, the offset after it), within end."""
    stop = data.find(b"\x00", start, end)
    stop = end if stop < 0 else stop
    return data[start:stop].decode("utf-8", "replace"), min(stop + 1, end)


def avif_uint(data, at, size, end, what):
    """(the big-endian number of size bytes at at, the offset after it); a field past end is refused."""
    if at + size > end:
        raise Refused("damaged AVIF: %s cut short" % what)
    return int.from_bytes(data[at:at + size], "big"), at + size


def avif_tail(data, at, end, what, found):
    """The bytes after the last field of a box may only be zero; anything else is named."""
    if at > end:
        raise Refused("damaged AVIF: %s longer than its box" % what)
    if data[at:end].strip(b"\x00"):
        found.add("bytes after the fields of " + what)


def avif_brands(data):
    """True when the file type box names AVIF as its major brand, or as a compatible one of a mif1 or msf1 file."""
    if len(data) < 16 or data[4:8] != b"ftyp":
        return False
    end = min(int.from_bytes(data[0:4], "big"), len(data))
    compatible = {data[i:i + 4] for i in range(16, end - 3, 4)}
    return data[8:12] in AVIF_BRANDS or (data[8:12] in (b"mif1", b"msf1") and bool(compatible & AVIF_BRANDS))


def avif_locations(data, body, end, found):
    """The file ranges of every item extent; an item kept anywhere but in this file's own bytes is not read."""
    version = data[body]
    if version > 2:
        raise Refused("an AVIF item location box of version %d, not read" % version)
    sizes, at = avif_uint(data, body + 4, 2, end, "iloc")
    offset_size, length_size, base_size = sizes >> 12, (sizes >> 8) & 15, (sizes >> 4) & 15
    index_size = sizes & 15 if version else 0
    if {offset_size, length_size, base_size, index_size} - {0, 4, 8}:
        raise Refused("damaged AVIF: an item location field that is not 0, 4 or 8 bytes long")
    count, at = avif_uint(data, at, 2 if version < 2 else 4, end, "iloc")
    ranges = []
    for _ in range(count):
        item, at = avif_uint(data, at, 2 if version < 2 else 4, end, "iloc item id")
        if version:
            method, at = avif_uint(data, at, 2, end, "iloc construction method")
            if method & 15:
                raise Refused("an AVIF item kept in item data or built from other items, not read")
        reference, at = avif_uint(data, at, 2, end, "iloc data reference")
        if reference:
            raise Refused("an AVIF item kept in another file, not read")
        base, at = avif_uint(data, at, base_size, end, "iloc base offset")
        extents, at = avif_uint(data, at, 2, end, "iloc extent count")
        for _ in range(extents):
            _, at = avif_uint(data, at, index_size, end, "iloc extent index")
            offset, at = avif_uint(data, at, offset_size, end, "iloc extent offset")
            length, at = avif_uint(data, at, length_size, end, "iloc extent length")
            if not length:
                raise Refused("an AVIF item extent that runs to the end of the file, not read")
            ranges.append((item, base + offset, base + offset + length))
    avif_tail(data, at, end, "iloc", found)
    return ranges


def avif_items(data, body, end, found):
    """The items as (id, type, name, content type); iinf holds its item entries and nothing else."""
    count, at = avif_uint(data, body + 4, 2 if data[body] == 0 else 4, end, "iinf")
    entries = isobmff_boxes(data, at, end, "AVIF iinf")
    if len(entries) != count or any(entry[0] != b"infe" for entry in entries):
        raise Refused("an AVIF item list that holds more than its %d item entries, not read" % count)
    items = []
    for _, start, header, entry_end in entries:
        at = start + header
        version = data[at]
        if version not in (2, 3):
            raise Refused("an AVIF item entry of version %d, not read" % version)
        item, at = avif_uint(data, at + 4, 2 if version == 2 else 4, entry_end, "item entry")
        at += 2  # the protection index
        item_type = data[at:at + 4].decode("latin-1")
        name, at = zero_terminated(data, at + 4, entry_end)
        content = ""
        if item_type in ("mime", "uri "):
            content, at = zero_terminated(data, at, entry_end)
            if item_type == "mime" and at < entry_end:
                at = zero_terminated(data, at, entry_end)[1]  # the content encoding
        avif_tail(data, at, entry_end, "an item entry", found)
        items.append((item, item_type, name, content))
    return items


def avif_properties(data, body, end, found):
    """iprp holds one ipco, whose properties are read to their ends, then its ipma boxes, read to theirs."""
    boxes = isobmff_boxes(data, body, end, "AVIF iprp")
    if not boxes or boxes[0][0] != b"ipco" or any(box[0] != b"ipma" for box in boxes[1:]):
        raise Refused("an AVIF property box that holds more than ipco and ipma, not read")
    _, start, header, ipco_end = boxes[0]
    for prop, prop_start, prop_header, prop_end in isobmff_boxes(data, start + header, ipco_end, "AVIF ipco"):
        prop_body, name = prop_start + prop_header, prop.decode("latin-1")
        if prop in AVIF_PADDING:
            avif_tail(data, prop_body, prop_end, "a padding box", found)
        elif prop in AVIF_UNPARSED_PROPERTIES:
            raise Refused("an AVIF image with the property %s (an auxiliary or layered image), not read" % name)
        elif prop not in AVIF_PROPERTIES:
            found.add("property box " + name)
        elif AVIF_PROPERTIES[prop] is not None:
            avif_tail(data, prop_body + AVIF_PROPERTIES[prop], prop_end, "property " + name, found)
        elif prop == b"av1C":
            if prop_end - prop_body != 4:
                raise Refused("an AVIF av1C box that holds configuration units, not read")
        elif prop == b"pixi":
            channels, at = avif_uint(data, prop_body + 4, 1, prop_end, "pixi")
            avif_tail(data, at + channels, prop_end, "property pixi", found)
        elif prop == b"colr":
            colour = data[prop_body:prop_body + 4]
            if colour != b"nclx":
                raise Refused("an AVIF colour box of type %r (an ICC profile), not read" % colour.decode("latin-1"))
            avif_tail(data, prop_body + 11, prop_end, "property colr", found)
    for _, ipma_start, ipma_header, ipma_end in boxes[1:]:
        at = ipma_start + ipma_header
        version, flags = data[at], int.from_bytes(data[at + 1:at + 4], "big")
        count, at = avif_uint(data, at + 4, 4, ipma_end, "ipma")
        for _ in range(count):
            _, at = avif_uint(data, at, 2 if version < 1 else 4, ipma_end, "ipma item id")
            links, at = avif_uint(data, at, 1, ipma_end, "ipma")
            at += links * (2 if flags & 1 else 1)
        avif_tail(data, at, ipma_end, "ipma", found)


def avif_references(data, body, end, found):
    """dinf holds one dref, whose entries may only point at this file itself."""
    boxes = isobmff_boxes(data, body, end, "AVIF dinf")
    if len(boxes) != 1 or boxes[0][0] != b"dref":
        raise Refused("an AVIF data information box that holds more than one dref, not read")
    _, start, header, dref_end = boxes[0]
    count, at = avif_uint(data, start + header + 4, 4, dref_end, "dref")
    entries = isobmff_boxes(data, at, dref_end, "AVIF dref")
    if len(entries) != count:
        raise Refused("damaged AVIF: a dref that holds %d entries and counts %d" % (len(entries), count))
    for entry_type, entry_start, entry_header, entry_end in entries:
        flags = int.from_bytes(data[entry_start + entry_header + 1:entry_start + entry_header + 4], "big")
        if entry_type != b"url " or not flags & 1 or entry_end != entry_start + entry_header + 4:
            found.add("data reference " + entry_type.decode("latin-1").strip())


def av1_units(data, found):
    """Walks the AV1 units of a picture item to its last byte; a unit that names something is named."""
    at = 0
    while at < len(data):
        header = data[at]
        unit, extension, sized = (header >> 3) & 15, (header >> 2) & 1, (header >> 1) & 1
        if header & 128 or not sized:
            raise Refused("an AVIF picture with an AV1 unit this reader cannot size, not read")
        at += 1 + extension
        size, shift = 0, 0
        while True:
            if at >= len(data) or shift > 49:
                raise Refused("damaged AVIF: an AV1 unit cut short")
            size |= (data[at] & 127) << shift
            at, shift = at + 1, shift + 7
            if not data[at - 1] & 128:
                break
        if at + size > len(data):
            raise Refused("damaged AVIF: an AV1 unit cut short")
        if unit == AV1_METADATA:
            found.add("AV1 metadata unit")
        elif unit == AV1_PADDING:
            if data[at:at + size].strip(b"\x00"):
                found.add("AV1 padding unit with content")
        elif unit not in AV1_PICTURE_UNITS:
            raise Refused("an AVIF picture with an AV1 unit of type %d, not read" % unit)
        at += size


def avif_parts(data):
    """(the span from the handler name to the end of its box, the items as (type, name, content type), what
    the boxes themselves name)."""
    if not avif_brands(data):
        raise Refused("not an AVIF file")
    found = set()
    top = isobmff_boxes(data, 0, len(data), "AVIF")
    ftyp_end = top[0][3]
    if (ftyp_end - 16) % 4:
        raise Refused("damaged AVIF: a file type box that is not a list of brands")
    if data[12:16].strip(b"\x00"):
        found.add("file type minor version")
    for at in [8] + list(range(16, ftyp_end, 4)):
        if data[at:at + 4] not in AVIF_KNOWN_BRANDS:
            found.add("file type brand " + data[at:at + 4].decode("latin-1"))
    metas = [box for box in top if box[0] == b"meta"]
    if b"moov" in [box[0] for box in top]:
        raise Refused("an AVIF image sequence (a moov box), not read")
    if len(metas) != 1:
        raise Refused("an AVIF with %d meta boxes, not read" % len(metas))
    for box_type, start, header, end in top[1:]:
        if box_type in AVIF_PADDING:
            avif_tail(data, start + header, end, "a padding box", found)
        elif box_type not in (b"meta", b"mdat"):
            found.add("top-level box " + box_type.decode("latin-1"))
    _, start, header, end = metas[0]
    name_span, items, ranges, seen = None, [], [], set()
    for box_type, box_start, box_header, box_end in isobmff_boxes(data, start + header + 4, end, "AVIF meta"):
        body = box_start + box_header
        if box_type in seen and box_type in AVIF_SINGLE_META:
            raise Refused("an AVIF meta box with two %s boxes, not read" % box_type.decode("latin-1"))
        seen.add(box_type)
        if box_type == b"hdlr":
            # version and flags, pre_defined, handler_type and three reserved words come before the name
            if body + 24 > box_end:
                raise Refused("damaged AVIF: a handler box shorter than its fields")
            name_span = (body + 24, box_end)
        elif box_type == b"pitm":
            avif_tail(data, body + 4 + (2 if data[body] == 0 else 4), box_end, "pitm", found)
        elif box_type == b"iloc":
            ranges = avif_locations(data, body, box_end, found)
        elif box_type == b"iinf":
            items = avif_items(data, body, box_end, found)
        elif box_type == b"iprp":
            avif_properties(data, body, box_end, found)
        elif box_type == b"dinf":
            avif_references(data, body, box_end, found)
        elif box_type in AVIF_PADDING:
            avif_tail(data, body, box_end, "a padding box", found)
        elif box_type in AVIF_UNPARSED_META:
            raise Refused("an AVIF image with a %s box (item references, groups or item data), not read"
                          % box_type.decode("latin-1"))
        else:
            found.add("meta box " + box_type.decode("latin-1"))
    missing = {b"hdlr", b"pitm", b"iloc", b"iinf", b"iprp"} - seen
    if missing:
        raise Refused("an AVIF meta box without %s, not read" % ", ".join(sorted(m.decode() for m in missing)))
    # every byte of the picture data belongs to an item extent; the extents lie inside the mdat boxes
    mdats = [(start + header, end) for box_type, start, header, end in top if box_type == b"mdat"]
    listed = {item for item, _, _, _ in items}
    for item, low, high in ranges:
        if not any(m_low <= low and high <= m_high for m_low, m_high in mdats):
            raise Refused("an AVIF item extent outside the picture data box, not read")
        if item not in listed:
            found.add("picture data of an item the item list does not name")
    for item, item_type, _, _ in items:
        if item_type in AVIF_PICTURE_ITEMS:
            av1_units(b"".join(data[low:high] for each, low, high in ranges if each == item), found)
    for m_low, m_high in mdats:
        at = m_low
        for low, high in sorted(r[1:] for r in ranges if m_low <= r[1] < m_high):
            if data[at:low].strip(b"\x00"):
                found.add("picture data bytes outside every item")
            at = max(at, high)
        if data[at:m_high].strip(b"\x00"):
            found.add("picture data bytes outside every item")
    return name_span, items, found


def avif_names(data):
    name_span, items, found = avif_parts(data)
    rest = data
    if name_span:
        name, after = zero_terminated(data, name_span[0], name_span[1])
        if name not in AVIF_PLAIN_HANDLERS:
            found.add("handler name " + name)
        if data[after:name_span[1]].strip(b"\x00"):
            found.add("handler name, bytes after it")
        # the handler name is cleared in place, so it is left out of the byte search
        rest = data[:name_span[0]] + bytes(name_span[1] - name_span[0]) + data[name_span[1]:]
    for _, item_type, name, content in items:
        if item_type == "Exif":
            found.add("Exif item")
        elif item_type == "mime":
            found.add("XMP item" if "xml" in content.lower() or "xmp" in content.lower() else "item " + content)
        elif item_type not in AVIF_PICTURE_ITEMS:
            found.add("item of type " + item_type.strip())
        if name not in AVIF_PLAIN_ITEM_NAMES:
            found.add("item name " + name)
    found.update(note for note, pattern in AVIF_NOTES.items() if pattern.search(rest))
    return sorted(found)


def avif_clean(data):
    left = [name for name in avif_names(data) if not name.startswith("handler name")]
    if left:
        raise Refused("%s: dropping it would move every offset; encode it again without metadata (ffmpeg "
                      "-map_metadata -1 -fflags +bitexact -flags +bitexact, avifenc --ignore-exif --ignore-xmp)"
                      % ", ".join(left))
    start, end = avif_parts(data)[0]
    return data[:start] + bytes(end - start) + data[end:]


KTX2_ID = b"\xabKTX 20\xbb\r\n\x1a\n"
# keys the KTX 2.0 spec defines that describe the texture; KTXwriter and KTXwriterScParams name the tool
# and its settings, and a key outside the KTX set can carry anything, in its name as much as its value
KTX2_KEEP = {"KTXcubemapIncomplete", "KTXorientation", "KTXglFormat", "KTXdxgiFormat__",
             "KTXmetalPixelFormat", "KTXswizzle", "KTXanimData", "KTXastcDecodeMode"}
KTX2_WRITER = {"KTXwriter", "KTXwriterScParams"}


def ktx2_values(data):
    """(key, start, end of its value) for each key/value entry."""
    if len(data) < 80 or not data.startswith(KTX2_ID):
        raise Refused("not a KTX2 file")
    start = int.from_bytes(data[56:60], "little")
    end = start + int.from_bytes(data[60:64], "little")
    if end > len(data):
        raise Refused("damaged KTX2: its key/value data runs past the file")
    entries, i = [], start
    while i + 4 <= end:
        length = int.from_bytes(data[i:i + 4], "little")
        body = i + 4
        if length == 0 or body + length > end:
            raise Refused("damaged KTX2 key/value entry at byte %d" % i)
        cut = data.find(b"\x00", body, body + length)
        if cut < 0:
            raise Refused("KTX2 key/value entry at byte %d has no key end" % i)
        entries.append((data[body:cut].decode("utf-8", "replace"), cut + 1, body + length))
        i = body + length + (-length % 4)
    return entries


def ktx2_names(data):
    found = set()
    for key, start, end in ktx2_values(data):
        if key in KTX2_WRITER and data[start:end].strip(b"\x00"):
            found.add(key)
        elif key not in KTX2_WRITER and key not in KTX2_KEEP:
            found.add("key %s" % key)  # a clear cannot drop an entry without moving every offset after it
    return sorted(found)


def ktx2_clean(data):
    out = bytearray(data)
    for key, start, end in ktx2_values(data):
        if key in KTX2_WRITER:
            out[start:end] = bytes(end - start)  # an empty string in the same bytes
    return bytes(out)


IMAGE_BYTES = {"image/png": (png_names, png_clean), "image/jpeg": (jpeg_names, jpeg_clean),
               "image/webp": (webp_names, webp_clean), "image/ktx2": (ktx2_names, ktx2_clean)}


def glb_parts(data):
    """(the JSON document, the binary chunk or None, what follows the file its header measures)."""
    if len(data) < 20 or data[:4] != b"glTF":
        raise Refused("not a binary glTF file")
    if int.from_bytes(data[4:8], "little") != 2:
        raise Refused("glTF version %d, only 2 is read" % int.from_bytes(data[4:8], "little"))
    total = int.from_bytes(data[8:12], "little")
    if total > len(data):
        raise Refused("damaged glTF: shorter than its header says")
    doc = binary = None
    i = 12
    while i + 8 <= total:
        length = int.from_bytes(data[i:i + 4], "little")
        chunk_type = int.from_bytes(data[i + 4:i + 8], "little")
        if i + 8 + length > total:
            raise Refused("damaged glTF chunk at byte %d" % i)
        body = data[i + 8:i + 8 + length]
        if chunk_type == GLB_JSON and doc is None:
            doc = json.loads(body.decode("utf-8"))
        elif chunk_type == GLB_BIN and doc is not None and binary is None:
            binary = body
        else:
            raise Refused("a glTF chunk it does not read (type 0x%08X)" % chunk_type)
        i += 8 + length
    if not isinstance(doc, dict):
        raise Refused("glTF with no JSON chunk")
    return doc, binary, data[total:]


def gltf_walk(node, where, found):
    """Name the extras, the XMP packets and any local path under a JSON node; the walk does not go into
    the extras or the XMP packets, which go whole."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "extras":
                found.add("extras")
                continue
            if key == "extensions" and isinstance(value, dict) and GLTF_XMP in value:
                found.add("XMP")
                value = {k: v for k, v in value.items() if k != GLTF_XMP}
            if key in ("extensionsUsed", "extensionsRequired") and isinstance(value, list) \
                    and GLTF_XMP in value:
                found.add("XMP")
            gltf_walk(value, "%s.%s" % (where, key) if where else key, found)
    elif isinstance(node, list):
        for n, value in enumerate(node):
            gltf_walk(value, "%s[%d]" % (where, n), found)
    elif isinstance(node, str) and LOCAL_PATH.search(node):
        found.add("a local path at %s" % where)


def gltf_strip(node):
    if isinstance(node, dict):
        node.pop("extras", None)
        extensions = node.get("extensions")
        if isinstance(extensions, dict) and GLTF_XMP in extensions:
            del extensions[GLTF_XMP]
            if not extensions:
                del node["extensions"]
        for key in ("extensionsUsed", "extensionsRequired"):
            if isinstance(node.get(key), list) and GLTF_XMP in node[key]:
                node[key] = [name for name in node[key] if name != GLTF_XMP]
                if not node[key]:
                    del node[key]
        for value in node.values():
            gltf_strip(value)
    elif isinstance(node, list):
        for value in node:
            gltf_strip(value)


def glb_images(doc, binary):
    """(view number, the texture's bytes, its two handlers) for each texture embedded in the file."""
    views, buffers = doc.get("bufferViews", []), doc.get("buffers", [])
    for n, image in enumerate(doc.get("images", [])):
        if str(image.get("uri", "")).startswith("data:"):
            raise Refused("image %d sits in a data URI, which is not read" % n)
        if "bufferView" not in image:
            continue  # a file beside the model, read on its own
        handlers = IMAGE_BYTES.get(image.get("mimeType"))
        if handlers is None:
            raise Refused("image %d is %s, which is not read" % (n, image.get("mimeType") or "untyped"))
        view = views[image["bufferView"]]
        if binary is None or view.get("buffer", 0) != 0 or "uri" in buffers[0]:
            raise Refused("image %d sits outside the file's binary chunk, which is not read" % n)
        start = view.get("byteOffset", 0)
        if start + view["byteLength"] > len(binary):
            raise Refused("image %d runs past the binary chunk" % n)
        yield n, image["bufferView"], binary[start:start + view["byteLength"]], handlers


def glb_names(data):
    doc, binary, after = glb_parts(data)
    found = set()
    asset = doc.get("asset", {})
    found.update("asset.%s" % field for field in ("generator", "copyright") if asset.get(field))
    gltf_walk(doc, "", found)
    for n, _, texture, (names, _) in glb_images(doc, binary):
        found.update("image %d %s" % (n, name) for name in names(texture))
    if after.rstrip(b"\x00"):
        found.add("data after the model")
    return sorted(found)


def gltf_addresses_buffer(node, top=True):
    """True when an object other than a buffer view itself names a buffer, the way EXT_meshopt_compression
    points a view's compressed bytes into the file's binary chunk; moving bytes would break it."""
    if isinstance(node, dict):
        if not top and "buffer" in node:
            return True
        return any(gltf_addresses_buffer(value, False) for value in node.values())
    if isinstance(node, list):
        return any(gltf_addresses_buffer(value, False) for value in node)
    return False


def glb_repack(doc, binary, textures):
    """The binary chunk again when a cleared texture shrank: every view of the file's buffer in its order,
    each one starting on a four byte boundary, which no accessor needs more than. Refused when an
    extension addresses the chunk itself, since those offsets would no longer match."""
    views = doc["bufferViews"]
    if any(gltf_addresses_buffer(view.get("extensions", {}), False) for view in views) \
            or any(gltf_addresses_buffer(buffer.get("extensions", {}), False) for buffer in doc.get("buffers", [])):
        raise Refused("an extension points into the binary chunk (EXT_meshopt_compression or the like), so "
                      "a texture that shrinks cannot be cleared after compression: clear the model first")
    order = sorted((n for n, view in enumerate(views) if view.get("buffer", 0) == 0),
                   key=lambda n: views[n].get("byteOffset", 0))
    if any("extensions" in views[n] for n in order):
        raise Refused("a buffer view carries an extension, so its bytes cannot be moved")
    spans = [(views[n].get("byteOffset", 0), views[n].get("byteOffset", 0) + views[n]["byteLength"])
             for n in order]
    if any(later[0] < earlier[1] for earlier, later in zip(spans, spans[1:])):
        raise Refused("buffer views overlap, so the textures cannot be cleared in place")
    out = bytearray()
    for n, (start, stop) in zip(order, spans):
        out += b"\x00" * (-len(out) % 4)
        part = textures.get(n, binary[start:stop])
        views[n]["byteOffset"] = len(out)
        views[n]["byteLength"] = len(part)
        out += part
    doc["buffers"][0]["byteLength"] = len(out)
    return bytes(out)


def glb_clean(data):
    doc, binary, _ = glb_parts(data)
    asset = doc.get("asset", {})
    for field in ("generator", "copyright"):
        asset.pop(field, None)
    gltf_strip(doc)
    textures = {view: (texture, clean(texture)) for _, view, texture, (names, clean) in glb_images(doc, binary)
                if names(texture)}
    if all(len(old) == len(new) for old, new in textures.values()):
        patched = bytearray(binary or b"")  # same length: written back where it was, no offset moves
        for view, (_, new) in textures.items():
            start = doc["bufferViews"][view].get("byteOffset", 0)
            patched[start:start + len(new)] = new
        binary = bytes(patched) if binary is not None else None
    else:
        binary = glb_repack(doc, binary, {view: new for view, (_, new) in textures.items()})
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 4)
    body = len(text).to_bytes(4, "little") + GLB_JSON.to_bytes(4, "little") + text
    if binary is not None:
        padded = binary + b"\x00" * (-len(binary) % 4)
        body += len(padded).to_bytes(4, "little") + GLB_BIN.to_bytes(4, "little") + padded
    return b"glTF" + (2).to_bytes(4, "little") + (12 + len(body)).to_bytes(4, "little") + body


PLY_DROP = {b"comment", b"obj_info"}


def ply_parts(data):
    """(the header through its end_header line, the data after it)."""
    if not (data.startswith(b"ply\n") or data.startswith(b"ply\r\n")):
        raise Refused("not a PLY file")
    pos = 0
    while True:
        end = data.find(b"\n", pos)
        if end < 0 or end > 1 << 20:
            raise Refused("damaged PLY: no end_header line in its first MB")
        if data[pos:end].strip() == b"end_header":
            return data[:end + 1], data[end + 1:]
        pos = end + 1


def ply_first_word(line):
    words = line.split(None, 1)
    return words[0] if words else b""


def ply_lines(head):
    """The header's lines split on line feeds only, each with its end, so a bare carriage return stays
    inside the line it belongs to."""
    return [line + b"\n" for line in head.split(b"\n")[:-1]]


def ply_names(data):
    head, _ = ply_parts(data)
    return sorted({ply_first_word(line).decode("ascii") for line in ply_lines(head)
                   if ply_first_word(line) in PLY_DROP})


def ply_clean(data):
    head, rest = ply_parts(data)
    return b"".join(line for line in ply_lines(head) if ply_first_word(line) not in PLY_DROP) + rest


GZIP_NAMES = {0x04: "gzip extra field", 0x08: "gzip file name", 0x10: "gzip comment"}


def gzip_header_end(data):
    """Where the first gzip member's compressed data starts."""
    flags, i = data[3], 10
    if flags & 0x04:
        i += 2 + int.from_bytes(data[i:i + 2], "little")
    for bit in (0x08, 0x10):
        if flags & bit:
            i = data.index(b"\x00", i) + 1
    if flags & 0x02:
        i += 2
    return i


def spz_v4(data):
    if len(data) < 32:
        raise Refused("damaged SPZ: shorter than its 32 byte header")
    version = int.from_bytes(data[4:8], "little")
    if version != 4:
        raise Refused("SPZ version %d with an NGSP header is not read" % version)


def spz_names(data):
    if data[:4] == b"NGSP":
        spz_v4(data)
        found = set()
        if data[14] & 0x2 or int.from_bytes(data[16:20], "little") > 32:
            found.add("extension records")
        if data[20:32].strip(b"\x00"):
            found.add("reserved bytes")
        return sorted(found)
    if data[:2] == b"\x1f\x8b" and len(data) >= 10:
        found = {name for bit, name in GZIP_NAMES.items() if data[3] & bit}
        unpack = zlib.decompressobj(-15)
        unpack.decompress(data[gzip_header_end(data):])
        if unpack.unused_data[8:].strip(b"\x00"):
            found.add("data after the first gzip member")  # left as it is, so a clear refuses the file
        return sorted(found)
    raise Refused("not an SPZ file")


def spz_clean(data):
    if data[:4] == b"NGSP":
        spz_v4(data)
        if data[14] & 0x2 or int.from_bytes(data[16:20], "little") > 32:
            raise Refused("SPZ extension records are not read, and dropping them would move every stream: "
                          "export it without extensions")
        return data[:20] + bytes(12) + data[32:]
    # the extra field, the name, the comment and the header checksum that covered them all go
    return data[:3] + bytes([0]) + data[4:10] + data[gzip_header_end(data):]


def sog_open(path):
    z = zipfile.ZipFile(path)
    names = [info.filename for info in z.infolist()]
    other = [n for n in names if n != "meta.json" and not n.lower().endswith(".webp")]
    if "meta.json" not in names or other:
        z.close()
        raise Refused("a SOG entry it does not read: %s" % (other[0] if other else "no meta.json"))
    return z


def sog_check(path):
    z = sog_open(path)
    found = set()
    with z:
        doc = json.loads(z.read("meta.json").decode("utf-8"))
        asset = doc.get("asset") if isinstance(doc, dict) else None
        if isinstance(asset, dict):
            found.update("meta.json asset.%s" % f for f in ("generator", "copyright") if asset.get(f))
        walked = set()
        gltf_walk(doc, "", walked)
        found.update("meta.json %s" % name for name in walked)
        if z.comment:
            found.add("zip comment")
        for info in z.infolist():
            if info.filename.lower().endswith(".webp"):
                found.update("%s %s" % (info.filename, name) for name in webp_names(z.read(info)))
            if info.comment:
                found.add("%s comment" % info.filename)
    return sorted(found)


def sog_scrub(path, tmp):
    z = sog_open(path)
    with z as zin, zipfile.ZipFile(tmp, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename == "meta.json":
                doc = json.loads(data.decode("utf-8"))
                if isinstance(doc.get("asset"), dict):
                    for field in ("generator", "copyright"):
                        doc["asset"].pop(field, None)
                gltf_strip(doc)
                data = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            elif info.filename.lower().endswith(".webp") and webp_names(data):
                data = webp_clean(data)
            info.comment = b""
            zout.writestr(info, data, compress_type=info.compress_type)


HDR_KEEP = {b"FORMAT", b"EXPOSURE", b"COLORCORR", b"PRIMARIES", b"PIXASPECT", b"VIEW", b"GAMMA"}


def hdr_parts(data):
    """(the header lines after the magic line, everything from the resolution line on)."""
    if not (data.startswith(b"#?RADIANCE\n") or data.startswith(b"#?RGBE\n")):
        raise Refused("not a Radiance HDR file")
    end = data.find(b"\n\n", 0, 65536)
    if end < 0:
        raise Refused("damaged Radiance header: no blank line in its first 64 KB")
    return data[:end].split(b"\n"), data[end + 2:]


def hdr_dropped(line):
    """What a header line names, None for one that stays."""
    if line.startswith(b"#"):
        return "comment"
    var = line.split(b"=", 1)[0].strip()
    if b"=" in line and var in HDR_KEEP:
        return None
    return "header %s" % var.decode("ascii", "replace")


def hdr_names(data):
    lines, _ = hdr_parts(data)
    return sorted({hdr_dropped(line) for line in lines[1:] if line} - {None})


def hdr_clean(data):
    lines, rest = hdr_parts(data)
    kept = [lines[0]] + [line for line in lines[1:] if line and hdr_dropped(line) is None]
    return b"\n".join(kept) + b"\n\n" + rest


SVG_COMMENT = re.compile(rb"<!--.*?-->", re.S)
SVG_METADATA = re.compile(rb"<(?:[\w.-]+:)?metadata(?![\w.-])[^>]*?(?:/>|>.*?</(?:[\w.-]+:)?metadata\s*>)", re.S)
SVG_CDATA = re.compile(rb"(<!\[CDATA\[.*?\]\]>)", re.S)
SVG_STYLE = re.compile(rb"(<style\b[^>]*>)(.*?)(</style\s*>)", re.S | re.I)
CSS_COMMENT = re.compile(rb"/\*.*?\*/", re.S)
SVG_DESC_TOOL = re.compile(rb"<desc\b[^>]*>\s*(?:Created|Made|Generated) (?:with|by)\b[^<]*</desc\s*>", re.I)
SVG_EDITORS = (b"inkscape", b"sodipodi", b"dc", b"cc", b"rdf")
SVG_EDITOR_ELEMENT = re.compile(rb"<(inkscape|sodipodi|dc|cc|rdf):([\w.-]+)\b[^>]*?(?:/>|>.*?</\1:\2\s*>)", re.S)
SVG_EDITOR_ATTR = re.compile(rb"""\s(?:inkscape|sodipodi):[\w.-]+\s*=\s*(?:"[^"]*"|'[^']*')""")
SVG_EDITOR_LEFT = re.compile(rb"[<\s](?:inkscape|sodipodi|dc|cc|rdf):")
SVG_NS = re.compile(rb"""\sxmlns:([\w.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
SVG_NS_PLAIN = {b"http://www.w3.org/2000/svg", b"http://www.w3.org/1999/xlink", b"http://www.w3.org/XML/1998/namespace"}
SVG_EMBEDDED = re.compile(rb"""(?:href|src)\s*=\s*["']\s*data:image/""", re.I)
SVG_ABS_LINK = re.compile(rb"""(?:href|src)\s*=\s*["']\s*(?:file:|[a-z]:[\\/]|\\\\|/(?:users|home)/)""", re.I)


def svg_other_ns(data):
    return sorted({m.group(1).decode("ascii", "replace") for m in SVG_NS.finditer(data)
                   if (m.group(2) if m.group(2) is not None else m.group(3)) not in SVG_NS_PLAIN
                   and m.group(1) not in SVG_EDITORS})


def svg_text(data):
    """Refuse what the byte patterns cannot read: a UTF-16 file, or NUL bytes anywhere (review note 1)."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in data:
        raise Refused("a UTF-16 or binary SVG is not read: save it as UTF-8")


def svg_outside_cdata(data, edit):
    """Apply edit to the markup only; a CDATA block is script or style text and stays as written (review note 2)."""
    return b"".join(part if i % 2 else edit(part) for i, part in enumerate(SVG_CDATA.split(data)))


def svg_style_comments(data):
    return any(CSS_COMMENT.search(m.group(2)) for m in SVG_STYLE.finditer(data))


def svg_names(data):
    svg_text(data)
    found = set()
    if any(SVG_COMMENT.search(part) for i, part in enumerate(SVG_CDATA.split(data)) if not i % 2):
        found.add("comment")
    if SVG_METADATA.search(data):
        found.add("metadata")
    if svg_style_comments(data):
        found.add("style comment")
    if SVG_DESC_TOOL.search(data):
        found.add("desc naming a tool")
    if SVG_EDITOR_LEFT.search(data) or any(m.group(1) in SVG_EDITORS for m in SVG_NS.finditer(data)):
        found.add("editor attributes")
    found.update("namespace " + prefix for prefix in svg_other_ns(data))
    if SVG_EMBEDDED.search(data):
        found.add("embedded image")
    if SVG_ABS_LINK.search(data):
        found.add("absolute path")
    return sorted(found)


def svg_clean(data):
    svg_text(data)
    if SVG_EMBEDDED.search(data) or SVG_ABS_LINK.search(data):
        raise Refused("an embedded image or an absolute path is inside it: export it again without that")
    other = svg_other_ns(data)
    if other:
        raise Refused("another editor's namespace (%s) is inside it: export it as plain SVG" % ", ".join(other))
    data = svg_outside_cdata(data, lambda part: SVG_COMMENT.sub(b"", part))
    data = SVG_STYLE.sub(lambda m: m.group(1) + CSS_COMMENT.sub(b"", m.group(2)) + m.group(3), data)
    data = SVG_METADATA.sub(b"", data)
    data = SVG_DESC_TOOL.sub(b"", data)
    data = SVG_EDITOR_ELEMENT.sub(b"", data)
    data = SVG_EDITOR_ATTR.sub(b"", data)
    data = SVG_NS.sub(lambda m: b"" if m.group(1) in SVG_EDITORS else m.group(0), data)
    if SVG_EDITOR_LEFT.search(data):
        raise Refused("an editor element it cannot take out cleanly is inside it: export it as plain SVG")
    try:
        xml.etree.ElementTree.fromstring(data)
    except xml.etree.ElementTree.ParseError as e:
        raise Refused("the cleared SVG would not parse (%s): export it as plain SVG" % e)
    return data


# A font is read table by table and cleared by a splice: the version records lose what follows the version
# number, the tables a font tool leaves go, and every other table is copied byte for byte. The credit records
# stay, since the SIL Open Font License asks every copy to keep them. Before the rename a proof reads the copy
# back and compares it with the original, table by table and field by field.
SFNT_FLAVORS = {0x00010000, 0x4F54544F, 0x74727565}  # TrueType, CFF ('OTTO'), Apple TrueType ('true')
SFNT_COLLECTION = 0x74746366  # 'ttcf'
# the OpenType tables the house's web fonts use; any other tag is named
FONT_TABLES = {"cmap", "head", "hhea", "hmtx", "maxp", "name", "OS/2", "post", "glyf", "loca", "cvt ", "fpgm", "prep",
               "gasp", "kern", "GDEF", "GPOS", "GSUB", "CFF ", "CFF2", "VORG", "BASE", "JSTF", "MATH", "STAT", "avar",
               "fvar", "gvar", "cvar", "HVAR", "MVAR", "VVAR", "vhea", "vmtx", "COLR", "CPAL", "hdmx", "LTSH", "VDMX",
               "PCLT", "meta"}
# tables a font tool or an editor leaves and no renderer needs (FontForge, ttfautohint, a signature, the VTT
# sources): named, and dropped by a clear
FONT_DROP = {"FFTM", "PfEd", "TTFA", "DSIG", "Debg", "TSI0", "TSI1", "TSI2", "TSI3", "TSI4", "TSI5", "TSIV", "TSIP",
             "TSIS", "TSID", "TSIJ", "TSIB"}
# image tables can hold an editor's comments or a PNG's text: named, and refused
FONT_IMAGES = {"SVG ", "sbix", "CBDT", "CBLC", "EBDT", "EBLC"}
FONT_META_PLAIN = {"dlng", "slng"}  # meta tags that only list scripts; any other one makes the table go
# copyright, trademark, manufacturer, designer, vendor and designer URLs, licence and its URL: the foundry's own
# words, where only a trace of this house is looked for, never a program's name
FONT_CREDITS = {0, 7, 8, 9, 11, 12, 13, 14}
FONT_VERSION_ID = 5
FONT_VERSION = re.compile(r"version +\d+(?:\.\d+)*", re.I)
# the font tools, and the AI programs only in forms no person or typeface bears (the bare word claude is also
# Claude Garamond, the designer a Garamond's description cites)
FONT_PROGRAMS = re.compile(r"\b(?:fonttools|pyftsubset|fontforge|glyphhanger|transfonter|fontsquirrel|ttfautohint|"
                           r"gftools|woff2_compress|sfnt2woff|claude\s+code|claude\.ai|anthropic|openai|chatgpt|"
                           r"copilot)\b", re.I)
FONT_NAMES_FILE = "~/.claude/tools/meta-scrub-names.txt"  # optional, one word a line, never copied to the kit
WOFF_HEADER = ">4sIIHHIHHIIIII"
WOFF2_HEADER = ">4sIIHHIIHHIIIII"
WOFF2_TAGS = ("cmap", "head", "hhea", "hmtx", "maxp", "name", "OS/2", "post", "cvt ", "fpgm", "glyf", "loca", "prep",
              "CFF ", "VORG", "EBDT", "EBLC", "gasp", "hdmx", "kern", "LTSH", "PCLT", "VDMX", "vhea", "vmtx", "BASE",
              "GDEF", "GPOS", "GSUB", "EBSC", "JSTF", "MATH", "CBDT", "CBLC", "COLR", "CPAL", "SVG ", "sbix", "acnt",
              "avar", "bdat", "bloc", "bsln", "cvar", "fdsc", "feat", "fmtx", "fvar", "gvar", "hsty", "just", "lcar",
              "mort", "morx", "opbd", "prop", "trak", "Zapf", "Silf", "Glat", "Gloc", "Feat", "Sill")
NODE_BROTLI = ("const z = require('zlib'), k = z.constants, parts = [];"
               "process.stdin.on('data', d => parts.push(d));"
               "process.stdin.on('end', () => { const b = Buffer.concat(parts);"
               " process.stdout.write(process.argv[1] === 'pack'"
               " ? z.brotliCompressSync(b, {params: {[k.BROTLI_PARAM_MODE]: k.BROTLI_MODE_FONT,"
               " [k.BROTLI_PARAM_QUALITY]: 11, [k.BROTLI_PARAM_SIZE_HINT]: b.length}})"
               " : z.brotliDecompressSync(b, {maxOutputLength: Number(process.argv[2])})); });")


class FontTable:
    """A table: its tag, its bytes as the container keeps them, its plain bytes (None while a WOFF2 transform
    holds them), and the container's own fields for it."""

    def __init__(self, tag, stored, plain, **fields):
        self.tag, self.stored, self.plain, self.fields = tag, stored, plain, fields


class Font:
    def __init__(self, container, flavor, tables, header, meta=None, private=None):
        self.container, self.flavor, self.tables, self.header = container, flavor, tables, header
        self.meta, self.private = meta, private  # a WOFF's extended metadata (decompressed) and private block

    def table(self, tag):
        return next((t for t in self.tables if t.tag == tag), None)


class ProofRefused(Exception):
    """A copy a clear wrote differs from its original in more than the clear may change."""

    def __init__(self, tag, field, detail=""):
        Exception.__init__(self, tag, field, detail)
        self.tag, self.field, self.detail = tag.strip() or tag, field, detail

    def __str__(self):
        return "PROOF REFUSED %s %s%s" % (self.tag, self.field, " (%s)" % self.detail if self.detail else "")


def node_brotli(data, op, size=0):
    """data packed ('pack') or unpacked ('unpack', to at most size bytes) by node's built-in Brotli. node is
    found on PATH, the way ffmpeg is (program), and runs with no NODE_OPTIONS or NODE_PATH from this environment."""
    node = program("node")
    if not node:
        raise Refused("needs node on PATH: a WOFF2 is Brotli-compressed and node's built-in zlib reads it")
    env = {k: v for k, v in os.environ.items() if k.upper() not in ("NODE_OPTIONS", "NODE_PATH")}
    done = subprocess.run([node, "-e", NODE_BROTLI, op, str(max(size, 1))], input=data, capture_output=True,
                          env=env, timeout=120)
    if done.returncode != 0:
        lines = done.stderr.decode("utf-8", "replace").strip().splitlines() or ["no message"]
        raise Refused("damaged WOFF2: its Brotli data does not %s (%s)"
                      % ("pack" if op == "pack" else "decode", next((l for l in lines if "Error" in l), lines[-1])))
    return done.stdout


def font_fields(data, at, layout, what):
    """The fields of a struct layout at offset at; a font cut short is refused."""
    if at < 0 or at + struct.calcsize(layout) > len(data):
        raise Refused("damaged font: %s cut short" % what)
    return struct.unpack_from(layout, data, at)


def font_round(n):
    return (n + 3) & ~3


def font_tag(raw, seen):
    tag = raw.decode("latin-1")
    if any(not 32 <= byte <= 126 for byte in raw):
        raise Refused("damaged font: a table tag %r that is not text" % tag)
    if tag in seen:
        raise Refused("damaged font: two %s tables" % tag.strip())
    seen.add(tag)
    return tag


def font_flavor(flavor, what):
    if flavor == SFNT_COLLECTION:
        raise Refused("a font collection, not read")
    if flavor not in SFNT_FLAVORS:
        raise Refused("not a %s font" % what)


def font_checksum(data):
    data = bytes(data) + bytes(-len(data) % 4)
    return sum(struct.unpack(">%dI" % (len(data) // 4), data)) & 0xFFFFFFFF


def table_checksum(tag, plain):
    """A table's checksum; head's is taken with its checksumAdjustment at zero, as the sfnt directory keeps it."""
    return font_checksum(plain[:8] + bytes(4) + plain[12:] if tag == "head" else plain)


def font_inflate(stored, size, what):
    unpack = zlib.decompressobj()
    try:
        plain = unpack.decompress(stored, size + 1)
    except zlib.error:
        raise Refused("damaged font: %s does not decompress" % what)
    if len(plain) != size or not unpack.eof:
        raise Refused("damaged font: %s decompresses to another size than its directory says" % what)
    return plain


def sfnt_read(data):
    flavor, count, search, selector, shift = font_fields(data, 0, ">IHHHH", "the sfnt header")
    font_flavor(flavor, "TrueType or OpenType")
    tables, seen = [], set()
    for n in range(count):
        tag, checksum, offset, length = font_fields(data, 12 + 16 * n, ">4sIII", "the table directory")
        tag = font_tag(tag, seen)
        if offset + length > len(data):
            raise Refused("damaged font: table %s runs past the end of the file" % tag.strip())
        body = data[offset:offset + length]
        tables.append(FontTable(tag, body, body, length=length, checksum=checksum, offset=offset))
    return Font("sfnt", flavor, tables, {"search": (search, selector, shift)})


def woff_block(data, offset, length, what):
    if not length:
        return None
    if offset + length > len(data):
        raise Refused("damaged WOFF: its %s block runs past the end of the file" % what)
    return data[offset:offset + length]


def woff_read(data):
    (signature, flavor, length, count, reserved, total_sfnt, major, minor, meta_offset, meta_length, meta_size,
     private_offset, private_length) = font_fields(data, 0, WOFF_HEADER, "the WOFF header")
    if signature != b"wOFF":
        raise Refused("not a WOFF font")
    font_flavor(flavor, "WOFF")
    if length != len(data) or reserved:
        raise Refused("damaged WOFF: a header that does not match the file (length %d of %d)" % (length, len(data)))
    tables, seen = [], set()
    for n in range(count):
        tag, offset, comp, orig, checksum = font_fields(data, 44 + 20 * n, ">4sIIII", "the table directory")
        tag = font_tag(tag, seen)
        if offset + comp > len(data) or comp > orig:
            raise Refused("damaged WOFF: table %s does not fit its directory entry" % tag.strip())
        stored = data[offset:offset + comp]
        plain = stored if comp == orig else font_inflate(stored, orig, "WOFF table " + tag.strip())
        tables.append(FontTable(tag, stored, plain, offset=offset, compLength=comp, origLength=orig,
                                origChecksum=checksum))
    meta = woff_block(data, meta_offset, meta_length, "metadata")
    header = {"totalSfntSize": total_sfnt, "version": (major, minor),
              "blocks": (meta_offset, meta_length, meta_size, private_offset, private_length)}
    return Font("woff", flavor, tables, header, None if meta is None else font_inflate(meta, meta_size, "WOFF metadata"),
                woff_block(data, private_offset, private_length, "private"))


def woff2_number(data, at):
    """A UIntBase128 at offset at, read as the reference decoder reads it: (the number, the offset after it)."""
    value = 0
    for n in range(5):
        (byte,) = font_fields(data, at + n, ">B", "the table directory")
        if (n == 0 and byte == 0x80) or value & 0xFE000000:
            raise Refused("damaged WOFF2: a malformed number in its table directory")
        value = value << 7 | byte & 127
        if not byte & 128:
            return value, at + n + 1
    raise Refused("damaged WOFF2: a number longer than five bytes in its table directory")


def woff2_encode(value):
    out = [value & 127]
    while value > 127:
        value >>= 7
        out.append(128 | value & 127)
    return bytes(reversed(out))


def woff2_read(data):
    (signature, flavor, length, count, reserved, total_sfnt, total_compressed, major, minor, meta_offset, meta_length,
     meta_size, private_offset, private_length) = font_fields(data, 0, WOFF2_HEADER, "the WOFF2 header")
    if signature != b"wOF2":
        raise Refused("not a WOFF2 font")
    font_flavor(flavor, "WOFF2")
    if length != len(data) or reserved:
        raise Refused("damaged WOFF2: a header that does not match the file (length %d of %d)" % (length, len(data)))
    at, entries, seen = 48, [], set()
    for _ in range(count):
        (flags,) = font_fields(data, at, ">B", "the table directory")
        at += 1
        if flags & 63 == 63:
            (raw,) = font_fields(data, at, ">4s", "the table directory")
            at += 4
        else:
            raw = WOFF2_TAGS[flags & 63].encode("latin-1")
        tag, version = font_tag(raw, seen), flags >> 6
        orig, at = woff2_number(data, at)
        # glyf and loca are transformed at version 0 (3 is the null transform); any other table at a version
        # other than 0, and only hmtx at 1 is a transform the reference decoder knows
        transformed = version == 0 if tag in ("glyf", "loca") else version != 0
        transform_length = None
        if transformed:
            if tag not in ("glyf", "loca", "hmtx") or (tag == "hmtx" and version != 1):
                raise Refused("a WOFF2 table %s with a transform this reader does not know, not read" % tag.strip())
            transform_length, at = woff2_number(data, at)
            if tag == "loca" and transform_length:
                raise Refused("damaged WOFF2: a transformed loca with a length")
        entries.append((tag, flags & 63, version, orig, transform_length))
    end = at + total_compressed
    if end > len(data):
        raise Refused("damaged WOFF2: its compressed tables run past the end of the file")
    size = sum(orig if transform_length is None else transform_length for _, _, _, orig, transform_length in entries)
    stream = node_brotli(data[at:end], "unpack", size)
    if len(stream) != size:
        raise Refused("damaged WOFF2: its tables decompress to %d bytes, the directory says %d" % (len(stream), size))
    tables, pos = [], 0
    for tag, index, version, orig, transform_length in entries:
        body = stream[pos:pos + (orig if transform_length is None else transform_length)]
        pos += len(body)
        tables.append(FontTable(tag, body, body if transform_length is None else None, index=index, transform=version,
                                origLength=orig, transformLength=transform_length))
    # the blocks lie where the reference decoder looks: each on the four byte boundary after the one before
    after, meta, private = font_round(end), None, None
    if meta_offset:
        if meta_offset != after or meta_offset + meta_length > len(data):
            raise Refused("damaged WOFF2: its metadata block is not where its header says")
        meta = node_brotli(data[meta_offset:meta_offset + meta_length], "unpack", meta_size)
        if len(meta) != meta_size:
            raise Refused("damaged WOFF2: its metadata decompresses to another size than its header says")
        after = font_round(meta_offset + meta_length)
    if private_offset:
        if private_offset != after or private_offset + private_length > len(data):
            raise Refused("damaged WOFF2: its private block is not where its header says")
        private = data[private_offset:private_offset + private_length]
        after = font_round(private_offset + private_length)
    if after != font_round(len(data)):
        raise Refused("damaged WOFF2: bytes after its last block")
    header = {"totalSfntSize": total_sfnt, "totalCompressedSize": total_compressed, "start": at,
              "version": (major, minor), "blocks": (meta_offset, meta_length, meta_size, private_offset, private_length)}
    return Font("woff2", flavor, tables, header, meta, private)


def font_read(data, ext):
    return {".woff2": woff2_read, ".woff": woff_read}.get(ext, sfnt_read)(data)


def name_records(table):
    """(format, records as (platform, encoding, language, name id, bytes), language tags as bytes) of a name table."""
    form, count, storage = font_fields(table, 0, ">HHH", "the name table")
    if form not in (0, 1):
        raise Refused("a name table of format %d, not read" % form)

    def string(length, offset, what):
        if storage + offset + length > len(table):
            raise Refused("damaged font: %s runs past the name table" % what)
        return table[storage + offset:storage + offset + length]
    records = []
    for n in range(count):
        platform, encoding, language, name_id, length, offset = font_fields(table, 6 + 12 * n, ">6H", "a name record")
        records.append((platform, encoding, language, name_id, string(length, offset, "name record %d" % name_id)))
    tags = []
    if form == 1:
        (tag_count,) = font_fields(table, 6 + 12 * count, ">H", "the name table's language tags")
        for n in range(tag_count):
            length, offset = font_fields(table, 8 + 12 * count + 4 * n, ">HH", "a language tag")
            tags.append(string(length, offset, "a language tag"))
    return form, records, tags


def name_table(form, records, tags):
    """A name table again: the records in their order, each string once in the storage, nothing else kept."""
    storage, placed = bytearray(), {}

    def place(raw):
        if raw not in placed:
            placed[raw] = len(storage)
            storage.extend(raw)
        return placed[raw]
    entries = b"".join(struct.pack(">6H", p, e, l, i, len(raw), place(raw)) for p, e, l, i, raw in records)
    tail = b""
    if form == 1:
        tail = struct.pack(">H", len(tags)) + b"".join(struct.pack(">HH", len(raw), place(raw)) for raw in tags)
    if len(storage) > 0xFFFF:
        raise Refused("a name table whose strings pass 64 KB, not read")
    return struct.pack(">HHH", form, len(records), 6 + len(entries) + len(tail)) + entries + tail + bytes(storage)


def name_text(platform, encoding, raw):
    """(the text of a name record, the bytes a character of its ASCII prefix takes): UTF-16 on the Unicode and
    Windows platforms, Mac Roman on the Mac's, one byte a character on any other."""
    if platform in (0, 3) or (platform == 2 and encoding == 1):
        return raw.decode("utf-16-be", "replace"), 2
    return raw.decode("mac_roman" if platform == 1 and encoding == 0 else "latin-1"), 1


def font_quote(text, at=0):
    """A record's text for a message, in ASCII, a long one cut to the part around offset at."""
    if len(text) > 100:
        start = max(0, min(at - 40, len(text) - 100))
        text = ("..." if start else "") + text[start:start + 100] + ("..." if start + 100 < len(text) else "")
    text = text.encode("ascii", "backslashreplace").decode("ascii")
    return '"%s"' % text.replace("\r", "\\r").replace("\n", "\\n")


def house_traces():
    """(what, pattern) for each trace of this house: a local path, the login and machine names (three letters or
    more, as whole words), and each line of the optional private list."""
    traces = [("a local path", LOCAL_PATH)]
    try:
        login = os.getlogin()
    except OSError:
        login = os.environ.get("USERNAME", "")
    for what, word in (("the login name", login), ("the machine name", socket.gethostname())):
        if len(word) >= 3:
            traces.append((what, re.compile(r"(?<!\w)%s(?!\w)" % re.escape(word), re.I)))
    try:
        with open(os.path.expanduser(FONT_NAMES_FILE), encoding="utf-8-sig") as f:
            words = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        words = []
    return traces + [("a listed word", re.compile(re.escape(word), re.I)) for word in words]


def font_hits(text, traces, programs):
    """(what the text holds, in order, where the first one starts)."""
    hits = [(m.start(), what) for what, pattern in traces for m in [pattern.search(text)] if m]
    if programs:
        hits += [(m.start(), "the program " + m.group(0).lower()) for m in FONT_PROGRAMS.finditer(text)]
    hits.sort()
    found = []
    for _, what in hits:
        if what not in found:
            found.append(what)
    return found, hits[0][0] if hits else 0


def cff_index(table, at, what):
    """The entries of the CFF INDEX at offset at, and the offset just past it; one that does not hold together
    is refused."""
    count, = font_fields(table, at, ">H", what)
    if not count:
        return [], at + 2
    size, = font_fields(table, at + 2, ">B", what)
    start, base = at + 3, at + 2 + (count + 1) * size
    if not 1 <= size <= 4 or base + 1 > len(table):
        raise Refused("damaged font: %s %s" % (what, "cut short" if 1 <= size <= 4 else "with offset size %d" % size))
    offsets = [int.from_bytes(table[start + n * size:start + (n + 1) * size], "big") for n in range(count + 1)]
    if offsets[0] != 1 or any(b < a for a, b in zip(offsets, offsets[1:])) or base + offsets[-1] > len(table):
        raise Refused("damaged font: %s whose offsets are out of order or run past its table" % what)
    return [table[base + a:base + b] for a, b in zip(offsets, offsets[1:])], base + offsets[-1]


def cff_strings(table):
    """The text a CFF table holds: its Name INDEX (the font names) and its String INDEX, where the Top DICT's
    Notice, Copyright, FullName, FamilyName and Weight point, with the glyph names and every other custom string.
    The charstrings and subroutines are glyph code, not text, and are not read. A CFF table that does not parse
    is refused as damaged."""
    major, _, size, offsize = font_fields(table, 0, ">4B", "the CFF header")
    if major != 1 or size < 4 or not 1 <= offsize <= 4:
        raise Refused("damaged font: a CFF table whose header is not CFF 1 (version %d, size %d, offset size %d)"
                      % (major, size, offsize))
    names, at = cff_index(table, size, "the CFF Name INDEX")
    tops, at = cff_index(table, at, "the CFF Top DICT INDEX")
    if not names or len(tops) != len(names):
        raise Refused("damaged font: a CFF table with %d names and %d Top DICTs" % (len(names), len(tops)))
    return names + cff_index(table, at, "the CFF String INDEX")[0]


def meta_tags(table):
    _, _, _, count = font_fields(table, 0, ">IIII", "the meta table")
    tags = set()
    for n in range(count):
        tag, offset, length = font_fields(table, 16 + 12 * n, ">4sII", "the meta table")
        if offset + length > len(table):
            raise Refused("damaged font: a meta table entry runs past its table")
        tags.add(tag.decode("latin-1"))
    return tags


def version_kept(platform, encoding, raw):
    """The bytes of a version record a clear keeps: "Version" and its number, as the record writes them."""
    text, width = name_text(platform, encoding, raw)
    found = FONT_VERSION.match(text)
    if not found:
        raise Refused("a version record (name 5) with no leading version number, %s: produce it again from the "
                      "upstream file" % font_quote(text))
    return raw[:found.end() * width]


def font_review(font):
    """What a font names, as (named, refused, cuts, dropped): every finding in order, the ones a clear refuses, the
    version records a clear cuts as {record number: the bytes kept}, and the tables a clear drops."""
    traces = house_traces()
    named, refused, cuts, dropped = [], [], {}, set()

    def refuse(item):
        named.append(item)
        refused.append(item)
    for table in font.tables:
        tag, label = table.tag, "table " + table.tag.strip()
        if tag in FONT_DROP:
            named.append(label)
            dropped.add(tag)
        elif tag in FONT_IMAGES:
            refuse(label + " (an image table)")
        elif tag not in FONT_TABLES:
            refuse(label + " (outside the OpenType set)")
        elif tag == "meta":
            other = sorted(meta_tags(table.plain) - FONT_META_PLAIN)
            if other:
                named.append("%s (%s)" % (label, ", ".join(other)))
                dropped.add(tag)
        elif tag == "CFF ":  # a CFF2 table has no Name or String INDEX and holds no text, so it is not scanned
            found = []
            for raw in cff_strings(table.plain):
                found += [what for what in font_hits(raw.decode("latin-1"), traces, False)[0] if what not in found]
            if found:
                refuse("%s holds %s" % (label, ", ".join(found)))
    name = font.table("name")
    for number, (platform, encoding, language, name_id, raw) in enumerate(name_records(name.plain)[1] if name else []):
        text = name_text(platform, encoding, raw)[0]
        where = "name %d [%d/%d/0x%X]" % (name_id, platform, encoding, language)
        if name_id == FONT_VERSION_ID:
            kept = version_kept(platform, encoding, raw)
            if kept != raw:
                named.append("%s %s" % (where, font_quote(text)))
                cuts[number] = kept
            continue
        found, at = font_hits(text, traces, name_id not in FONT_CREDITS)
        if found:
            refuse("%s %s holds %s" % (where, font_quote(text, at), ", ".join(found)))
    if font.meta is not None:
        found, _ = font_hits(font.meta.decode("utf-8", "replace"), traces, False)
        named.append("metadata block" + (" holding %s" % ", ".join(found) if found else ""))
    if font.private is not None:
        named.append("private block")
    return named, refused, cuts, dropped


def sfnt_build(flavor, tables):
    """An sfnt from (tag, bytes) pairs laid out in the order given, its directory sorted by tag, every checksum
    and head's checksumAdjustment computed over the bytes written."""
    count = len(tables)
    if not count:
        raise Refused("a font with no tables, not read")
    power = 1 << (count.bit_length() - 1)
    header = struct.pack(">IHHHH", flavor, count, power * 16, count.bit_length() - 1, count * 16 - power * 16)
    body, placed = bytearray(), {}
    for tag, raw in tables:
        if tag == "head":
            raw = raw[:8] + bytes(4) + raw[12:]
        placed[tag] = (12 + 16 * count + len(body), raw)
        body += raw + bytes(-len(raw) % 4)
    directory = b"".join(struct.pack(">4sIII", tag.encode("latin-1"), font_checksum(raw), at, len(raw))
                         for tag, (at, raw) in sorted(placed.items()))
    out = bytearray(header + directory + body)
    if "head" in placed:
        at = placed["head"][0]
        out[at + 8:at + 12] = struct.pack(">I", (0xB1B0AFBA - font_checksum(out)) & 0xFFFFFFFF)
    return bytes(out)


def sfnt_write(font, kept, name):
    return sfnt_build(font.flavor, [(t.tag, name if t.tag == "name" and name is not None else t.plain)
                                    for t in sorted(kept, key=lambda t: t.fields["offset"])])


def woff_head(flavor, plains):
    """head with the checksumAdjustment of the sfnt a WOFF decodes to, its tables laid out in tag order."""
    if "head" not in plains:
        return None
    return sfnt_read(sfnt_build(flavor, sorted(plains.items()))).table("head").plain


def woff_write(font, kept, name):
    plains = {t.tag: name if t.tag == "name" and name is not None else t.plain for t in kept}
    head = woff_head(font.flavor, plains)
    if head is not None:
        plains["head"] = head
    start = 44 + 20 * len(kept)
    body, directory = bytearray(), []
    for table in sorted(kept, key=lambda t: t.fields["offset"]):
        plain = plains[table.tag]
        stored = table.stored  # the kept tables' compressed bytes, as they were
        if plain != table.plain:
            packed = zlib.compress(plain, 9)
            stored = packed if len(packed) < len(plain) else plain
        directory.append(struct.pack(">4sIIII", table.tag.encode("latin-1"), start + len(body), len(stored), len(plain),
                                     table_checksum(table.tag, plain)))
        body += stored + bytes(-len(stored) % 4)
    total = 12 + 16 * len(kept) + sum(font_round(len(p)) for p in plains.values())
    header = struct.pack(WOFF_HEADER, b"wOFF", font.flavor, start + len(body), len(kept), 0, total,
                         *font.header["version"], 0, 0, 0, 0, 0)
    return header + b"".join(sorted(directory)) + bytes(body)


def woff2_write(font, kept, name):
    directory, stream, sizes = bytearray(), [], []
    for table in kept:
        fields, raw, orig = table.fields, table.stored, table.fields["origLength"]
        if table.tag == "name" and name is not None:
            raw, orig = name, len(name)
        directory.append(fields["transform"] << 6 | fields["index"])
        if fields["index"] == 63:
            directory += table.tag.encode("latin-1")
        directory += woff2_encode(orig)
        if fields["transformLength"] is not None:
            directory += woff2_encode(fields["transformLength"])
        stream.append(raw)
        sizes.append(orig)
    packed = node_brotli(b"".join(stream), "pack")
    start = 48 + len(directory)
    length = font_round(start + len(packed))
    total = 12 + 16 * len(kept) + sum(font_round(size) for size in sizes)
    # head.checksumAdjustment stays as it was: the reference decoder rebuilds glyf and loca and recomputes it
    header = struct.pack(WOFF2_HEADER, b"wOF2", font.flavor, length, len(kept), 0, total, len(packed),
                         *font.header["version"], 0, 0, 0, 0, 0)
    return header + bytes(directory) + packed + bytes(length - start - len(packed))


def font_clean(data, ext):
    font = font_read(data, ext)
    _, refused, cuts, dropped = font_review(font)
    if refused:
        raise Refused("%s: a clear cuts only the version records and drops only tool tables, so produce it again "
                      "from the upstream file" % "; ".join(refused))
    head = font.table("head")
    if head is not None and len(head.plain) < 54:
        raise Refused("damaged font: a head table shorter than 54 bytes")
    name = None
    if cuts:
        form, records, tags = name_records(font.table("name").plain)
        name = name_table(form, [r[:4] + (cuts.get(n, r[4]),) for n, r in enumerate(records)], tags)
    kept = [t for t in font.tables if t.tag not in dropped]
    return {"sfnt": sfnt_write, "woff": woff_write, "woff2": woff2_write}[font.container](font, kept, name)


def proof_name(old, new, cuts):
    old_form, old_records, old_tags = name_records(old.plain)
    try:
        form, records, tags = name_records(new.plain)
    except Refused as e:
        raise ProofRefused("name", "unreadable", str(e))
    if form != old_form:
        raise ProofRefused("name", "format")
    if len(records) != len(old_records):
        raise ProofRefused("name", "count")
    for number, (before, after) in enumerate(zip(old_records, records)):
        if before[:4] != after[:4] or after[4] != cuts.get(number, before[4]):
            raise ProofRefused("name", "record", "%d, name %d [%d/%d/0x%X]" % ((number, before[3]) + before[:3]))
    if tags != old_tags:
        raise ProofRefused("name", "langTag")


def proof_table(container, old, new, cuts):
    """The container's fields and the bytes of one kept table, against the original's."""
    tag = new.tag
    fields = {"sfnt": ("length",), "woff": ("compLength", "origLength"),
              "woff2": ("index", "transform", "origLength", "transformLength")}[container]
    for field in fields:
        if (tag == "name" and field in ("length", "compLength", "origLength")) \
                or (tag == "head" and container == "woff" and field == "compLength"):
            continue  # the cut name changes its lengths; a WOFF's head is compressed again
        if old.fields[field] != new.fields[field]:
            raise ProofRefused(tag, "flags" if field == "index" else field)
    if tag == "name":
        proof_name(old, new, cuts)
    elif tag == "head" and container != "woff2":
        if len(old.plain) != len(new.plain) or old.plain[:8] + old.plain[12:] != new.plain[:8] + new.plain[12:]:
            raise ProofRefused(tag, "bytes")
    elif (old.stored != new.stored) if container == "woff2" else (old.plain != new.plain):
        raise ProofRefused(tag, "bytes")


def proof_layout(tables, start, length_field, data):
    """Each table starts where the one before ends, on a four byte boundary, with zero padding, and the file ends
    after the last one."""
    at = start
    for table in sorted(tables, key=lambda t: t.fields["offset"]):
        if table.fields["offset"] != at:
            raise ProofRefused(table.tag, "offset")
        end = at + table.fields[length_field]
        if data[end:font_round(end)].strip(b"\x00"):
            raise ProofRefused(table.tag, "padding")
        at = font_round(end)
    if len(data) != at:
        raise ProofRefused("header", "length")


def proof_header(font, new, data):
    """The copy's header, directory and checksums, computed again from the bytes it holds."""
    count = len(new.tables)
    if count != len(font.tables) - len({t.tag for t in font.tables} - {t.tag for t in new.tables}):
        raise ProofRefused("header", "numTables")
    if new.container == "sfnt":
        power = 1 << (count.bit_length() - 1)
        if new.header["search"] != (power * 16, count.bit_length() - 1, count * 16 - power * 16):
            raise ProofRefused("header", "searchRange")
        for table in new.tables:
            if table.fields["checksum"] != table_checksum(table.tag, table.plain):
                raise ProofRefused(table.tag, "checksum")
        proof_layout(new.tables, 12 + 16 * count, "length", data)
        head = new.table("head")
        if head is not None:
            at = head.fields["offset"]
            zeroed = data[:at + 8] + bytes(4) + data[at + 12:]
            if struct.unpack(">I", head.plain[8:12])[0] != (0xB1B0AFBA - font_checksum(zeroed)) & 0xFFFFFFFF:
                raise ProofRefused("head", "checksumAdjustment")
        return
    if new.header["version"] != font.header["version"]:
        raise ProofRefused("header", "version")
    if new.header["blocks"][:3] != (0, 0, 0):
        raise ProofRefused("header", "metaOffset")
    if new.header["blocks"][3:] != (0, 0):
        raise ProofRefused("header", "privOffset")
    if new.header["totalSfntSize"] != 12 + 16 * count + sum(font_round(t.fields["origLength"]) for t in new.tables):
        raise ProofRefused("header", "totalSfntSize")
    if new.container == "woff":
        if [t.tag for t in new.tables] != sorted(t.tag for t in new.tables):
            raise ProofRefused("header", "directoryOrder")
        for table in new.tables:
            if table.fields["origChecksum"] != table_checksum(table.tag, table.plain):
                raise ProofRefused(table.tag, "origChecksum")
        proof_layout(new.tables, 44 + 20 * count, "compLength", data)
        head = new.table("head")
        if head is not None and head.plain != woff_head(new.flavor, {t.tag: t.plain for t in new.tables}):
            raise ProofRefused("head", "checksumAdjustment")
        return
    end = new.header["start"] + new.header["totalCompressedSize"]
    if len(data) % 4 or len(data) != font_round(end) or data[end:].strip(b"\x00"):
        raise ProofRefused("header", "totalCompressedSize")


def font_proof(original, copy, ext):
    """Raise ProofRefused unless copy is original with only its version records cut and its dropped tables and
    blocks left out: every other table byte for byte, its container fields equal, head's other bytes equal, and
    the copy's header, directory and checksums right for the bytes it holds. An original it cannot read is
    Refused."""
    font = font_read(original, ext)
    _, _, cuts, dropped = font_review(font)
    try:
        new = font_read(copy, ext)
    except Refused as e:
        raise ProofRefused("file", "unreadable", str(e))
    if new.flavor != font.flavor:
        raise ProofRefused("header", "flavor")
    want = [t.tag for t in font.tables if t.tag not in dropped]
    got = [t.tag for t in new.tables]
    for tag in want:
        if tag not in got:
            raise ProofRefused(tag, "missing")
    for tag in got:
        if tag not in want:
            raise ProofRefused(tag, "extra")
    if font.container == "woff2" and got != want:  # a WOFF2 stream holds its tables in directory order
        raise ProofRefused(next(g for g, w in zip(got, want) if g != w), "order")
    old = {t.tag: t for t in font.tables}
    for table in new.tables:
        proof_table(font.container, old[table.tag], table, cuts)
    proof_header(font, new, copy)


def font_write(path, data):
    """The one writer a font clear uses (a test replaces it to show the proof refuses a damaged copy)."""
    with open(path, "wb") as f:
        f.write(data)


def font_check(path):
    with open(path, "rb") as f:
        return font_review(font_read(f.read(), kind(path)))[0]


def font_scrub(path, tmp):
    with open(path, "rb") as f:
        data = f.read()
    font_write(tmp, font_clean(data, kind(path)))
    with open(tmp, "rb") as f:
        copy = f.read()
    try:
        font_proof(data, copy, kind(path))
    except ProofRefused as e:
        raise Refused("the cleared copy failed its proof (%s), so the original is left as it is" % e)


def font_proof_main(paths):
    if len(paths) != 2 or kind(paths[0]) not in FONTS:
        sys.stderr.write("usage: meta-scrub.py --font-proof <original .ttf .otf .woff or .woff2> <its cleared copy>\n")
        return 2
    try:
        with open(paths[0], "rb") as f:
            original = f.read()
        with open(paths[1], "rb") as f:
            copy = f.read()
        font_proof(original, copy, kind(paths[0]))
    except ProofRefused as e:
        print(e)
        return 3
    except Exception as e:  # the original or the copy cannot be opened, or node is missing: no proof either way
        sys.stderr.write("meta-scrub: %s: %s\n" % (paths[0], e))
        return 2
    print("PROOF HOLDS")
    return 0


def bytes_check(names):
    def check(path):
        with open(path, "rb") as f:
            return names(f.read())
    return check


def bytes_scrub(clean):
    def scrub_to(path, tmp):
        with open(path, "rb") as f:
            data = f.read()
        with open(tmp, "wb") as f:
            f.write(clean(data))
    return scrub_to


def program(name):
    """The full path of the program name on PATH, or None. A match that is not an absolute path, or that sits in
    the folder the tool runs from, counts as missing: Windows looks in that folder before PATH (unless
    NoDefaultCurrentDirectoryInExePath is set) and a PATH entry can be ".", so a node.cmd or an ffmpeg.cmd left
    in the folder being shared would run otherwise."""
    found = shutil.which(name)
    if not found or not os.path.isabs(found):
        return None
    here, folder = os.getcwd(), os.path.dirname(found)
    if any(os.path.normcase(form(here)) == os.path.normcase(form(folder)) for form in (os.path.normpath,
                                                                                       os.path.realpath)):
        return None
    return found


def tool(name):
    found = program(name)
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
    matroska = os.path.splitext(path)[1].lower() in (".webm", ".mkv")
    # an empty tag names nothing, and a bare "Lavf" is the muxing app Matroska must write, which ffmpeg
    # leaves without its version under bitexact; a versioned one is still named, here and by the notes
    found = ["format " + k for k, v in (probe.get("format", {}).get("tags") or {}).items()
             if k not in VIDEO_PLAIN_FORMAT_TAGS and v != "" and not (matroska and v == "Lavf")]
    for stream in probe.get("streams", []):
        for k, v in (stream.get("tags") or {}).items():
            # Matroska and WebM carry a DURATION tag on every stream, a time and nothing else
            if (k == "language" or v == "" or (matroska and k == "DURATION")
                    or (k == "handler_name" and v in VIDEO_PLAIN_HANDLERS)):
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


HANDLERS = {".pdf": (pdf_check, pdf_scrub)}
HANDLERS.update({ext: (bytes_check(names), bytes_scrub(clean)) for ext, names, clean in (
    (".png", png_names, png_clean), (".jpg", jpeg_names, jpeg_clean), (".jpeg", jpeg_names, jpeg_clean),
    (".webp", webp_names, webp_clean), (".avif", avif_names, avif_clean), (".glb", glb_names, glb_clean),
    (".ktx2", ktx2_names, ktx2_clean),
    (".hdr", hdr_names, hdr_clean), (".ply", ply_names, ply_clean), (".spz", spz_names, spz_clean),
    (".svg", svg_names, svg_clean))})
HANDLERS[".sog"] = (sog_check, sog_scrub)
HANDLERS.update({ext: (office_check, office_scrub) for ext in OFFICE})
HANDLERS.update({ext: (video_check, video_scrub) for ext in VIDEO})
HANDLERS.update({ext: (font_check, font_scrub) for ext in FONTS})


def kind(path):
    return os.path.splitext(path)[1].lower()


def is_binary(path):
    """True for a file with a control byte in its first 8 KB, which no text, code or data file has."""
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
    except OSError:
        return True  # named, and the read fails again with its reason
    return any(byte in CONTROL for byte in head)


def files(targets):
    for target in targets:
        if os.path.isdir(target):
            for root, dirs, names in os.walk(target):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                for name in sorted(names):
                    path = os.path.join(root, name)
                    if kind(name) in KINDS or kind(name) in NOT_READ or is_binary(path):
                        yield path  # a binary file of a kind it does not know is named, never passed
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
    if argv[:1] == ["--font-proof"]:
        return font_proof_main(argv[1:])
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
