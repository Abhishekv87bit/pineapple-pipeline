import yaml
from pathlib import Path
from pydantic import ValidationError

from backend.kfs_manifest_types import KFSManifest, GeometrySpec, MotionSpec, SimulationSpec, PluginSpec

class KFSManifestParserError(Exception):
    """Custom exception for KFS manifest parsing errors."""
    pass

def parse_kfs_manifest(file_path: Path) -> KFSManifest:
    """
    Parses a .kfs.yaml file and converts its content into a structured KFSManifest object.

    Args:
        file_path: The path to the .kfs.yaml file.

    Returns:
        A KFSManifest object representing the parsed content.

    Raises:
        KFSManifestParserError: If the file cannot be read, contains invalid YAML,
                                or does not conform to the KFSManifest schema.
    """
    if not file_path.is_file():
        raise KFSManifestParserError(f"Manifest file not found: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            yaml_content = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise KFSManifestParserError(f"Invalid YAML in {file_path}: {e}") from e
    except Exception as e:
        raise KFSManifestParserError(f"Could not read manifest file {file_path}: {e}") from e

    if not isinstance(yaml_content, dict):
        raise KFSManifestParserError(f"Manifest content must be a dictionary in {file_path}")

    try:
        # Pydantic will validate the dictionary against the KFSManifest schema
        manifest = KFSManifest(**yaml_content)
        return manifest
    except ValidationError as e:
        raise KFSManifestParserError(f"Manifest schema validation failed for {file_path}: {e}") from e
    except Exception as e:
        raise KFSManifestParserError(f"Unexpected error during manifest parsing for {file_path}: {e}") from e


# Example usage (for testing purposes, can be removed in production if not needed)
if __name__ == "__main__":
    # Create a dummy .kfs.yaml file for testing
    dummy_yaml_content = """
    apiVersion: kineticforge.studio/v1alpha1
    kind: KFSManifest
    metadata:
      name: ExampleProject
      version: 1.0.0
    geometry:
      type: CAD
      parameters:
        filepath: models/example.step
        format: STEP
    motion:
      type: KinematicSimulation
      parameters:
        duration: 10.0
        steps: 100
    simulation:
      solver: AnsysFluent
      configuration:
        meshQuality: high
        timesteps: 500
    plugins:
      - name: custom-post-processor
        path: ./plugins/post_processor.py
        config:
          logLevel: INFO
          outputDir: ./results
    """
    test_file_path = Path("test_manifest.kfs.yaml")
    test_file_path.write_text(dummy_yaml_content)

    try:
        print(f"Parsing manifest from {test_file_path}...")
        parsed_manifest = parse_kfs_manifest(test_file_path)
        print("Manifest parsed successfully!")
        print(f"API Version: {parsed_manifest.apiVersion}")
        print(f"Kind: {parsed_manifest.kind}")
        print(f"Project Name: {parsed_manifest.metadata.name}")
        print(f"Geometry Type: {parsed_manifest.geometry.type}")
        print(f"Motion Type: {parsed_manifest.motion.type}")
        print(f"Simulation Solver: {parsed_manifest.simulation.solver}")
        if parsed_manifest.plugins:
            print(f"Plugin 1 Name: {parsed_manifest.plugins[0].name}")

        # Test with an invalid manifest (missing required fields)
        invalid_yaml_content = """
        apiVersion: kineticforge.studio/v1alpha1
        kind: KFSManifest
        metadata:
          name: InvalidProject
        # Missing geometry, motion, simulation
        """
        invalid_test_file_path = Path("invalid_manifest.kfs.yaml")
        invalid_test_file_path.write_text(invalid_yaml_content)

        print(f"\nAttempting to parse invalid manifest from {invalid_test_file_path}...")
        try:
            parse_kfs_manifest(invalid_test_file_path)
        except KFSManifestParserError as e:
            print(f"Caught expected error for invalid manifest: {e}")

    except KFSManifestParserError as e:
        print(f"An error occurred during parsing: {e}")
    finally:
        # Clean up dummy files
        if test_file_path.exists():
            test_file_path.unlink()
        if invalid_test_file_path.exists():
            invalid_test_file_path.unlink()
