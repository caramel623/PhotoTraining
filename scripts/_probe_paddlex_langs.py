import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import re
t = open(".venv-ocr/Lib/site-packages/paddlex/inference/utils/official_models.py", encoding="utf-8").read()
langs = sorted(set(re.findall(r'"([a-z]{2}(?:-[A-Z]{2})?)"', t)))
print("langs:", langs)
recs = sorted(set(re.findall(r'"([A-Za-z0-9_\-]*(?:rec|svt)[A-Za-z0-9_\-]*)"', t)))
print("rec/svt model names:", recs)