"""Write tiny text PDFs (one slide per page, Helvetica) for tests. No dependencies."""

from __future__ import annotations

from pathlib import Path


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(path: Path, pages: list[list[str]]) -> Path:
    """Each page is a list of lines; the first line is drawn large like a slide title."""
    objs: list[bytes] = []
    n = len(pages)
    page_ids = [3 + 2 * i for i in range(n)]
    font_id = 3 + 2 * n
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    for i, lines in enumerate(pages):
        ops = ["BT"]
        y = 480
        for k, line in enumerate(lines):
            size = 32 if k == 0 else 20
            ops.append(f"/F1 {size} Tf 1 0 0 1 50 {y} Tm ({_esc(line)}) Tj")
            y -= 60 if k == 0 else 34
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        resources = f"/Resources << /Font << /F1 {font_id} 0 R >> >>"
        page = f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 960 540] {resources} "
        objs.append(f"{page}/Contents {page_ids[i] + 1} 0 R >>".encode())
        objs.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objs.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path
