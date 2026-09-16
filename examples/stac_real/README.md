# Real Sentinel-2 data demo (V0.4)

This demo proves the **STAC adapter** on **real data**: it fetches a real
Sentinel-2 L2A scene from [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/)
(an open STAC API + COG store, no account needed for anonymous access), imports
it as a GeoCard, and computes **real NDVI** from the actual B04/B08 pixels.

## What it does

1. Fetches a REAL Sentinel-2 L2A item from the Planetary Computer STAC API
   (default: the most recent scene over the Amazon, tile `T21NWA`,
   2026-08-19).
2. Imports it as a GeoCard (`geonexus.adapters.import_stac_item`) — real
   metadata: scene date, bbox, CRS (EPSG:32621), 14 spectral bands.
3. Signs the COG asset URLs with an anonymous SAS token and reads a
   **window** of real B04/B08 pixels (windowed COG reads keep the download
   small — ~2.5 km patch at 10 m resolution).
4. Computes NDVI and writes `ndvi_real.tif` (tagged `GEONEXUS_REAL=TRUE`),
   prints summary statistics.

If the real pixel download fails (no network / auth / flaky link), the demo
**fails loudly and degrades** to synthetic execution with a clear label —
it never claims synthetic data is real.

## Run it

```bash
cd /path/to/GeoNexus-SDK
.venv/bin/geonexus demo stac-real            # needs network access
# or with a specific item:
.venv/bin/geonexus demo stac-real --item https://.../items/S2B_...
```

Observed result on a reference run (2026-08-19 T21NWA, Amazon):

```
REAL NDVI computed: mean=0.342, vegetation_fraction=0.608
  mean 0.3423 | median 0.4104 | std 0.1890 | min -0.0533 | max 0.6078
```

## Notes & known constraints

- **SAS signing**: Planetary Computer assets need an anonymous SAS token
  (`GET https://planetarycomputer.microsoft.com/api/sas/v1/token/{collection}`).
  The token **is** a full Azure SAS query string and must be appended
  directly to the asset href (`href?st=...&se=...&sig=...`), not wrapped in
  `?token=`.
- **Flaky links**: this machine's network truncates large COG tiles, so the
  demo reads a coarse overview to locate valid pixels, then reads a small
  window with GDAL retries enabled. On a reliable network you can increase
  `window_size` in `run_demo.py`.
- **Licensing**: Sentinel-2 data usage is governed by the
  [ESA Sentinel Data Legal Notice](https://sentinel.esa.int/documents/247904/690755/Sentinel_Data_Legal_Notice);
  Planetary Computer mirrors the data under those terms.

## Files

- `run_demo.py` — the demo (fetch item → GeoCard → real NDVI → stats).
- The STAC adapter itself lives in `src/geonexus/adapters/stac.py`; use
  `geonexus card import-stac <url-or-file>` to import any STAC item as a
  GeoCard without running the pixel demo.
