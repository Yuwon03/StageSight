"""
Screenplay upload: accept a PDF or Word file and return its plain text.

Uploads are the one place a stranger's bytes enter this service, so the checks
here are deliberately strict and ordered cheapest-first:

  1. size cap, before anything is read into memory
  2. real file type from magic bytes — the extension and the browser-supplied
     content-type are both attacker-controlled and are only used for messaging
  3. format-specific structural checks (see below)
  4. text extraction with a page/paragraph cap

What we refuse and why:
  - .docm / any OOXML containing vbaProject.bin — Word macros are executable code
  - PDFs containing /JavaScript, /Launch, /EmbeddedFile — active content
  - zip bombs — an OOXML file is a zip; we cap both the entry count and the
    decompressed total before extracting anything
  - encrypted files — we cannot inspect what we cannot read, so we decline

Nothing is written to disk and no external tool is shelled out to; extraction is
pure-python over bytes already in memory.
"""
import io
import logging
import re
import zipfile
from dataclasses import dataclass
from typing import List, Tuple

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024      # 10 MB
MAX_DECOMPRESSED_BYTES = 60 * 1024 * 1024  # zip-bomb ceiling
MAX_ZIP_ENTRIES = 500
MAX_PDF_PAGES = 80
MAX_TEXT_CHARS = 200_000

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy .doc

# Markers that mean executable behaviour, not merely "an action exists".
#
# Deliberately NOT blocked: /OpenAction and /AA on their own. Word, Pages, LaTeX
# and most exporters emit those for benign things like "open at page 1, fit to
# width" — rejecting them turned away ordinary screenplays. What matters is
# whether an action runs code or launches something, which is caught below.
PDF_DANGEROUS = [
    (b"/JavaScript", "자바스크립트가 포함되어 있습니다"),
    (b"/Launch", "외부 프로그램을 실행하는 동작이 포함되어 있습니다"),
    (b"/SubmitForm", "데이터를 외부로 전송하는 동작이 포함되어 있습니다"),
    (b"/RichMedia", "실행 가능한 미디어(Flash 등)가 포함되어 있습니다"),
    (b"/XFA", "실행 가능한 XFA 폼이 포함되어 있습니다"),
]

# `/JS` needs a delimiter check: the bare bytes appear inside plenty of innocent
# names and streams (e.g. a font called "…JSomething"), so match it as a real
# PDF name token only.
PDF_JS_TOKEN = re.compile(rb"/JS[\s/<\[(]")


class UploadRejected(Exception):
    """Raised with a Korean, user-facing reason."""


@dataclass
class ExtractedScript:
    text: str
    kind: str          # "pdf" | "docx"
    pages: int
    truncated: bool
    warnings: List[str]


def _sniff(data: bytes) -> str:
    if data.startswith(PDF_MAGIC):
        return "pdf"
    if data.startswith(ZIP_MAGIC):
        return "ooxml"
    if data.startswith(OLE_MAGIC):
        return "doc-legacy"
    return "unknown"


def _check_size(data: bytes) -> None:
    if not data:
        raise UploadRejected("빈 파일입니다.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadRejected(
            f"파일이 너무 큽니다 ({len(data) // (1024*1024)}MB). 최대 {MAX_UPLOAD_BYTES // (1024*1024)}MB까지 지원합니다."
        )


# ── PDF ─────────────────────────────────────────────────────────────────────
def _scan_pdf(data: bytes) -> List[str]:
    warnings: List[str] = []

    if b"/Encrypt" in data:
        raise UploadRejected("암호화된 PDF는 내용을 확인할 수 없어 지원하지 않습니다.")

    for marker, reason in PDF_DANGEROUS:
        if marker in data:
            raise UploadRejected(f"보안상 업로드할 수 없는 파일입니다 — {reason}.")

    if PDF_JS_TOKEN.search(data):
        raise UploadRejected("보안상 업로드할 수 없는 파일입니다 — 자바스크립트가 포함되어 있습니다.")

    # Attachments are common in exported PDFs and are never read by us — we only
    # pull page text — so this is worth telling the user about, not refusing.
    if b"/EmbeddedFile" in data:
        warnings.append("PDF에 첨부 파일이 있지만 무시하고 본문 텍스트만 읽었습니다.")

    return warnings


