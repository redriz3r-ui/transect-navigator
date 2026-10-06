"""Build a deterministic local-install ZIP using only standard Python."""
from pathlib import Path
from configparser import ConfigParser
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]


def build(destination: Path) -> Path:
    """Package runtime sources and release notices with stable ZIP metadata."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    sources = [source for source in (ROOT / "transect_navigator").rglob("*")
               if source.is_file() and source.suffix in {".py", ".txt"}]
    sources.extend(ROOT / name for name in ("README.md", "CHANGELOG.md", "LICENSE"))
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for source in sorted(sources):
            name = source.relative_to(ROOT).as_posix() if source.is_relative_to(ROOT / "transect_navigator") \
                else f"transect_navigator/{source.name}"
            info = ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())
    return destination


if __name__ == "__main__":
    metadata = ConfigParser()
    metadata.read(ROOT / 'transect_navigator/metadata.txt')
    print(build(ROOT / 'dist' / f"transect_navigator-{metadata['general']['version']}.zip"))
