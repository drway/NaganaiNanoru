import sys
import os
import glob
import pathlib

# 动态寻找应用包的原生 DLL 目录（优先 deps/bin，其次 runtimes/*/native）
dir_path = os.path.dirname(os.path.realpath(__file__))
target_bin = os.path.abspath(os.path.join(dir_path, "..", "deps", "bin"))

if not os.path.exists(target_bin):
    runtimes_dir = os.path.abspath(os.path.join(dir_path, "..", "runtimes"))
    possible_native_dirs = glob.glob(os.path.join(runtimes_dir, "*", "native"))
    target_bin = possible_native_dirs[0] if possible_native_dirs else None

# 导入 maa（触发 maa/__init__.py → Library.open(pip_bin, agent_server=False)）
from maa.library import Library
from maa.agent.agent_server import AgentServer

# 确定实际使用的 DLL 目录：应用包 runtimes > pip 包 bin（兜底）
import maa as _maa_pkg
if target_bin and os.path.exists(target_bin):
    dll_dir = pathlib.Path(target_bin)
else:
    dll_dir = pathlib.Path(_maa_pkg.__file__).parent / "bin"

# 将 DLL 目录加入 Windows 搜索路径，确保 MaaAgentServer.dll 能找到同级依赖
if dll_dir.exists():
    os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(dll_dir))

# 以 AgentServer 模式重新配置 Library。
# Library._api_properties_initialized 此时仍为 False（装饰器尚未触发），
# 故此处的 Library.open() 可正常覆盖 _is_agent_server 及 agent_server_libpath。
Library.open(dll_dir, agent_server=True)

import my_action
import my_reco
import smart_click


def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <socket_id>")
        print("socket_id is provided by AgentIdentifier.")
        sys.exit(1)

    socket_id = sys.argv[-1]

    AgentServer.start_up(socket_id)
    AgentServer.join()
    AgentServer.shut_down()


if __name__ == "__main__":
    main()
