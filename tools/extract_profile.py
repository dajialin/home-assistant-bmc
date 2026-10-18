"""Extract a local profile from the exact verified user-supplied APK."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

from custom_components.bmc_e5.profile import load_profile

VERIFIED_SHA256 = "c6519533fd27f382bc1c4196401dbcb94e92c48f4ef6cbe71d0de668e3b940e6"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with zipfile.ZipFile(args.apk) as archive:
        native = archive.read("lib/arm64-v8a/libnative-lib.so")
    if hashlib.sha256(native).hexdigest() != VERIFIED_SHA256:
        parser.error("Unsupported native library: only the verified version is accepted")
    moduli = re.findall(rb"N = ([A-F0-9]{256})", native)
    exponents = re.findall(rb"D = ([A-F0-9]{256})", native)
    if len(moduli) != 2 or len(exponents) != 1:
        parser.error("Unexpected protocol material layout")
    profile = {"version": 1, "modulus": moduli[1].decode(), "private_exponent": exponents[0].decode()}
    with args.output.open("x", encoding="utf8") as output:
        json.dump(profile, output, indent=2)
    load_profile(str(args.output))
    print("Local protocol profile created. Keep it private; copy it to HA /config.")


if __name__ == "__main__":
    main()
