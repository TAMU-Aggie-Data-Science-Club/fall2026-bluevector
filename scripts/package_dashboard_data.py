"""Package existing artifacts locally; this never uploads data or commits it."""
from pathlib import Path
import hashlib
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.cloud_data import FILES


def main():
    for name in FILES:
        if not (ROOT / name).is_file():
            raise FileNotFoundError(f'Missing dashboard artifact: {name}')
    destination = ROOT / 'data/processed/dashboard_bundle.zip'
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(ROOT / name, name)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.sha256').write_text(digest + '\n', encoding='utf-8')
    print(f'Bundle: {destination}')
    print(f'DATA_BUNDLE_SHA256 = "{digest}"')
    print('Keep local until the data owner approves a hosting destination and access policy.')


if __name__ == '__main__':
    main()
