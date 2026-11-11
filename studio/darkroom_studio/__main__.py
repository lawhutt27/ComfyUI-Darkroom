"""python -m darkroom_studio [--workdir DIR] [--port N] [--share]"""
import argparse

from .ui.app import launch

ap = argparse.ArgumentParser(description="Darkroom Studio")
ap.add_argument("--workdir", default=None)
ap.add_argument("--port", type=int, default=None)
ap.add_argument("--share", action="store_true")
a = ap.parse_args()
launch(workdir=a.workdir, share=a.share or None, server_port=a.port)
