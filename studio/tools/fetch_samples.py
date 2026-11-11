"""Download the real-camera test files (public domain, CC0, from raw.pixls.us).

    python tools/fetch_samples.py [target_dir]

Files are verified by SHA-256.  Nikon D5300 14-bit and 12-bit NEF (lens: AF-S DX
18-140mm), Canon EOS 1000D / Rebel XS CR2 (lens: EF-S 18-55mm IS).
"""

import hashlib
import os
import sys
import urllib.request

BASE = "https://raw.pixls.us/data/"
FILES = {
    "D5300_14bit.NEF": ("NIKON%20CORPORATION/NIKON%20D5300/DSC_0089-14bit-uncompressed.NEF",
                        "8751a0a3bc491bb5c79ed6b32f83b3923b119ae9d950b851ad26db2a0fbb3b91"),
    "D5300_12bit.NEF": ("NIKON%20CORPORATION/NIKON%20D5300/DSC_0090-12bit-uncompressed.NEF",
                        "0ae88fc81f082929d76dd1b932aacf7815cd6bc670b96ef55a81928e11084a6c"),
    "EOS1000D.CR2": ("Canon/EOS%201000D/IMG_9406.CR2",
                     "08b0843a7968315b68bd3c71a96c952ebf275d5b5eba4b6094ae8b103522584c"),
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main(target):
    os.makedirs(target, exist_ok=True)
    for name, (url, digest) in FILES.items():
        dst = os.path.join(target, name)
        if os.path.exists(dst) and sha256(dst) == digest:
            print(f"ok      {name}")
            continue
        print(f"fetch   {name} ...", flush=True)
        req = urllib.request.Request(BASE + url, headers={"User-Agent": "darkroom-studio-tests"})
        with urllib.request.urlopen(req, timeout=300) as r, open(dst + ".part", "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
        os.replace(dst + ".part", dst)
        got = sha256(dst)
        if got != digest:
            os.remove(dst)
            raise SystemExit(f"checksum mismatch for {name}: {got}")
        print(f"ok      {name}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "samples"))
