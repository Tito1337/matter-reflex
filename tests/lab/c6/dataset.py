"""Generate a disposable active operational dataset for an isolated Thread mesh."""

import secrets


def new_dataset():
    records = (
        (14, bytes.fromhex("0000000000010000")),  # Active Timestamp
        (0, bytes.fromhex("000010")),  # 2.4 GHz channel 16
        (53, bytes.fromhex("0004001fffe0")),  # Supported channel mask
        (2, secrets.token_bytes(8)),  # Extended PAN ID
        (7, b"\xfd" + secrets.token_bytes(7)),  # Mesh-local /64
        (5, secrets.token_bytes(16)),  # Network key
        (3, b"ReflexLab"),
        (1, secrets.randbelow(65535).to_bytes(2, "big")),  # PAN ID
        (4, secrets.token_bytes(16)),  # PSKc
        (12, bytes.fromhex("02a0f7f8")),  # Security policy
    )
    return b"".join(bytes((tag, len(value))) + value for tag, value in records).hex()


if __name__ == "__main__":
    print(new_dataset())
