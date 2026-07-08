# TARGET configuration details based on dictionary/inventory
import yaml 

def load_targets(filename="config/targets.yaml"):
    try:
        with open(filename, "r") as f:
            return yaml.safe_load(f)

    except FileNotFoundError:
        raise RuntimeError(
            f"Targets file not found: {filename}"
        )

    except yaml.YAMLError as e:
        raise RuntimeError(
            f"Invalid YAML file {filename}: {e}"
        )