import sys
import os
import glob
import pathlib
import ctypes

dir_path = os.path.dirname(os.path.realpath(__file__))
target_bin = os.path.abspath(os.path.join(dir_path, "..", "deps", "bin"))

if not os.path.exists(target_bin):
    runtimes_dir = os.path.abspath(os.path.join(dir_path, "..", "runtimes"))
    possible_native_dirs = glob.glob(os.path.join(runtimes_dir, "*", "native"))
    target_bin = possible_native_dirs[0] if possible_native_dirs else None

from maa.library import Library
from maa.agent.agent_server import AgentServer

import maa as _maa_pkg
if target_bin and os.path.exists(target_bin):
    dll_dir = pathlib.Path(target_bin)
else:
    dll_dir = pathlib.Path(_maa_pkg.__file__).parent / "bin"

if dll_dir.exists():
    os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(dll_dir))

print(f"[DLLInit] dll_dir = {dll_dir}")

# opencv_world4_maa.dll has a DllMain null-pointer crash under certain ASLR
# base addresses in Python processes. Pre-mapping it with DONT_RESOLVE_DLL_REFERENCES
# (0x1) forces Windows to reuse the same mapped image on the subsequent normal load,
# bypassing the crash. The handle is kept alive in _premapped for the process lifetime.
_k32 = ctypes.WinDLL("kernel32")
_k32.LoadLibraryExW.restype = ctypes.c_uint64
_k32.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]
_DONT_RESOLVE = 0x00000001
_premapped = []
for _premap_name in ("opencv_world4_maa.dll",):
    _premap_path = dll_dir / _premap_name
    if _premap_path.exists():
        _h = _k32.LoadLibraryExW(str(_premap_path), None, _DONT_RESOLVE)
        if _h:
            _premapped.append(_h)
            print(f"[DLLInit] PRE-MAPPED: {_premap_name}")

_DLL_LOAD_ORDER = [
    "MaaUtils.dll",
    "onnxruntime_maa.dll",
    "opencv_world4_maa.dll",
    "fastdeploy_ppocr_maa.dll",
    "DirectML.dll",
    "ViGEmClient.dll",
    "MaaFramework.dll",
    "MaaToolkit.dll",
    "MaaAgentServer.dll",
]
_preloaded = []
for _dll_name in _DLL_LOAD_ORDER:
    _dll_path = dll_dir / _dll_name
    if not _dll_path.exists():
        print(f"[DLLInit] SKIP: {_dll_name}")
        continue
    try:
        _preloaded.append(ctypes.WinDLL(str(_dll_path)))
        print(f"[DLLInit] OK: {_dll_name}")
    except OSError as _e:
        print(f"[DLLInit] FAIL: {_dll_name} → {_e}")

Library.open(dll_dir, agent_server=True)

import my_action
import my_reco
import smart_click


def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <socket_id>")
        sys.exit(1)
    socket_id = sys.argv[-1]
    AgentServer.start_up(socket_id)
    AgentServer.join()
    AgentServer.shut_down()


if __name__ == "__main__":
    main()
