"""Build an integration ZIP from an explicit file-type allowlist."""
import json
from pathlib import Path
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    component = root / "custom_components" / "bmc_e5"
    version = json.loads((component / "manifest.json").read_text(encoding="utf8"))["version"]
    destination = root / "dist" / f"bmc_e5-{version}.zip"
    destination.parent.mkdir(exist_ok=True)
    files = sorted(p for p in component.rglob("*") if p.is_file() and "__pycache__" not in p.parts
                   and (p.suffix in {".py", ".json"} or (p.parent == component / "brand" and p.suffix == ".png")))
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(component.parent))
    print(f"Built {destination.name}: {len(files)} files")


if __name__ == "__main__":
    main()
