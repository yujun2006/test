"""
Build script for packaging the appointment management app with PyInstaller.
Run on Windows: python build.py
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

APP_NAME = "安蒂克服务管理系统"
MAIN_SCRIPT = "app.py"

def find_package_path(package_name):
    """Find the installed package directory."""
    import importlib
    mod = importlib.import_module(package_name)
    return os.path.dirname(mod.__file__)

def main():
    build_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(build_dir)
    print(f"Build directory: {build_dir}")

    # Find flet_desktop package path (contains the Flutter client)
    flet_desktop_path = find_package_path("flet_desktop")
    flet_desktop_app = os.path.join(flet_desktop_path, "app")
    print(f"flet_desktop app path: {flet_desktop_app}")

    # Find flet package path
    flet_path = find_package_path("flet")
    print(f"flet path: {flet_path}")

    # Build the PyInstaller command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        f"--name={APP_NAME}",
        # Add the flet_desktop app directory (Flutter client binaries)
        f"--add-data={flet_desktop_app}{os.pathsep}flet_desktop/app",
        # Add our assets (fonts)
        f"--add-data=assets{os.pathsep}assets",
        # Hidden imports that PyInstaller may miss
        "--hidden-import=flet",
        "--hidden-import=flet_desktop",
        "--hidden-import=flet.auth",
        "--hidden-import=flet.canvas",
        "--hidden-import=flet.controls",
        "--hidden-import=flet.messaging",
        "--hidden-import=flet.pubsub",
        "--hidden-import=flet.security",
        "--hidden-import=flet.utils",
        "--hidden-import=flet.fastapi",
        "--hidden-import=flet.testing",
        "--hidden-import=msgpack",
        "--hidden-import=oauthlib",
        "--hidden-import=repath",
        "--hidden-import=httpx",
        "--hidden-import=httpcore",
        "--hidden-import=anyio",
        "--hidden-import=uvicorn",
        "--hidden-import=starlette",
        "--hidden-import=fastapi",
        "--hidden-import=websockets",
        "--collect-all=flet",
        "--collect-all=flet_desktop",
        MAIN_SCRIPT,
    ]

    print("\nRunning PyInstaller...")
    print(" ".join(cmd[:5]) + " ...")
    result = subprocess.run(cmd, capture_output=False)

    if result.returncode == 0:
        dist_dir = os.path.join(build_dir, "dist", APP_NAME)
        print(f"\nBuild successful! Output at: {dist_dir}")
        print(f"Run: {os.path.join(dist_dir, APP_NAME + '.exe')}")
    else:
        print(f"\nBuild failed with code {result.returncode}")
        sys.exit(1)

if __name__ == "__main__":
    main()
