"""One-time, online: install Argos translation packs for the subtitle feature."""
import argostranslate.package as package

WANT = ["sv", "es", "fr", "de"]

package.update_package_index()
available = package.get_available_packages()

for code in WANT:
    pkg = next((p for p in available
                if p.from_code == "en" and p.to_code == code), None)
    if pkg is None:
        print(f"no en->{code} package offered")
        continue
    print(f"downloading en->{code} ...")
    package.install_from_path(pkg.download())
    print(f"installed en->{code}")
