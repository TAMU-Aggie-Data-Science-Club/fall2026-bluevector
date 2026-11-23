"""Restore the approved dashboard artifacts from a versioned HTTPS ZIP."""
from pathlib import Path
import hashlib
import io
import os
import tempfile
import urllib.request
import zipfile

FILES = (
    'data/interim/od_matrix.csv',
    'data/processed/chokepoint_weekly_attributes.parquet',
)
MAX_BYTES = 200 * 1024 * 1024


def restore_bundle(root: Path, url: str, expected_sha256: str) -> str:
    """Verify before writing; permit only the two declared data artifacts."""
    expected_sha256 = expected_sha256.strip().lower()
    if len(expected_sha256) != 64 or any(c not in '0123456789abcdef' for c in expected_sha256):
        raise ValueError('DATA_BUNDLE_SHA256 must contain 64 hexadecimal characters.')
    if not url.startswith('https://'):
        raise ValueError('DATA_BUNDLE_URL must use HTTPS.')
    marker = root / 'data' / '.dashboard_bundle_sha256'
    if marker.exists() and marker.read_text().strip() == expected_sha256 and all((root / n).exists() for n in FILES):
        return expected_sha256
    with urllib.request.urlopen(url, timeout=60) as response:
        if not response.geturl().startswith('https://'):
            raise ValueError('The data download redirected to an insecure URL.')
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError('The dashboard bundle exceeds 200 MiB.')
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError('The downloaded dashboard bundle failed its SHA-256 check.')
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        if sorted(names) != sorted(FILES):
            raise ValueError('The bundle must contain exactly the two declared dashboard files.')
        if sum(i.file_size for i in archive.infolist()) > MAX_BYTES:
            raise ValueError('The expanded dashboard bundle exceeds 200 MiB.')
        # Read all members first, verifying their ZIP checksums before changing local files.
        contents = {name: archive.read(name) for name in FILES}
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.unlink(missing_ok=True)
    for name, content in contents.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
            tmp.write(content)
            staging = Path(tmp.name)
        try:
            os.replace(staging, target)
        finally:
            staging.unlink(missing_ok=True)
    marker.write_text(expected_sha256 + '\n', encoding='utf-8')
    return expected_sha256
