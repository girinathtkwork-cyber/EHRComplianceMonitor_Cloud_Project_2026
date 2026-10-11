"""
scripts/build_lambda.py — Build the Lambda deployment zip
-----------------------------------------------------------
Builds build/stage_a_lambda.zip using Python's zipfile module.

Usage:
    python scripts/build_lambda.py
"""

import os
import zipfile
from pathlib import Path


def build_lambda_zip():
    project_root = Path(__file__).resolve().parent.parent
    build_dir = project_root / "build"
    build_dir.mkdir(exist_ok=True)
    zip_path = build_dir / "stage_a_lambda.zip"

    # Ensure build/ is in .gitignore
    gitignore_path = project_root / ".gitignore"
    if gitignore_path.exists():
        content = gitignore_path.read_text(encoding="utf-8")
        if "build/" not in content:
            with open(gitignore_path, "a", encoding="utf-8") as f:
                f.write("\n# Lambda build artifacts\nbuild/\n")
            print("Added build/ to .gitignore")
    else:
        gitignore_path.write_text("build/\n", encoding="utf-8")

    print("Building Lambda zip...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Handler at zip root
        handler_path = project_root / "src" / "aws" / "lambda" / "stage_a_handler.py"
        if handler_path.exists():
            zf.write(handler_path, "stage_a_handler.py")
            print(f"  + stage_a_handler.py")
        else:
            print(f"  WARNING: {handler_path} not found!")

        # 2. backend/stage_a/ package (from src/backend/stage_a/)
        stage_a_dir = project_root / "src" / "backend" / "stage_a"
        backend_init = project_root / "src" / "backend" / "__init__.py"

        # Add backend/__init__.py
        if backend_init.exists():
            zf.write(backend_init, "backend/__init__.py")
            print(f"  + backend/__init__.py")

        # Add backend/stage_a/**/*.py
        if stage_a_dir.exists():
            for py_file in stage_a_dir.rglob("*.py"):
                arcname = "backend/stage_a/" + py_file.relative_to(stage_a_dir).as_posix()
                zf.write(py_file, arcname)
                print(f"  + {arcname}")

        # 3. Reference JSON files
        ref_dir = project_root / "dataset" / "reference"
        for ref_name in ("rxnorm_lexicon.json", "icd10cm_codes.json", "icd10cm_index.json"):
            ref_path = ref_dir / ref_name
            if ref_path.exists():
                arcname = f"dataset/reference/{ref_name}"
                zf.write(ref_path, arcname)
                size_mb = ref_path.stat().st_size / (1024 * 1024)
                print(f"  + {arcname} ({size_mb:.2f} MB)")

        # 4. Warmed cache (if exists)
        cache_path = project_root / "dataset" / "cache" / "rxnorm_cache.json"
        if cache_path.exists():
            zf.write(cache_path, "dataset/cache/rxnorm_cache.json")
            size_mb = cache_path.stat().st_size / (1024 * 1024)
            print(f"  + dataset/cache/rxnorm_cache.json ({size_mb:.2f} MB)")

    # Check size
    size_bytes = zip_path.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    print(f"\nZip created: {zip_path}")
    print(f"Size: {size_mb:.2f} MB")

    if size_mb > 49:
        raise RuntimeError(
            f"Lambda deployment package too large: {size_mb:.2f} MB > 49 MB limit"
        )

    print("Build successful!")
    return zip_path


if __name__ == "__main__":
    build_lambda_zip()
