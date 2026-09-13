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
