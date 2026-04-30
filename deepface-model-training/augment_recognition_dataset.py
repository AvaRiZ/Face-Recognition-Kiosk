from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
DEFAULT_VARIANTS = 8


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description=(
            "Create light identity-preserving augmentations for DeepFace recognition "
            "datasets used by ArcFace and Facenet."
        )
    )
    parser.add_argument("--images-dir", type=Path, default=root / "df_images", help="Folder with one subfolder per identity.")
    parser.add_argument("--variants", type=int, default=DEFAULT_VARIANTS, help="Augmented copies created per source image.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible augmentations.")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing aug_*.jpg files.")
    return parser.parse_args()


def identity_dirs(images_dir: Path) -> list[Path]:
    identities = sorted(path for path in images_dir.iterdir() if path.is_dir())
    if not identities:
        raise FileNotFoundError(f"No identity folders found in: {images_dir}")
    return identities


def source_images(identity_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in identity_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in IMAGE_SUFFIXES
        and not path.stem.startswith("aug_")
    )


def crop_zoom(image: Image.Image, rng: random.Random) -> Image.Image:
    width, height = image.size
    zoom = rng.uniform(0.92, 0.99)
    crop_width = max(1, int(width * zoom))
    crop_height = max(1, int(height * zoom))
    left = rng.randint(0, width - crop_width)
    top = rng.randint(0, height - crop_height)
    return image.crop((left, top, left + crop_width, top + crop_height)).resize((width, height), Image.Resampling.LANCZOS)


def augment_image(image: Image.Image, variant_index: int, rng: random.Random) -> Image.Image:
    augmented = ImageOps.exif_transpose(image).convert("RGB")

    if variant_index % 4 == 0:
        augmented = ImageOps.mirror(augmented)

    augmented = crop_zoom(augmented, rng)
    augmented = augmented.rotate(
        rng.uniform(-7.0, 7.0),
        resample=Image.Resampling.BICUBIC,
        expand=False,
        fillcolor=(255, 255, 255),
    )
    augmented = ImageEnhance.Brightness(augmented).enhance(rng.uniform(0.88, 1.12))
    augmented = ImageEnhance.Contrast(augmented).enhance(rng.uniform(0.90, 1.12))
    augmented = ImageEnhance.Color(augmented).enhance(rng.uniform(0.92, 1.08))

    if variant_index % 5 == 0:
        augmented = augmented.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.15, 0.45)))

    return augmented


def remove_existing_augments(identity_dir: Path) -> None:
    for path in identity_dir.glob("aug_*.jpg"):
        path.unlink()


def write_manifest(images_dir: Path, rows: list[dict[str, str]]) -> Path:
    manifest_path = images_dir / "recognition_labels.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as manifest_file:
        writer = csv.DictWriter(manifest_file, fieldnames=["identity", "image_path", "is_augmented"])
        writer.writeheader()
        writer.writerows(rows)
    return manifest_path


def main() -> None:
    args = parse_args()
    images_dir = args.images_dir.resolve()
    rng = random.Random(args.seed)
    rows: list[dict[str, str]] = []
    augmented_count = 0

    for identity_dir in identity_dirs(images_dir):
        if args.overwrite:
            remove_existing_augments(identity_dir)

        originals = source_images(identity_dir)
        if not originals:
            continue

        for source_path in originals:
            rows.append(
                {
                    "identity": identity_dir.name,
                    "image_path": source_path.relative_to(images_dir).as_posix(),
                    "is_augmented": "false",
                }
            )

            image = Image.open(source_path)
            for variant_index in range(1, args.variants + 1):
                output_path = identity_dir / f"aug_{source_path.stem}_{variant_index:02d}.jpg"
                if output_path.exists() and not args.overwrite:
                    rows.append(
                        {
                            "identity": identity_dir.name,
                            "image_path": output_path.relative_to(images_dir).as_posix(),
                            "is_augmented": "true",
                        }
                    )
                    continue

                augmented = augment_image(image, variant_index, rng)
                augmented.save(output_path, format="JPEG", quality=rng.randint(86, 94), optimize=True)
                augmented_count += 1
                rows.append(
                    {
                        "identity": identity_dir.name,
                        "image_path": output_path.relative_to(images_dir).as_posix(),
                        "is_augmented": "true",
                    }
                )

    manifest_path = write_manifest(images_dir, rows)
    print(f"Identities: {len(identity_dirs(images_dir))}")
    print(f"Manifest rows: {len(rows)}")
    print(f"New augmentations: {augmented_count}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
