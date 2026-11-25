"""Unit tests package for repo_pipeline."""

import os
import sys

# Ensure src is in sys.path for test discovery
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
