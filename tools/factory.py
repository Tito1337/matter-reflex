"""Create private onboarding data for this board using CHIP's factory generator."""

import argparse
import contextlib
import fcntl
import logging
import os
from pathlib import Path
import runpy
import secrets
import sys
import tempfile


def generate(parent: Path) -> None:
    generator = (Path(os.environ["ESP_MATTER_PATH"]) / "connectedhomeip/connectedhomeip"
                 / "scripts/tools/generate_esp32_chip_factory_bin.py")
    target = parent / "factory"
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Lock the directory so simultaneous invocations keep the same onboarding code.
    directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(directory, fcntl.LOCK_EX)
        if target.exists():
            if not all((target / name).is_file()
                       for name in ("factory_partition.bin", "onboarding_codes.csv")):
                raise RuntimeError("Dossier de provisionnement incomplet.")
            print("Les données d’usine existantes sont disponibles.")
            return

        forbidden = {11_111_111 * n for n in range(10)} | {12_345_678, 87_654_321}
        pin = secrets.randbelow(99_999_998) + 1
        while pin in forbidden:
            pin = secrets.randbelow(99_999_998) + 1
        # CHIP's tool generates the onboarding code only for nonzero discriminators.
        discriminator = secrets.randbelow(4095) + 1
        with tempfile.TemporaryDirectory(dir=parent) as staging:
            output = Path(staging) / "factory"
            sys.argv = [
                str(generator), "--target", "esp32c6", "--size", "0x6000",
                "--output_dir", str(output), "--passcode", str(pin),
                "--discriminator", str(discriminator), "--discovery-mode", "4",
                "--vendor-id", "0xFFF1", "--product-id", "0x8000",
                "--vendor-name", "Matter Reflex", "--product-name", "Matter Reflex",
                "--hw-ver", "1", "--hw-ver-str", "XIAO ESP32-C6",
                "--serial-num", secrets.token_hex(8), "--rd-id-uid", secrets.token_hex(16),
            ]
            # CHIP logs setup codes. Keep them exclusively in the onboarding file.
            logging.disable(logging.CRITICAL)
            with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                runpy.run_path(str(generator), run_name="__main__")
            if (output / "factory_partition.bin").stat().st_size != 0x6000:
                raise ValueError("Taille inattendue de la partition usine.")
            if not (output / "onboarding_codes.csv").is_file():
                raise ValueError("Le SDK n’a pas produit le code d’appairage.")
            (output / "nvs_partition.csv").unlink()
            output.rename(target)
        print("Partition usine et codes d’appairage générés.")
    finally:
        os.close(directory)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    generate(args.directory)
