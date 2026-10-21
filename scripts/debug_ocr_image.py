from pathlib import Path
import sys
import json

from PIL import Image, ImageDraw
import easyocr


def to_float(value) -> float:
    return float(value)


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python scripts/debug_ocr_image.py <imagen>")
        raise SystemExit(1)

    image_path = Path(sys.argv[1])
    output_dir = Path("data/samples/debug_ocr")
    output_dir.mkdir(parents=True, exist_ok=True)

    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)

    reader = easyocr.Reader(["es", "en"], gpu=False)

    results = reader.readtext(
        str(image_path),
        detail=1,
        paragraph=False,
    )

    tokens = []

    for index, (bbox, text, confidence) in enumerate(results):
        points = [
            [to_float(point[0]), to_float(point[1])]
            for point in bbox
        ]

        xs = [point[0] for point in points]
        ys = [point[1] for point in points]

        x0 = min(xs)
        y0 = min(ys)
        x1 = max(xs)
        y1 = max(ys)

        token = {
            "index": int(index),
            "text": str(text),
            "confidence": float(confidence),
            "bbox": [
                float(x0),
                float(y0),
                float(x1),
                float(y1),
            ],
            "points": points,
        }

        tokens.append(token)

        draw.rectangle(
            [
                int(x0),
                int(y0),
                int(x1),
                int(y1),
            ],
            outline="red",
            width=2,
        )

        draw.text(
            (
                int(x0),
                max(0, int(y0) - 14),
            ),
            str(index),
            fill="red",
        )

    json_path = output_dir / f"{image_path.stem}_tokens.json"
    image_debug_path = output_dir / f"{image_path.stem}_debug.png"

    json_path.write_text(
        json.dumps(tokens, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    image.save(image_debug_path)

    print(f"OCR tokens: {json_path}")
    print(f"Debug image: {image_debug_path}")


if __name__ == "__main__":
    main()