import yaml 

def load_targets(filename="config/targets.yaml"):
    with open(filename, "r") as f:
        return yaml.safe_load(f)
