"""STAC write-side demo (V1.0+): GeoCard -> STAC Item -> re-import.

Round trip: load the demo GeoCard, export it as a STAC Item (and a Catalog),
then import it back through the read adapter and verify the fields survive.

Usage:
    python run_demo.py [--output DIR]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from geonexus.adapters import (  # noqa: E402
    geocard_to_stac_catalog,
    geocard_to_stac_item,
    import_stac_item,
    save_stac_item,
)
from geonexus.geocard import load_geocard  # noqa: E402

DEMO_DIR = Path(__file__).resolve().parent
AMAZON_DIR = DEMO_DIR.parent / "amazon_ndvi"


def run(output_dir: str | None = None) -> dict:
    print("=" * 74)
    print("GeoNexus Reference Stack - STAC Write-Side Demo (V1.0+)")
    print("Publish GeoNexus assets into the STAC ecosystem")
    print("=" * 74)

    out_dir = Path(output_dir) if output_dir else DEMO_DIR / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load the demo GeoCard ------------------------------------------------ #
    card = load_geocard(AMAZON_DIR / "geocard.yaml")
    print(f"\n[1/4] Loaded GeoCard '{card.id}' (type={card.type})")

    # 2. Export as a STAC Item ------------------------------------------------ #
    print("[2/4] Exporting as STAC Item")
    item = geocard_to_stac_item(card, collection="geonexus-demo")
    item_path = out_dir / "sentinel-2-amazon.stac-item.json"
    save_stac_item(item, item_path)
    print(f"      id={item['id']} bbox={item.get('bbox')} assets={list(item['assets'])}")
    print(
        f"      properties keys: {sorted(k for k in item['properties'] if k != 'description')[:8]} ..."
    )

    # 3. Build a STAC Catalog ------------------------------------------------ #
    print("[3/4] Building STAC Catalog")
    result = geocard_to_stac_catalog([card], catalog_id="geonexus-demo", collection="geonexus-demo")
    catalog_path = out_dir / "catalog.json"
    save_stac_item(result["catalog"], catalog_path)
    item_links = [link for link in result["catalog"]["links"] if link["rel"] == "item"]
    print(f"      catalog '{result['catalog']['id']}' with {len(item_links)} item link(s)")

    # 4. Re-import through the read adapter and verify ------------------------- #
    print("[4/4] Re-importing the STAC Item as a GeoCard")
    imported = import_stac_item(str(item_path))
    assert imported.id == card.id
    card_spatial = card.spatial
    imported_spatial = imported.spatial
    assert card_spatial is not None and imported_spatial is not None
    assert imported_spatial.bbox == card_spatial.bbox
    assert imported.band_names() == card.band_names()
    imported.validate()
    print(
        f"      round-trip OK: id={imported.id} bands={imported.band_names()} "
        f"bbox={imported_spatial.bbox}"
    )

    print(f"\n      Outputs in {out_dir}:")
    print(f"        - {item_path.name} (STAC Item)")
    print(f"        - {catalog_path.name} (STAC Catalog)")
    print("\nSTAC write-side demo complete.")
    return {
        "demo": "stac-write",
        "item": str(item_path),
        "catalog": str(catalog_path),
        "roundtrip": {"id": imported.id, "bands": imported.band_names()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="STAC write-side demo")
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)
    run(output_dir=args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
