# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""Minimal PDFs for the planted-leak fixtures: a text-and-graphics page, and a raster-only wrapper."""
import subprocess
import zlib


def _assemble(objects: list[bytes], producer: bytes | None) -> bytes:
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    info = b""
    if producer is not None:
        offsets.append(len(out))
        out += b"%d 0 obj\n<< /Producer (%s) >>\nendobj\n" % (len(objects) + 1, producer)
        info = b" /Info %d 0 R" % (len(objects) + 1)
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(offsets) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R%s >>\nstartxref\n%d\n%%%%EOF\n" % (len(offsets) + 1, info, start)
    return bytes(out)


def page_pdf(lines: list[str], boxes: list[tuple[int, int, int, int]], producer: str | None = None,
             compress: bool = False) -> bytes:
    """One US-Letter page: black filled boxes, then one line of Helvetica per entry in `lines`."""
    ops = b"".join(b"%d %d %d %d re f\n" % b for b in boxes)
    y = 740
    for line in lines:
        ops += b"BT /F1 11 Tf 40 %d Td (%s) Tj ET\n" % (y, line.encode("latin-1"))
        y -= 16
    if compress:
        stream = b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(zlib.compress(ops)) + zlib.compress(ops) + b"\nendstream"
    else:
        stream = b"<< /Length %d >>\nstream\n" % len(ops) + ops + b"\nendstream"
    return _assemble([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        stream,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ], producer.encode() if producer else None)


def raster_pdf(pgm: bytes, dpi: int = 0) -> bytes:
    """A page that is only a grey image (no text layer). dpi=0 sizes it as US Letter, else width*72/dpi points."""
    parts = pgm.split(None, 4)
    width, height, data = int(parts[1]), int(parts[2]), parts[4][-(int(parts[1]) * int(parts[2])):]
    img = zlib.compress(data)
    pw, ph = (612, 792) if not dpi else (width * 72 / dpi, height * 72 / dpi)
    ops = b"q %.3f 0 0 %.3f 0 0 cm /Im0 Do Q" % (pw, ph)
    return _assemble([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.3f %.3f] /Contents 4 0 R "
        b"/Resources << /XObject << /Im0 5 0 R >> >> >>" % (pw, ph),
        b"<< /Length %d >>\nstream\n" % len(ops) + ops + b"\nendstream",
        b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8 "
        b"/Filter /FlateDecode /Length %d >>\nstream\n" % (width, height, len(img)) + img + b"\nendstream",
    ], None)


def rasterise(pdf_path, dpi: int, tmp_prefix) -> bytes:
    """The page as a grey PGM at `dpi` (pdftoppm)."""
    subprocess.run(["pdftoppm", "-gray", "-r", str(dpi), "-singlefile", str(pdf_path), str(tmp_prefix)],
                   check=True, capture_output=True)
    return open(f"{tmp_prefix}.pgm", "rb").read()
