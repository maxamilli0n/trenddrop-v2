"""
Utility helpers for assembling provider-specific ZIP packs.
"""

from __future__ import annotations

import zipfile
from pathlib import Path


def create_zip_pack(provider: str, pdf_path: Path, csv_path: Path | None) -> Path:
    """
    Bundle the provider's weekly PDF/CSV into a Gumroad-ready ZIP.

    Returns the path to the generated ZIP inside the out/ directory.
    """
    provider_key = provider.strip().lower()
    out_dir = Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{provider_key}_weekly_pack.zip"

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(pdf_path, arcname=f"{provider_key}_weekly.pdf")
        if csv_path and csv_path.exists():
            archive.write(csv_path, arcname=f"{provider_key}_weekly.csv")

    return zip_path


from typing import Iterable, Tuple


def create_master_zip(
    csv_path: Path,
    pdf_path: Path,
    provider_zips: Iterable[Tuple[str, Path]] | None = None,
) -> Path:
    """
    Bundle the master Top 25 CSV/PDF into a single ZIP artifact.
    """
    out_dir = Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / "master_top25_pack.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(pdf_path, arcname="master_top25.pdf")
        archive.write(csv_path, arcname="master_top25.csv")
        if provider_zips:
            for provider, pack_path in provider_zips:
                if not pack_path.exists():
                    continue
                archive.write(pack_path, arcname=f"{provider}_weekly_pack.zip")
    return zip_path


