"""Text extraction and OCR providers.

Concrete implementations behind the interfaces in
:mod:`claimiq.ai.domain.providers`. Heavy dependencies (pdfplumber, torch,
docTR) are imported **inside methods**, not at module import time, for two
reasons: the web process never OCRs anything and should not pay a multi-second
torch import to serve a request, and a deployment without OCR provisioned
should fail when OCR is attempted rather than at startup.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Sequence

from claimiq.ai.domain.providers import ExtractedPage, ModelSpec, OCRProvider
from claimiq.core.domain.errors import ExtractionError, OCRError, ProviderUnavailableError
from claimiq.core.logging import get_logger

logger = get_logger("ingestion.extraction")

#: A page with fewer extractable characters than this is treated as having no
#: usable text layer. Chosen well below a realistic page of contract prose
#: (2000+ characters) but above the handful of characters a scanned page yields
#: from its header or a stamped page number.
DIGITAL_TEXT_THRESHOLD = 100


@dataclass(frozen=True)
class PageAnalysis:
    """Per-page classification produced by the analysis stage."""

    page_number: int
    digital_character_count: int
    needs_ocr: bool
    has_tables: bool
    width: float | None = None
    height: float | None = None
    rotation: int = 0


class PdfPlumberExtractor:
    """Digital text extraction and page analysis via pdfplumber."""

    name = "pdfplumber"

    def analyse(self, file_path: str) -> list[PageAnalysis]:
        """Classify every page as digital or scanned.

        Per page, not per document. The prototype sampled the first five pages
        and applied one verdict to the whole file
        (`../backend/contract_parser.py:83`), which misreads the common case of
        a contract set that opens with a digital agreement and continues with
        scanned appendices — those appendices extracted as blank.
        """
        import pdfplumber

        analyses: list[PageAnalysis] = []
        try:
            with pdfplumber.open(file_path) as pdf:
                for index, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text() or ""
                    char_count = len(text.strip())
                    try:
                        tables = page.find_tables()
                    except Exception:  # noqa: BLE001 - table finding is best-effort
                        tables = []
                    analyses.append(
                        PageAnalysis(
                            page_number=index,
                            digital_character_count=char_count,
                            needs_ocr=char_count < DIGITAL_TEXT_THRESHOLD,
                            has_tables=bool(tables),
                            width=float(page.width) if page.width else None,
                            height=float(page.height) if page.height else None,
                            rotation=int(getattr(page, "rotation", 0) or 0),
                        )
                    )
        except Exception as exc:
            raise ExtractionError(
                "The PDF could not be opened for analysis. It may be corrupt or "
                "password-protected.",
                details={"reason": type(exc).__name__},
            ) from exc

        if not analyses:
            raise ExtractionError("The PDF contains no pages.")
        return analyses

    def extract(
        self, file_path: str, *, page_numbers: Sequence[int] | None = None
    ) -> list[ExtractedPage]:
        """Extract the text layer from the requested pages."""
        import pdfplumber

        wanted = set(page_numbers) if page_numbers else None
        pages: list[ExtractedPage] = []
        try:
            with pdfplumber.open(file_path) as pdf:
                for index, page in enumerate(pdf.pages, start=1):
                    if wanted is not None and index not in wanted:
                        continue
                    text = page.extract_text() or ""
                    pages.append(
                        ExtractedPage(
                            page_number=index,
                            text=text.strip(),
                            width=float(page.width) if page.width else None,
                            height=float(page.height) if page.height else None,
                        )
                    )
        except Exception as exc:
            raise ExtractionError(
                "Text extraction failed.", details={"reason": type(exc).__name__}
            ) from exc
        return pages

    def extract_tables(
        self, file_path: str, *, page_numbers: Sequence[int]
    ) -> list[tuple[int, list[list[str | None]]]]:
        """Extract table cell grids from the requested digital pages.

        Returns ``(page_number, rows)`` per table, in page order. Only pages
        with a text layer are meaningful here: pdfplumber reads ruling lines
        and character positions, neither of which a scanned page has.
        """
        import pdfplumber

        wanted = set(page_numbers)
        tables: list[tuple[int, list[list[str | None]]]] = []
        try:
            with pdfplumber.open(file_path) as pdf:
                for index, page in enumerate(pdf.pages, start=1):
                    if index not in wanted:
                        continue
                    for rows in page.extract_tables() or []:
                        if rows and any(any(cell for cell in row) for row in rows):
                            tables.append((index, rows))
        except Exception as exc:
            raise ExtractionError(
                "Table extraction failed.", details={"reason": type(exc).__name__}
            ) from exc
        return tables


def image_page_analyses(file_path: str) -> list[PageAnalysis]:
    """Page analysis for an image file: every frame is a scanned page."""
    from PIL import Image

    analyses: list[PageAnalysis] = []
    try:
        with Image.open(file_path) as image:
            frames = int(getattr(image, "n_frames", 1) or 1)
            for index in range(frames):
                image.seek(index)
                analyses.append(
                    PageAnalysis(
                        page_number=index + 1,
                        digital_character_count=0,
                        needs_ocr=True,
                        has_tables=False,
                        width=float(image.width),
                        height=float(image.height),
                    )
                )
    except Exception as exc:
        raise ExtractionError(
            "The image could not be opened. It may be corrupt.",
            details={"reason": type(exc).__name__},
        ) from exc
    return analyses


class OfficeExtractor:
    """Text from DOCX, XLSX and plain-text files, split into pseudo-pages.

    These formats have no pages. Text is paginated on paragraph boundaries
    (see :func:`claimiq.ingestion.domain.layout.paginate_text`) so citations
    and the viewer work the same way they do for PDFs.
    """

    name = "office"

    def extract(self, file_path: str, kind) -> list[ExtractedPage]:
        from claimiq.ingestion.domain.layout import FileKind, paginate_text

        try:
            if kind is FileKind.DOCX:
                text = self._docx_text(file_path)
            elif kind is FileKind.XLSX:
                text = self._xlsx_text(file_path)
            elif kind is FileKind.TEXT:
                text = self._plain_text(file_path)
            else:
                raise ExtractionError(
                    "This file is not an office or text document.",
                    details={"file_kind": getattr(kind, "value", str(kind))},
                )
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError(
                "The document could not be read. It may be corrupt or password-protected.",
                details={"reason": type(exc).__name__},
            ) from exc

        return [
            ExtractedPage(page_number=index, text=page)
            for index, page in enumerate(paginate_text(text), start=1)
        ]

    @staticmethod
    def _docx_text(file_path: str) -> str:
        """Paragraphs and tables in body order."""
        from docx import Document as DocxDocument
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        from claimiq.ingestion.domain.layout import render_table_text

        document = DocxDocument(file_path)
        blocks: list[str] = []
        for child in document.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                text = Paragraph(child, document).text.strip()
                if text:
                    blocks.append(text)
            elif tag == "tbl":
                table = Table(child, document)
                rendered = render_table_text([cell.text for cell in row.cells] for row in table.rows)
                if rendered:
                    blocks.append(rendered)
        return "\n\n".join(blocks)

    @staticmethod
    def _xlsx_text(file_path: str) -> str:
        """Each sheet as a titled table. Formulas read as their cached values."""
        from openpyxl import load_workbook

        from claimiq.ingestion.domain.layout import render_table_text

        workbook = load_workbook(file_path, read_only=True, data_only=True)
        try:
            blocks: list[str] = []
            for sheet in workbook.worksheets:
                rendered = render_table_text(sheet.iter_rows(values_only=True))
                if rendered:
                    blocks.append(f"Sheet: {sheet.title}\n\n{rendered}")
            return "\n\n".join(blocks)
        finally:
            workbook.close()

    @staticmethod
    def _plain_text(file_path: str) -> str:
        with open(file_path, "rb") as handle:
            raw = handle.read()
        for encoding in ("utf-8-sig", "cp1252"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")


class DocTROCRProvider(OCRProvider):
    """OCR via docTR.

    The model is loaded lazily and cached on the instance, because loading is
    expensive and a worker processes many documents in its lifetime.
    """

    provider_key = "doctr"

    def __init__(self, *, batch_size: int = 8) -> None:
        self._model = None
        self._device: str | None = None
        self._batch_size = batch_size

    def is_available(self) -> bool:
        try:
            import doctr  # noqa: F401
            import torch  # noqa: F401
        except ImportError:
            return False
        return True

    def list_models(self) -> Sequence[ModelSpec]:
        if not self.is_available():
            return ()
        return (
            ModelSpec(
                name="db_resnet50+crnn_vgg16_bn",
                provider=self.provider_key,
                display_name="docTR (DB ResNet-50 / CRNN VGG-16)",
            ),
        )

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            import torch
            from doctr.models import ocr_predictor
        except ImportError as exc:
            raise ProviderUnavailableError(
                "OCR was requested but docTR is not installed in this "
                "deployment. Scanned documents cannot be processed.",
                details={"remedy": "Install python-doctr and torch in the backend image."},
            ) from exc

        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("ocr.model_loading", extra={"device": self._device})
        model = ocr_predictor(
            det_arch="db_resnet50", reco_arch="crnn_vgg16_bn", pretrained=True
        ).to(self._device)
        self._model = model
        return model

    def extract(
        self, file_path: str, *, page_numbers: Sequence[int] | None = None
    ) -> list[ExtractedPage]:
        """OCR the requested pages.

        ``page_numbers`` is honoured strictly: only those pages are rendered
        and processed. That is what makes the OCR stage resumable — a job that
        died at page 400 of 500 restarts at 400, not at 1.
        """
        from doctr.io import DocumentFile

        model = self._load()

        try:
            document = DocumentFile.from_pdf(file_path)
        except Exception as exc:
            raise OCRError(
                "The PDF could not be rendered for OCR.",
                details={"reason": type(exc).__name__},
            ) from exc

        total = len(document)
        targets = (
            sorted({p for p in page_numbers if 1 <= p <= total})
            if page_numbers
            else list(range(1, total + 1))
        )
        if not targets:
            return []

        results: list[ExtractedPage] = []
        started = time.perf_counter()

        for offset in range(0, len(targets), self._batch_size):
            batch_numbers = targets[offset : offset + self._batch_size]
            batch = [document[n - 1] for n in batch_numbers]
            try:
                output = model(batch)
            except Exception as exc:
                raise OCRError(
                    "OCR failed while processing a page batch.",
                    details={
                        "reason": type(exc).__name__,
                        # The caller checkpoints from this, so a retry resumes
                        # at the first page of the failed batch.
                        "failed_from_page": batch_numbers[0],
                    },
                ) from exc

            for page_number, page in zip(batch_numbers, output.pages):
                lines: list[str] = []
                confidences: list[float] = []
                for block in page.blocks:
                    for line in block.lines:
                        words = [w.value for w in line.words]
                        if words:
                            lines.append(" ".join(words))
                        confidences.extend(float(w.confidence) for w in line.words)

                results.append(
                    ExtractedPage(
                        page_number=page_number,
                        text="\n".join(lines).strip(),
                        confidence=(
                            sum(confidences) / len(confidences) if confidences else None
                        ),
                    )
                )

        logger.info(
            "ocr.completed",
            extra={
                "pages": len(results),
                "device": self._device,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return results


class RapidOCRProvider(OCRProvider):
    """OCR via RapidOCR: PP-OCRv4 detection and recognition on ONNX Runtime.

    The default offline engine. Its models ship inside the Python wheel, it
    runs on CPU, and it needs no system binary and no download at runtime —
    the properties an air-gapped installation needs. docTR is preferred when
    installed (see :func:`build_ocr_provider`).

    Known limitation, surfaced rather than hidden: recognition occasionally
    drops the space between words ("theContractor"). Lexical search recall on
    OCR'd pages is reduced accordingly; the extraction quality score reflects
    OCR provenance.
    """

    provider_key = "rapidocr"

    #: Render scale for OCR. Measured on the scanned N-55 contract: at 2.0
    #: (144 dpi) recognition dropped inter-word spaces on 5 of ~313 words of a
    #: page; at 3.0 (216 dpi) none, for ~7% more time.
    DEFAULT_RENDER_SCALE = 3.0

    def __init__(self, *, render_scale: float = DEFAULT_RENDER_SCALE) -> None:
        self._engine = None
        self._render_scale = render_scale

    def is_available(self) -> bool:
        try:
            import rapidocr_onnxruntime  # noqa: F401
        except ImportError:
            return False
        return True

    def list_models(self) -> Sequence[ModelSpec]:
        if not self.is_available():
            return ()
        return (
            ModelSpec(
                name="ppocr-v4-onnx",
                provider=self.provider_key,
                display_name="RapidOCR (PP-OCRv4, ONNX Runtime)",
            ),
        )

    def _load(self):
        if self._engine is not None:
            return self._engine
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise ProviderUnavailableError(
                "OCR was requested but RapidOCR is not installed in this deployment.",
                details={"remedy": "Install rapidocr_onnxruntime in the backend environment."},
            ) from exc
        logger.info("ocr.model_loading", extra={"provider": self.provider_key})
        self._engine = RapidOCR()
        return self._engine

    def extract(
        self, file_path: str, *, page_numbers: Sequence[int] | None = None
    ) -> list[ExtractedPage]:
        import numpy as np

        from claimiq.ingestion.domain.layout import assemble_ocr_lines
        from claimiq.ingestion.providers.pages import iter_page_images, sniff_file_kind

        engine = self._load()
        kind = sniff_file_kind(file_path)
        started = time.perf_counter()
        results: list[ExtractedPage] = []

        for page_number, image in iter_page_images(
            file_path, kind, page_numbers, scale=self._render_scale
        ):
            try:
                detections, _elapsed = engine(np.asarray(image))
            except Exception as exc:
                raise OCRError(
                    "OCR failed on a page.",
                    details={"reason": type(exc).__name__, "failed_from_page": page_number},
                ) from exc
            text, confidence = assemble_ocr_lines(
                [(d[0], d[1], d[2]) for d in (detections or [])]
            )
            results.append(
                ExtractedPage(
                    page_number=page_number,
                    text=text,
                    confidence=confidence,
                    width=float(image.width),
                    height=float(image.height),
                )
            )

        logger.info(
            "ocr.completed",
            extra={
                "provider": self.provider_key,
                "pages": len(results),
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return results


def build_ocr_provider() -> OCRProvider:
    """The OCR engine for this deployment: docTR if installed, else RapidOCR.

    Raises:
        ProviderUnavailableError: neither engine is installed. Scanned pages
            then fail their OCR stage with this message, rather than being
            reported as processed with no text.
    """
    doctr = DocTROCRProvider()
    if doctr.is_available():
        return doctr
    rapid = RapidOCRProvider()
    if rapid.is_available():
        return rapid
    raise ProviderUnavailableError(
        "Scanned pages need OCR, and no OCR engine is installed in this deployment.",
        details={"remedy": "Install rapidocr_onnxruntime (CPU, offline) or python-doctr with torch."},
    )
