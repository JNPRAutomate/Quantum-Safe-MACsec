import yaml
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

def load_runtime_pki_profile():

    file = (
        BASE_DIR
        / "config"
        / "runtime"
        / "pki_profile.yaml"
    )

    with open(file) as f:
        return yaml.safe_load(f)