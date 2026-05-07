import sys
import os
import glob

# 动态寻找 DLL 路径：优先找 deps/bin (开发环境)，其次找 runtimes/*/native (发版环境)
dir_path = os.path.dirname(os.path.realpath(__file__))
target_bin = os.path.abspath(os.path.join(dir_path, "..", "deps", "bin"))

if not os.path.exists(target_bin):
    runtimes_dir = os.path.abspath(os.path.join(dir_path, "..", "runtimes"))
    possible_native_dirs = glob.glob(os.path.join(runtimes_dir, "*", "native"))
    if possible_native_dirs:
        target_bin = possible_native_dirs[0]
    else:
        target_bin = "./"

if target_bin != "./" and os.path.exists(target_bin):
    os.environ["PATH"] = target_bin + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(target_bin)

from maa.agent.agent_server import AgentServer
from maa.toolkit import Toolkit

# 初始化 Toolkit 指向正确的 DLL 目录
Toolkit.init_option(target_bin)

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
