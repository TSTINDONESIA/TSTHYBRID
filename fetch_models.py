"""Dipakai saat BUILD (butuh internet): unduh model Whisper + Argos ke folder models."""
import os, shutil, sys
root, size = sys.argv[1], sys.argv[2]
from faster_whisper.utils import download_model
w = os.path.join(root, "models", "whisper-" + size)
if not os.path.exists(os.path.join(w, "model.bin")):
    print("Mengunduh model Whisper '%s' ..." % size)
    download_model(size, output_dir=w)
import argostranslate.package as p
d = os.path.join(root, "models", "argos"); os.makedirs(d, exist_ok=True)
p.update_package_index()
for a, b in (("id", "en"), ("en", "id")):
    t = os.path.join(d, "%s_%s.argosmodel" % (a, b))
    if os.path.exists(t): continue
    pkg = next(x for x in p.get_available_packages() if x.from_code == a and x.to_code == b)
    print("Mengunduh terjemahan %s -> %s ..." % (a, b))
    shutil.copy(str(pkg.download()), t)
print("Model siap.")
