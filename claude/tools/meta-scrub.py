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

Other media, document and model kinds (.webm, .mkv, .gif, .heic, .svg, .mp3, .doc, .gltf, .obj, .fbx and
the like) are not read: each one is named as not read and the run exits 2, so a check never passes a file
it did not open.

    python meta-scrub.py <file or folder>...           # clear in place, one line per file changed
    python meta-scrub.py --check <file or folder>...   # change nothing; one line per file that still
                                                       # names something or could not be read

A folder is walked whole, skipping .git, node_modules and .venv. In a folder, a text, code or data file
(no control byte in its first 8 KB) is ignored, and a binary file of a kind the tool does not know (a
font, an archive, a model) is named as not read, the same as when it is named directly; UTF-16 text
holds NUL bytes, so it counts as binary. A file is replaced only through a temporary file in its own
folder, checked again before a rename, so a run that stops halfway, or a
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
import xml.etree.ElementTree
import zipfile
import zlib

OFFICE = {".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm"}
VIDEO = {".mp4", ".mov", ".m4a", ".m4v", ".webm", ".mkv"}
KINDS = OFFICE | VIDEO | {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".glb", ".ktx2", ".hdr", ".ply", ".sog", ".spz",
                          ".svg"}
# kinds that can carry a name too, which this tool does not clear: named, never passed in silence
NOT_READ = {".avi", ".wmv", ".flv", ".gif", ".heic", ".heif", ".avif", ".tif",
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
    (".webp", webp_names, webp_clean), (".glb", glb_names, glb_clean), (".ktx2", ktx2_names, ktx2_clean),
    (".hdr", hdr_names, hdr_clean), (".ply", ply_names, ply_clean), (".spz", spz_names, spz_clean),
    (".svg", svg_names, svg_clean))})
HANDLERS[".sog"] = (sog_check, sog_scrub)
HANDLERS.update({ext: (office_check, office_scrub) for ext in OFFICE})
HANDLERS.update({ext: (video_check, video_scrub) for ext in VIDEO})


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
