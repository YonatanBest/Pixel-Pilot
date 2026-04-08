from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("pixelpilot.easyocr_onnx")


def import_easyocr_onnx() -> Any:
    import torchfree_ocr
    from torchfree_ocr import utils as torchfree_utils

    progress_factory = getattr(torchfree_utils, "printProgressBar", None)
    if callable(progress_factory) and not getattr(progress_factory, "_pixelpilot_ascii", False):
        def ascii_progress_bar(
            prefix: str = "",
            suffix: str = "",
            decimals: int = 1,
            length: int = 100,
            fill: str = "#",
        ):
            return progress_factory(
                prefix=prefix,
                suffix=suffix,
                decimals=decimals,
                length=length,
                fill=fill,
            )

        ascii_progress_bar._pixelpilot_ascii = True  # type: ignore[attr-defined]
        torchfree_utils.printProgressBar = ascii_progress_bar
        logger.debug("Patched torchfree_ocr progress output to ASCII.")

    return torchfree_ocr
