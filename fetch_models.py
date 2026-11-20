"""Dipakai saat BUILD (butuh internet): unduh model Whisper + Argos ke folder models."""
import os, shutil, sys
root, size = sys.argv[1], sys.argv[2]
extra = [x.strip() for x in (sys.argv[3] if len(sys.argv) > 3 else "").split(",") if x.strip()]
from faster_whisper.utils import download_model
w = os.path.join(root, "models", "whisper-" + size)
if not os.path.exists(os.path.join(w, "model.bin")):
    print("Mengunduh model Whisper '%s' ..." % size)
    download_model(size, output_dir=w)
import argostranslate.package as p
d = os.path.join(root, "models", "argos"); os.makedirs(d, exist_ok=True)
p.update_package_index()
pairs = [("id", "en"), ("en", "id")] + [pr for c in extra for pr in (("en", c), (c, "en"))]
for a, b in pairs:
    t = os.path.join(d, "%s_%s.argosmodel" % (a, b))
    if os.path.exists(t): continue
    pkg = next((x for x in p.get_available_packages() if x.from_code == a and x.to_code == b), None)
    if pkg is None:
        if (a, b) in (("id", "en"), ("en", "id")): raise SystemExit("Paket Argos %s -> %s tidak ditemukan" % (a, b))
        print("PERINGATAN: paket Argos %s -> %s tidak tersedia, dilewati (bahasa itu perlu mode Gemini)" % (a, b)); continue
    print("Mengunduh terjemahan %s -> %s ..." % (a, b))
    shutil.copy(str(pkg.download()), t)
print("Model siap.")
