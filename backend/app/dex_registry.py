from __future__ import annotations

from dataclasses import dataclass


DEX_UNISWAP_V2 = "uniswap_v2"
DEX_UNISWAP_V3 = "uniswap_v3"
DEX_PANCAKESWAP_V2 = "pancakeswap_v2"


@dataclass(frozen=True, slots=True)
class DexRegistryEntry:
    dex: str
    strategy: str


# Registry of known pool/pair contracts for high-confidence decoding.
# Address keys are lowercased.
_REGISTRY: dict[tuple[str, str], DexRegistryEntry] = {
    # Ethereum
    ("ethereum", "0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc"): DexRegistryEntry(
        dex=DEX_UNISWAP_V2,
        strategy="v2_pair",
    ),
    ("ethereum", "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"): DexRegistryEntry(
        dex=DEX_UNISWAP_V3,
        strategy="v3_pool",
    ),
    # BSC
    ("bsc", "0x16b9a828a7d7c2f6ec0f3b7e6754a672032b337d"): DexRegistryEntry(
        dex=DEX_PANCAKESWAP_V2,
        strategy="v2_pair",
    ),
}


def lookup_dex(chain: str, address: str | None) -> DexRegistryEntry | None:
    if not address:
        return None
    return _REGISTRY.get((chain.lower(), address.lower()))
