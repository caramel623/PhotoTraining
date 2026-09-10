import io, sys, os, glob, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

os.environ.setdefault("HOME", os.path.join(os.getcwd(), "runs", "home"))
os.environ.setdefault("USERPROFILE", os.environ["HOME"])
import warnings
warnings.filterwarnings("ignore")

from paddleocr import PaddleOCR


def main():
    imgs = sorted(glob.glob(r"runs\2025\crops\vehicle\*.jpg"))[:12]
    print("images:", len(imgs))
    eng = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        ocr_version="PP-OCRv5",
        enable_mkldnn=False,
    )
    ok = 0
    for p in imgs:
        res = list(eng.predict(p))
        r = res[0]
        texts = list(r.get("rec_texts", []))
        scores = [round(float(s), 3) for s in r.get("rec_scores", [])]
        polys = r.get("rec_polys", [])
        print(os.path.basename(p), "|", list(zip(texts, scores)))
        if texts:
            ok += 1
    print("detected:", ok, "/", len(imgs))
    if ok:
        r = res[0]
        print("rec_polys sample:", str(polys)[:300])


if __name__ == "__main__":
    main()