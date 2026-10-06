"""Publish a navigation folder only after all staged files validate."""
from pathlib import Path
import os
import re
import tempfile
from typing import Callable


def package_destination(parent: Path, name: str) -> Path:
    """Require an existing output directory and a portable new folder name."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}', name) or name.endswith(('.', ' ')):
        raise ValueError('Package name must be 1–80 letters/numbers/spaces/._- and start with a letter or number')
    if name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
        raise ValueError('Choose a package name that is not a reserved device name')
    if not parent.is_dir():
        raise ValueError('Choose an existing output folder')
    destination = parent.resolve() / name
    if destination.exists() or destination.is_symlink():
        raise ValueError('A package with that name already exists. Choose a new name; nothing will be overwritten.')
    return destination


def publish_package(parent: Path, name: str, write_and_validate: Callable[[Path], None]) -> Path:
    """Stage beside the destination, reserve exclusively, then rename into place.

    Existing packages are never overwritten. Failed writes are removed from staging.
    The reserved empty directory is removed only if still empty on publish failure.
    """
    destination = package_destination(parent, name)
    with tempfile.TemporaryDirectory(prefix='.transect-nav-', dir=destination.parent) as temporary:
        stage = Path(temporary) / 'package'
        stage.mkdir()
        write_and_validate(stage)
        if os.name == 'nt':
            # Windows rename fails if any destination exists (unlike POSIX rename).
            os.rename(stage, destination)
            return destination
        destination.mkdir()  # Exclusive reservation also closes the initial existence-check race.
        try:
            os.replace(stage, destination)
        except OSError:
            try:
                destination.rmdir()
            except OSError:
                pass
            raise
    return destination
