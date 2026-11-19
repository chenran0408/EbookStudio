"""Run the optional PaddleOCR-VL pipeline in its own Python environment."""

import json
import os
from pathlib import Path
import sys


def main(work: Path) -> None:
    pages = json.loads((work / "pages.json").read_text(encoding="utf-8"))
    cache = Path(os.environ["EBOOK_STUDIO_PADDLE_MODELS"])
    layout = cache / "PP-DocLayoutV3"
    recognition = cache / "PaddleOCR-VL-1.6"
    if not (layout / "inference.pdiparams").is_file() or not (recognition / "model.safetensors").is_file():
        raise FileNotFoundError(f"PaddleOCR-VL-1.6 模型尚未安装完整：{cache}")
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(cache)
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    import cv2
    import numpy as np
    from paddleocr import PaddleOCRVL

    pipeline = PaddleOCRVL(
        pipeline_version="v1.6",
        device="gpu:0",
        layout_detection_model_dir=str(layout),
        vl_rec_model_dir=str(recognition),
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_layout_detection=True,
        use_chart_recognition=False,
        markdown_ignore_labels=["header", "footer", "header_image", "footer_image", "number"],
    )
    for offset, page in enumerate(pages, 1):
        image = cv2.imdecode(np.fromfile(work / f"scan-{page}.png", dtype=np.uint8), cv2.IMREAD_COLOR)
        result = pipeline.predict(image)[0].markdown
        images = {}
        for index, (source, crop) in enumerate(result["markdown_images"].items(), 1):
            name = f"crop-{page}-{index}.png"
            crop.save(work / name, format="PNG")
            images[source] = name
        data = {"markdown": result["markdown_texts"], "images": images}
        (work / f"result-{page}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(f"EBOOK_STUDIO_PAGE {offset} {page}", flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