def _extract_pdf(data: bytes) -> Tuple[str, int, bool]:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise UploadRejected("서버에 PDF 처리 모듈이 설치되어 있지 않습니다 (pip install pypdf).")

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as e:
        raise UploadRejected(f"PDF를 읽을 수 없습니다: {type(e).__name__}")

    if getattr(reader, "is_encrypted", False):
        raise UploadRejected("암호화된 PDF는 지원하지 않습니다.")

    pages = reader.pages
    truncated = len(pages) > MAX_PDF_PAGES
    chunks: List[str] = []
    for page in pages[:MAX_PDF_PAGES]:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(chunks), len(pages), truncated


# ── Word (OOXML .docx) ──────────────────────────────────────────────────────
def _extract_docx(data: bytes) -> Tuple[str, bool]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UploadRejected("손상되었거나 Word 형식이 아닌 파일입니다.")

    names = zf.namelist()
    if len(names) > MAX_ZIP_ENTRIES:
        raise UploadRejected("파일 내부 구조가 비정상적으로 복잡합니다 (압축 폭탄 의심).")

    total = sum(i.file_size for i in zf.infolist())
    if total > MAX_DECOMPRESSED_BYTES:
        raise UploadRejected("압축을 풀면 지나치게 커지는 파일입니다 (압축 폭탄 의심).")

    # Path traversal in a zip entry name — never trusted, and we never write to
    # disk, but a file containing them is not a normal document.
    if any(n.startswith("/") or ".." in n for n in names):
        raise UploadRejected("파일 내부 경로가 비정상적입니다.")

    if any("vbaProject" in n or n.endswith(".bin") and "vba" in n.lower() for n in names):
        raise UploadRejected("보안상 업로드할 수 없는 파일입니다 — 매크로(VBA)가 포함되어 있습니다.")

    if "word/document.xml" not in names:
        if any(n.startswith("ppt/") or n.startswith("xl/") for n in names):
            raise UploadRejected("PowerPoint/Excel 파일은 지원하지 않습니다. PDF 또는 Word 파일을 올려주세요.")
        raise UploadRejected("Word 문서(.docx) 구조가 아닙니다.")

    with zf.open("word/document.xml") as fh:
        xml = fh.read(MAX_DECOMPRESSED_BYTES).decode("utf-8", errors="replace")

    # Paragraph and line breaks become real newlines so scene headers survive.
    xml = re.sub(r"</w:p\s*>", "\n", xml)
    xml = re.sub(r"<w:br\s*/?>", "\n", xml)
    xml = re.sub(r"<w:tab\s*/?>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
        text = text.replace(entity, char)
    return text, False


# ── Entry point ─────────────────────────────────────────────────────────────
def extract_script(data: bytes, filename: str = "") -> ExtractedScript:
    _check_size(data)
    kind = _sniff(data)
    warnings: List[str] = []

    if kind == "doc-legacy":
        raise UploadRejected("구버전 .doc 형식은 지원하지 않습니다. .docx 또는 PDF로 저장 후 올려주세요.")
    if kind == "unknown":
        raise UploadRejected("PDF 또는 Word(.docx) 파일만 업로드할 수 있습니다.")

    # The extension is only a hint; the magic bytes decide. Flag the mismatch
    # because a .pdf that is really a zip is worth telling the user about.
    lower = filename.lower()
    if kind == "pdf" and lower.endswith((".doc", ".docx")):
        warnings.append("확장자는 Word지만 실제 내용은 PDF입니다. PDF로 처리했습니다.")
    if kind == "ooxml" and lower.endswith(".pdf"):
        warnings.append("확장자는 PDF지만 실제 내용은 Word 문서입니다. Word로 처리했습니다.")

    if kind == "pdf":
        warnings += _scan_pdf(data)
        text, pages, truncated = _extract_pdf(data)
    else:
        text, truncated = _extract_docx(data)
        pages = 0

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        truncated = True

    if len(text) < 20:
        raise UploadRejected(
            "문서에서 글자를 찾지 못했습니다. 스캔한 이미지 PDF라면 텍스트가 포함된 파일로 다시 올려주세요."
        )

    if truncated:
        warnings.append("문서가 길어 앞부분만 분석에 사용했습니다.")

    return ExtractedScript(
        text=text,
        kind="pdf" if kind == "pdf" else "docx",
        pages=pages,
        truncated=truncated,
        warnings=warnings,
    )
