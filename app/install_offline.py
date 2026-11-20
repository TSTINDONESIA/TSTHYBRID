"""Dijalankan tiap start: memasang paket terjemahan Argos dari folder models\argos (sekali saja per laptop)."""
import glob, os
here = os.path.dirname(os.path.abspath(__file__))
src = os.path.join(here, "..", "models", "argos")
try:
    import argostranslate.package as p
    have = {(x.from_code, x.to_code) for x in p.get_installed_packages()}
    for f in sorted(glob.glob(os.path.join(src, "*.argosmodel"))):
        a, b = os.path.basename(f)[:-len(".argosmodel")].split("_")
        if (a, b) not in have:
            print("Memasang model terjemahan %s -> %s ..." % (a, b))
            p.install_from_path(f)
except Exception as e:
    print("Peringatan install_offline:", e)
