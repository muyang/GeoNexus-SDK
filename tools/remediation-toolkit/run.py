import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

wheel = next((Path(__file__).resolve().parent / "dist").glob("*.whl"))
# Keep packaged schema resources alive for the full CLI lifetime.
with TemporaryDirectory(prefix="geonode-repair-") as directory:
    with ZipFile(wheel) as archive:
        for name in archive.namelist():
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError("Unsafe wheel member")
        archive.extractall(directory)
    sys.path.insert(0, directory)
    from geonexus.remediation.__main__ import main
    code = main()
raise SystemExit(code)
