"""Exact FEBio 4.12 runtime qualification and dependency pinning."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from febio_cae.domain import ExecutionBundle, ToolIdentity
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.storage._ownership import pinned_read

_DESCRIPTOR_KIND: Final = "febio-runtime-qualification"
_DESCRIPTOR_PATH: Final = "input/native-runtime.json"
_QUALIFIED_TOOL: ToolIdentity = ToolIdentity(
    "febio",
    "4.12.0",
    "03b9db12c4b3e2ed0cf027be6b8b5d0cef2d4edc9eab26f5a2bd8193efb770c9",
)


@dataclass(frozen=True, slots=True)
class _RuntimeFile:
    name: str
    digest: str
    size_bytes: int


_QUALIFIED_EXECUTABLE = _RuntimeFile(
    "febio4.exe",
    "03b9db12c4b3e2ed0cf027be6b8b5d0cef2d4edc9eab26f5a2bd8193efb770c9",
    74752,
)
_QUALIFIED_DLLS: tuple[tuple[str, str, int], ...] = (
    ("febiolib.dll", "e178d4d7b6eee15c076c57af2226994027ddc2c312c02c40e1f4760f21dbaa63", 382976),
    ("febiofluid.dll", "478bf915e65ea848a600345302d67c070a39b20d5757e18120b4b9d2d4b91883", 1637888),
    ("febiomix.dll", "d0655a1c773aebd5c1e72def0633549bea7924738215fa1573c7e2ead2b4a634", 2566656),
    ("numcore.dll", "ced8f7cf120f00466753af64a74924f00c8fcf2b4dd2a224c930f2d2d5fcea29", 122859520),
    ("febiorve.dll", "7ce14d97db3d1bd24cbde8fc5dc1abd049c472673e650dbf3c3c22ae512f61c0", 243200),
    ("febiomech.dll", "ff85625bf44e3a4bbc4ab449e40c6cfe11ed1d3366cc0197aa83599a4add8181", 5029376),
    ("febioopt.dll", "e85f4b7e6a9091e679b4034618f2a04aaf53be1dcb76db99d1a90a707b51185b", 12724736),
    ("febioxml.dll", "b3478e3ae3acdc9fde375fe0846f436d2a16db2158b7c64b89aa2362369be219", 592896),
    ("febioplot.dll", "dbbf96bccf6c537d0be0e375a1ec37f7c04c49acd910e68453773fddfd8584c8", 141824),
    ("feamr.dll", "c900c29616472d396ba6c40a9f136a71e7cfb474ee80732b3617ed496d3d70f9", 936960),
    ("feimglib.dll", "aff5a19c00d63c2ee1af51708bc55afb8faa9faf9c8b04a097dfdd94d9d3df1b", 78848),
    ("fecore.dll", "a54a85edc10e1b427133064c82d43f51d44d45bfba8ccc52cdd363ea377008b1", 2116608),
    ("zip.dll", "d5cb8eb0d410016b01fc69d2020b7725271591096d03405595f52e52addaa156", 102400),
    ("nglib.dll", "435a6a8b44831c78a0ea4e788e3678c4cad038b7e5d386443acabc6655cb733d", 4194816),
    ("ngcore.dll", "d920f764a7df432383601cc7cfa4193d99079bd3ef018b898f67ffb739bd62f2", 384000),
    ("TKXDESTEP.dll", "730e0ea1f24a8865914f801de4b64c41b97fd0c30339c05fcbcb39e65a348a9d", 736768),
    ("TKSTEP.dll", "5549c15cd0eb131cd7f3501014234346a66f6c1b1a16f0a0477fcb4252b971cd", 2735104),
    ("TKSTEPAttr.dll", "980d2cf1659d8b6b4d2137e13a982c1236fad6217f58b70a35bd9c975685b195", 1222656),
    ("TKXDEIGES.dll", "24e9aad50d1540f0a46bd37b3c3951dd9b4492c6572d13437cf65c3fc3a591e4", 164352),
    ("TKSTEP209.dll", "917ae403c341a40e42dda2ad1cc5497287445a19c782edadfc45730852e96691", 429056),
    ("TKIGES.dll", "427acf7684063d09d351186aa5a524761d495799d3d6d827128707d32ba4f194", 2769920),
    ("TKSTEPBase.dll", "aca1ed074dbc1944f4cced991ea5e26042821819449a4a1be9ed4c1b790f193e", 1577472),
    ("TKFillet.dll", "ed59fac3d77d314ab2169f9527fda3059c22b9171f469689d36bb0b44fafb0fb", 2362880),
    ("TKSTL.dll", "4ac1e17e2f8f2bda7d394f4f903a126e3e3c5ab399c7ba0a43afb67240c1777a", 116736),
    ("TKXCAF.dll", "b660e8e740996b9b06638160184a83745263c46cb9b098ed43161b60a01c3e99", 817664),
    ("TKBool.dll", "f4d9b8fcf8e091b44d2e6ee2fccf062a3728f13f158c64f0788bd12d00a634a7", 3604480),
    ("TKVCAF.dll", "cb9e6d8257f5eb6b3f5f1c72b93c7003484ffda762578c5dfb8302473c1da3e4", 193536),
    ("TKV3d.dll", "98c65c810b5e1b105d0339402639c360f186c044fd0fd1e5ec36a977be7ba7d9", 2466304),
    ("TKCAF.dll", "4875f90e61fc25ee72876f277a62eb7b0be93c51bcc5c907ed1b7b23f7fd5e2c", 512000),
    ("TKXSBase.dll", "b76e790d37d46105abb425023448d1e6d947dc39f28a5e19d4f221acd2966579", 1903104),
    ("TKBO.dll", "2599b8876463c1926ca0515905c5cfc83f8d516e6bfe7ccd3eb7c068238ff60f", 2025984),
    ("TKMesh.dll", "f2e70016007fe72d69ff0cc8f351b322e7980b9a42a8711df700150e44631662", 635392),
    (
        "TKShHealing.dll",
        "9cea40972f0f3e2c12d2532a5a587f2cbe2aa48c3f3e29a1d163de873edbdb08",
        2583040,
    ),
    ("TKHLR.dll", "7d250c5a9cfdac9f0b5fcf4407251a446508928aaf5213b56b1fdb2d49f99412", 966144),
    ("TKPrim.dll", "ccbfbfe1507d72be05eba72a736b33d90f558d0d1ae68657775e9158ff6187f9", 312320),
    ("TKTopAlgo.dll", "cd851336ca1a86abbc95aeeaba9b35e7dff135a78d565c3e1784fb620e19e18e", 2388992),
    ("TKGeomAlgo.dll", "268e654344fd10a005b93864642ea537f8c1317755d9c9807c195d74e175d8cc", 4187136),
    ("TKBRep.dll", "928a3c8188f0a4dc67dc0cbed88ff582f59e5ae867ff752e51506ef86f05d87b", 892416),
    ("TKGeomBase.dll", "121f4c2d93f02cdaae0cc3afba987c568f1006578193433d6abb86e1178ba5a7", 3984384),
    ("TKG3d.dll", "6c0b72ea2a2ffc1a1939a28842c31ae34dc735e1a3836448b74d8ccc1c6c444e", 889856),
    ("TKService.dll", "45ee292266dca2178d434abade8a993b957b396a976d5c8af4f95c126af516ba", 1000960),
    ("TKG2d.dll", "f04e296d0dee12a5827cef63b16f76980a6b1e1521831bd491a5dfae03b86237", 285184),
    ("TKLCAF.dll", "0a4ba6cf2b97f7ca31f4d9b1be05811627772e606cc2604f1bb3b62d34801f6e", 656384),
    ("TKMath.dll", "5cc218b8bd06aff9f46b3689dfb56504f13e536fd6ea9a502864b3c918d8078d", 1702400),
    ("TKCDF.dll", "6c82aafee8369fe417973bb711ba93edb172a17d0fcb0407f4e40b0a2e845c9f", 266240),
    ("TKXDE.dll", "0717e6e73714587bae5e9b6381e89ef2e53c58bf24b88705edde057c561f6930", 85504),
    ("TKernel.dll", "fbb9d163dab35bd7a0ce17b8766965767646ef0f9ab69b3e53757203c86437a5", 1612288),
    ("zstd.dll", "c0b43068f80f257a3a707688ff052f5abd652cbd2b9d25b256221bf2ce23bff0", 658944),
    ("zlib1.dll", "b22e4b08c7e7c6c46a70f74cb811da16a28af8185e9a4f928c84dbcced7a2ffe", 90112),
    ("sqlite3.dll", "067aad6cc16a353b0362694fa267e6eb947e0d703a288f73e6e0a59cb2032d8d", 1077248),
    ("ssh.dll", "d4c36d2cd3f7074b3c143381f5bd8e4350ad3c4e447c072147ce336f72d69356", 400896),
    (
        "libcrypto-3-x64.dll",
        "5df68f6bfcc0b8e3d3a7dfa21aa666723fa48ad7e4df7eaa08a5542293dd61bb",
        5266944,
    ),
    (
        "libssl-3-x64.dll",
        "10c181bd43711be28d53ad5c0b7e581d690cbe4fd86efacb3ef4cf01e0ca1672",
        867328,
    ),
    ("fftw3.dll", "69aef6413ab0de2086eb3635cce33aad5a1408f117c6fe33a2c00a1e84198821", 953856),
    ("Qt6Widgets.dll", "36cfd5321d60623047f51bc9ce34333e1d090aca7a23b45e6c8a79f92d6a1aa6", 6540512),
    (
        "Qt6OpenGLWidgets.dll",
        "3941a9753884ed0a035ed95a03a3b7f4fc12c6fe8f67fca3aa9b82a1aeaec632",
        64736,
    ),
    ("Qt6OpenGL.dll", "2dbcc6b28480c186fd0c9f47b1987e85da3487861e1e0e76326b2cb68e183e81", 1972960),
    ("Qt6Network.dll", "dcd4d0951de4f8a7d8609dd2f44cb32cc7fd507c7562b635346df2de6707374b", 1735392),
    ("Qt6Gui.dll", "569f50c8fde5e3841197e4f44bc4673b330e2f8744391f36f0dccc17fc858f03", 9497312),
    ("Qt6Core.dll", "b6c6bedca4e798f9a6cfae20946688ab0a0c3dbd7de605951b0ffb7b679e4f53", 10023136),
    ("python3.dll", "c1de4bfe57dc4a5be8c72c865d617dc39dfd8162fcd2ce1fac9f401cf9efb504", 72560),
    ("python313.dll", "78b1dd211c0e66a0603df48da2c9b67a915ab3258701b9285d3faa255ed8dc25", 6093816),
    (
        "avcodec-60.dll",
        "b8ba83ff43cf8596586cbb015497d3cbe054d989a812b26b4b89cb9cd64a7a6b",
        79826944,
    ),
    (
        "avdevice-60.dll",
        "15a54a835d207cb92a2562804ac7d6b678d6ab62fe4cc4fe596bcadbaf67dcff",
        4417024,
    ),
    (
        "avfilter-9.dll",
        "5640ad1e25727b3bf092c0f2f3cd1bdc65d7854958f824fadb2959c252eb7fbe",
        39759872,
    ),
    (
        "avformat-60.dll",
        "01c35f9e157bf16b9b9ea416c0299ffc64a7626775857d7bc19ba1bc9b498208",
        17132544,
    ),
    ("avutil-58.dll", "c086635f541df5bfd9982b94b80197a211c3a82bfec1e9a34ecafac2c1e826a1", 2302976),
    ("postproc-57.dll", "2447e3a91171892143a168ffc30e7a3f8470c2acdb422e7cf0eebbde2f1655d5", 76288),
    (
        "swresample-4.dll",
        "9c14cda52325bca290815a21fd4f8d10e9547d41dd9c1edc12c40fbc61b02ac1",
        436224,
    ),
    ("swscale-7.dll", "8a2ff15b48672535ee70667148a902dba9cc9bb580d93a98dc83a3667dde7a56", 643072),
    ("libiomp5md.dll", "51043532cbb152b15ab3d4b20b85aaa28e18ebfe2b2565ff91950a1b622163e5", 1975912),
)
_QUALIFIED_FILES = (
    _QUALIFIED_EXECUTABLE,
    *(_RuntimeFile(*item) for item in _QUALIFIED_DLLS),
)

_QUALIFIED_DESCRIPTOR: bytes = canonical_bytes(
    {
        "schema_version": "1",
        "kind": _DESCRIPTOR_KIND,
        "tool": _QUALIFIED_TOOL.to_dict(),
        "executable": {
            "name": _QUALIFIED_EXECUTABLE.name,
            "digest": _QUALIFIED_EXECUTABLE.digest,
            "size_bytes": _QUALIFIED_EXECUTABLE.size_bytes,
        },
        "dlls": [
            {"name": item[0], "digest": item[1], "size_bytes": item[2]} for item in _QUALIFIED_DLLS
        ],
    }
)


def qualification_document(tool: ToolIdentity) -> bytes | None:
    """Return the frozen descriptor only for the exact qualified ToolIdentity."""
    if not isinstance(tool, ToolIdentity) or tool != _QUALIFIED_TOOL:
        return None
    return _QUALIFIED_DESCRIPTOR


def has_qualified_runtime(bundle: ExecutionBundle) -> bool:
    """Whether a bundle contains the exact descriptor binding for its exact tool."""
    if not isinstance(bundle, ExecutionBundle):
        return False
    document = qualification_document(bundle.tool)
    if document is None:
        return False
    descriptor_entry = next(
        (entry for entry in bundle.files if entry.logical_path == _DESCRIPTOR_PATH), None
    )
    if descriptor_entry is None or descriptor_entry.role != "native-runtime":
        return False
    return (
        descriptor_entry.size_bytes == len(document)
        and descriptor_entry.digest == hashlib.sha256(document).hexdigest()
    )


def _hash_stream(stream: Any) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def pin_qualified_runtime(bundle: ExecutionBundle, executable: Path) -> Iterator[None]:
    """Pin and verify the executable and every listed same-directory DLL."""
    if not isinstance(bundle, ExecutionBundle):
        raise TypeError("bundle must be an ExecutionBundle")
    document = qualification_document(bundle.tool)
    if document is None:
        raise ValueError("bundle tool is not a qualified FEBio runtime")
    if not has_qualified_runtime(bundle):
        raise ValueError("bundle does not contain its qualified runtime descriptor")
    expected = _QUALIFIED_FILES
    candidate = Path(executable)
    if candidate.is_symlink():
        raise OSError("qualified runtime executable is a link")
    candidate = candidate.absolute()
    if candidate.name != expected[0].name:
        raise OSError("qualified runtime executable name does not match its descriptor")
    with ExitStack() as stack:
        for item in expected:
            path = candidate.parent / item.name
            if path.is_symlink():
                raise OSError(f"qualified runtime dependency is a link: {item.name}")
            stream = stack.enter_context(pinned_read(path))
            info = os.fstat(stream.fileno())
            if info.st_nlink != 1:
                raise OSError(f"qualified runtime dependency has multiple links: {item.name}")
            if not stat.S_ISREG(info.st_mode):
                raise OSError(f"qualified runtime dependency is not a regular file: {item.name}")
            if info.st_size != item.size_bytes:
                raise OSError(f"qualified runtime dependency size mismatch: {item.name}")
            stream.seek(0)
            if _hash_stream(stream) != item.digest:
                raise OSError(f"qualified runtime dependency digest mismatch: {item.name}")
        yield None


__all__ = ["has_qualified_runtime", "pin_qualified_runtime", "qualification_document"]
