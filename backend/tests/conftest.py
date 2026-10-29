from pathlib import Path
import sys


BACKEND_PATH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_PATH))
