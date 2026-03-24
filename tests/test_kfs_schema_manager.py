
import unittest
from unittest import mock
import os
import yaml
import pkg_resources
import logging

# Suppress logging during tests to avoid console clutter
logging.getLogger().setLevel(logging.CRITICAL)

# Assuming backend.kfs_schema_manager is importable in the test environment
from backend.kfs_schema_manager import KFSSchemaManager


class TestKFSSchemaManager(unittest.TestCase):

    def setUp(self):
        # Reset the singleton instance before each test to ensure isolation
        KFSSchemaManager._instance = None
        KFSSchemaManager._schemas = {}  # Clear loaded schemas

        self.mock_manifest_schema_content = """
        $schema: http://json-schema.org/draft-07/schema#
        title: KFS Manifest v1.0 Test
        description: A test schema for KFS Manifest files, version 1.0.
        type: object
        properties:
          version:
            type: string
            pattern: "^1\\.0$"
          metadata:
            type: object
            properties:
              name:
                type: string
            required: ["name"]
        required: ["version", "metadata"]
        """
        # Parse the content once to get the expected dictionary
        self.expected_manifest_schema = yaml.safe_load(self.mock_manifest_schema_content)

    @mock.patch("pkg_resources.resource_listdir")
    @mock.patch("pkg_resources.resource_filename")
    @mock.patch("builtins.open", new_callable=mock.mock_open)
    @mock.patch("yaml.safe_load")
    def test_load_and_get_schema(self, mock_yaml_safe_load, mock_open, mock_resource_filename, mock_resource_listdir):
        """
        Test that the schema manager correctly loads schemas from the package
        and can retrieve them by name and version.
        """
        # Configure mocks
        mock_resource_listdir.return_value = ["manifest_v1.0.yaml"]

        # Simulate pkg_resources.resource_filename returning a path
        mock_resource_filename.return_value = "/mock/path/to/manifest_v1.0.yaml"

        # Configure mock_open to return the dummy schema content
        mock_open.return_value.__enter__.return_value.read.return_value = self.mock_manifest_schema_content

        # Configure yaml.safe_load to return the parsed schema dictionary
        mock_yaml_safe_load.return_value = self.expected_manifest_schema

        # Initialize the schema manager (this will trigger _load_schemas)
        manager = KFSSchemaManager()

        # Assert that _load_schemas was called and populated _schemas
        self.assertIn("manifest", manager._schemas)
        self.assertIn("1.0", manager._schemas["manifest"])
        self.assertEqual(manager._schemas["manifest"]["1.0"], self.expected_manifest_schema)

        # Test get_schema with an existing schema
        retrieved_schema = manager.get_schema("manifest", "1.0")
        self.assertEqual(retrieved_schema, self.expected_manifest_schema)

        # Verify mocks were called
        mock_resource_listdir.assert_called_once_with("backend.kfs_schema_manager", "schemas")
        mock_resource_filename.assert_called_once_with("backend.kfs_schema_manager", os.path.join("schemas", "manifest_v1.0.yaml"))
        mock_open.assert_called_once_with("/mock/path/to/manifest_v1.0.yaml", "r")
        mock_yaml_safe_load.assert_called_once_with(self.mock_manifest_schema_content)

    @mock.patch("pkg_resources.resource_listdir", return_value=["manifest_v1.0.yaml"])
    @mock.patch("pkg_resources.resource_filename", return_value="/mock/path/to/manifest_v1.0.yaml")
    @mock.patch("builtins.open", new_callable=mock.mock_open)
    @mock.patch("yaml.safe_load")
    def test_get_schema_version_not_found_when_name_exists(self, mock_yaml_safe_load, mock_open, mock_resource_filename, mock_resource_listdir):
        """
        Test that get_schema raises a ValueError when the schema name exists but the version does not.
        """
        # Configure mocks just like in test_load_and_get_schema
        mock_open.return_value.__enter__.return_value.read.return_value = self.mock_manifest_schema_content
        mock_yaml_safe_load.return_value = self.expected_manifest_schema

        manager = KFSSchemaManager()  # This will load 'manifest_v1.0'

        with self.assertRaisesRegex(ValueError, "Schema 'manifest' with version '9.9' not found."):
            manager.get_schema("manifest", "9.9")

        with self.assertRaisesRegex(ValueError, "Schema 'nonexistent_schema' not found."):
            manager.get_schema("nonexistent_schema", "1.0")

    @mock.patch("pkg_resources.resource_listdir")
    @mock.patch("pkg_resources.resource_filename")
    @mock.patch("builtins.open", new_callable=mock.mock_open)
    @mock.patch("yaml.safe_load")
    def test_schema_parsing_logic_in_load_schemas(self, mock_yaml_safe_load, mock_open, mock_resource_filename, mock_resource_listdir):
        """
        Test the internal logic of _load_schemas for parsing filename to schema_name and version.
        This tests how files like 'manifest_v1.0.yaml' are translated internally.
        """
        # Reset the singleton before this specific test.
        KFSSchemaManager._instance = None
        KFSSchemaManager._schemas = {}

        # Simulate finding two different schema files
        mock_resource_listdir.return_value = ["manifest_v1.0.yaml", "geometry_v2.1.yaml"]

        # Set up side_effect for resource_filename to return different paths for different calls
        def resource_filename_side_effect(package, filename):
            if "manifest_v1.0.yaml" in filename:
                return "/mock/path/manifest_v1.0.yaml"
            elif "geometry_v2.1.yaml" in filename:
                return "/mock/path/geometry_v2.1.yaml"
            return None
        mock_resource_filename.side_effect = resource_filename_side_effect

        # Set up side_effect for open to return different content for different paths
        def open_side_effect(path, mode='r'):
            if "/mock/path/manifest_v1.0.yaml" in path:
                return mock.mock_open(read_data=self.mock_manifest_schema_content)()
            elif "/mock/path/geometry_v2.1.yaml" in path:
                return mock.mock_open(read_data='title: Geometry Schema v2.1\nproperties:\n  type: {type: string}')()
            return mock.mock_open()()
        mock_open.side_effect = open_side_effect

        # Set up side_effect for yaml.safe_load to return different parsed schemas
        mock_yaml_safe_load.side_effect = [
            self.expected_manifest_schema,
            {'title': 'Geometry Schema v2.1', 'properties': {'type': {'type': 'string'}}} # Dummy schema for geometry
        ]

        manager = KFSSchemaManager()

        # Assert correct loading and parsing
        self.assertIn("manifest", manager._schemas)
        self.assertIn("1.0", manager._schemas["manifest"])
        self.assertEqual(manager._schemas["manifest"]["1.0"], self.expected_manifest_schema)

        self.assertIn("geometry", manager._schemas)
        self.assertIn("2.1", manager._schemas["geometry"])
        self.assertEqual(manager._schemas["geometry"]["2.1"], {'title': 'Geometry Schema v2.1', 'properties': {'type': {'type': 'string'}}})

        # Check call counts for mocks
        self.assertEqual(mock_resource_listdir.call_count, 1)
        self.assertEqual(mock_resource_filename.call_count, 2)
        self.assertEqual(mock_open.call_count, 2)
        self.assertEqual(mock_yaml_safe_load.call_count, 2)

    @mock.patch("pkg_resources.resource_listdir", return_value=["invalid_schema.txt"])
    @mock.patch("logging.Logger.debug")  # Mock logger to check if debug message is called
    @mock.patch("pkg_resources.resource_filename")
    @mock.patch("builtins.open")
    @mock.patch("yaml.safe_load")
    def test_load_schemas_skips_non_yaml(self, mock_yaml_safe_load, mock_open, mock_resource_filename, mock_logger_debug, mock_resource_listdir):
        """
        Test that _load_schemas skips files that are not .yaml.
        """
        KFSSchemaManager._instance = None
        KFSSchemaManager._schemas = {}

        manager = KFSSchemaManager()
        self.assertEqual(manager._schemas, {})  # No schemas should be loaded
        mock_resource_listdir.assert_called_once_with("backend.kfs_schema_manager", "schemas")  # listdir should be called
        mock_logger_debug.assert_called_once_with("Skipping non-YAML file: invalid_schema.txt")
        mock_resource_filename.assert_not_called()
        mock_open.assert_not_called()
        mock_yaml_safe_load.assert_not_called()

    @mock.patch("pkg_resources.resource_listdir", return_value=["malformed_v1.0.yaml"])
    @mock.patch("pkg_resources.resource_filename", return_value="/mock/path/malformed_v1.0.yaml")
    @mock.patch("builtins.open", new_callable=mock.mock_open)
    @mock.patch("yaml.safe_load", side_effect=yaml.YAMLError("Malformed YAML"))
    @mock.patch("logging.Logger.error")  # Mock logger to check if error message is called
    def test_load_schemas_handles_yaml_errors(self, mock_logger_error, mock_yaml_safe_load, mock_open, mock_resource_filename, mock_resource_listdir):
        """
        Test that _load_schemas gracefully handles malformed YAML files (e.g., logs an error and skips).
        For this test, we expect the manager to initialize but not store the malformed schema.
        """
        KFSSchemaManager._instance = None
        KFSSchemaManager._schemas = {}

        mock_open.return_value.__enter__.return_value.read.return_value = "this is not yaml: {"

        manager = KFSSchemaManager()
        self.assertEqual(manager._schemas, {})  # Should not have loaded the malformed schema

        mock_yaml_safe_load.assert_called_once_with("this is not yaml: {")  # Should have tried to load it
        mock_resource_filename.assert_called_once_with("backend.kfs_schema_manager", os.path.join("schemas", "malformed_v1.0.yaml"))
        mock_open.assert_called_once_with("/mock/path/malformed_v1.0.yaml", "r")
        mock_logger_error.assert_called_once()  # Verify an error was logged

    @mock.patch("pkg_resources.resource_listdir", return_value=["manifest.yaml"])  # No version in filename
    @mock.patch("logging.Logger.warning")  # Mock logger to check for warning
    @mock.patch("pkg_resources.resource_filename")
    @mock.patch("builtins.open")
    @mock.patch("yaml.safe_load")
    def test_load_schemas_skips_unversioned_files(self, mock_yaml_safe_load, mock_open, mock_resource_filename, mock_logger_warning, mock_resource_listdir):
        """
        Test that _load_schemas skips YAML files that do not follow the 'name_vX.Y.yaml' naming convention.
        """
        KFSSchemaManager._instance = None
        KFSSchemaManager._schemas = {}

        manager = KFSSchemaManager()
        self.assertEqual(manager._schemas, {})  # No schema should be loaded
        mock_resource_listdir.assert_called_once_with("backend.kfs_schema_manager", "schemas")
        mock_logger_warning.assert_called_once_with(
            "Skipping schema file with invalid naming convention (expected <name>_v<major>.<minor>.yaml): manifest.yaml"
        )
        mock_resource_filename.assert_not_called()
        mock_open.assert_not_called()
        mock_yaml_safe_load.assert_not_called()

    @mock.patch("pkg_resources.resource_listdir", return_value=["manifest_v1_0.yaml"])  # Invalid version format
    @mock.patch("logging.Logger.warning")  # Mock logger to check for warning
    @mock.patch("pkg_resources.resource_filename")
    @mock.patch("builtins.open")
    @mock.patch("yaml.safe_load")
    def test_load_schemas_skips_invalid_version_format(self, mock_yaml_safe_load, mock_open, mock_resource_filename, mock_logger_warning, mock_resource_listdir):
        """
        Test that _load_schemas skips YAML files with invalid version format (e.g., 'v1_0' instead of 'v1.0').
        """
        KFSSchemaManager._instance = None
        KFSSchemaManager._schemas = {}

        manager = KFSSchemaManager()
        self.assertEqual(manager._schemas, {})  # No schema should be loaded
        mock_resource_listdir.assert_called_once_with("backend.kfs_schema_manager", "schemas")
        mock_logger_warning.assert_called_once_with(
            "Skipping schema file with invalid version format in filename (expected major.minor): manifest_v1_0.yaml"
        )
        mock_resource_filename.assert_not_called()
        mock_open.assert_not_called()
        mock_yaml_safe_load.assert_not_called()

    @mock.patch("pkg_resources.resource_listdir", side_effect=Exception("Cannot list resources"))
    @mock.patch("logging.Logger.error")  # Mock logger to check if error message is called
    def test_load_schemas_handles_listdir_failure(self, mock_logger_error, mock_resource_listdir):
        """
        Test that _load_schemas gracefully handles failures when listing resources.
        The manager should still initialize, but no schemas should be loaded.
        """
        KFSSchemaManager._instance = None
        KFSSchemaManager._schemas = {}

        manager = KFSSchemaManager()
        self.assertEqual(manager._schemas, {})
        mock_resource_listdir.assert_called_once_with("backend.kfs_schema_manager", "schemas")
        mock_logger_error.assert_called_once_with(f"Failed to list schema files in '{KFSSchemaManager._schema_dir}': Cannot list resources")


if __name__ == '__main__':
    unittest.main()
