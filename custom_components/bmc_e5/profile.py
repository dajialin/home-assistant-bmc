"""Load locally provisioned protocol material without putting it in HA entries."""
import json
from pathlib import Path


def load_profile(path: str) -> tuple[int, int]:
    data = json.loads(Path(path).read_text(encoding="utf8"))
    if data.get("version") != 1:
        raise ValueError("Unsupported protocol profile")
    modulus = int(data["modulus"], 16)
    exponent = int(data["private_exponent"], 16)
    if modulus.bit_length() != 1024 or not 1 < exponent < modulus:
        raise ValueError("Invalid protocol profile")
    if pow(pow(42, exponent, modulus), 65537, modulus) != 42:
        raise ValueError("Protocol key pair does not match")
    return modulus, exponent
