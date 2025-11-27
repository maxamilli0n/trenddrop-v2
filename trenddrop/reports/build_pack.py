"""
Build a weekly pack ZIP (PDF + CSV) for a given provider, suitable for Gumroad.

Usage:
    python -m trenddrop.reports.build_pack --provider ebay

This:
  - Reads out/artifacts.json to find the latest PDF/CSV URLs for that provider
  - Downloads those files into out/
  - Creates out/weekly-pack-<provider>.zip containing:
        Top50-<Provider>.pdf
        Top50-<Provider>.csv
"""

import argparse
import json
import pathlib
import zipfile

import requests


ROOT = pathlib.Path(__file__).resolve().parents[2]  # project root
OUT_DIR = ROOT / "out"
ARTIFACTS_PATH = OUT_DIR / "artifacts.json"


def _load_artifacts() -> dict:
    if not ARTIFACTS_PATH.exists():
        raise SystemExit(
            f"artifacts.json not found at {ARTIFACTS_PATH}. "
            "Run `python -m trenddrop.reports.generate_reports` first."
        )
    with ARTIFACTS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _download(url: str, dest: pathlib.Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"[pack] downloading {url} -> {dest}")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    dest.write_bytes(resp.content)


def build_pack(provider: str) -> pathlib.Path:
    provider = provider.lower()
    artifacts = _load_artifacts()

    if provider not in artifacts:
        raise SystemExit(
            f"Provider '{provider}' not found in artifacts.json keys: {list(artifacts.keys())}"
        )

    data = artifacts[provider]
    pdf_url = data.get("pdf_url")
    csv_url = data.get("csv_url")

    if not pdf_url or not csv_url:
        raise SystemExit(f"artifacts for provider '{provider}' must contain pdf_url and csv_url")

    provider_label = provider.capitalize()
    pdf_path = OUT_DIR / f"Top50-{provider_label}.pdf"
    csv_path = OUT_DIR / f"Top50-{provider_label}.csv"

    _download(pdf_url, pdf_path)
    _download(csv_url, csv_path)

    zip_path = OUT_DIR / f"weekly-pack-{provider}.zip"
    print(f"[pack] writing ZIP -> {zip_path}")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(pdf_path, arcname=pdf_path.name)
        zf.write(csv_path, arcname=csv_path.name)

    print(f"[pack] built pack for {provider}: {zip_path}")
    return zip_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build weekly pack ZIP for a provider")
    parser.add_argument(
        "--provider",
        default="ebay",
        help="Provider to build pack for (ebay, amazon, aliexpress, ...). Default: ebay",
    )
    args = parser.parse_args(argv)

    build_pack(args.provider)


if __name__ == "__main__":
    main()

