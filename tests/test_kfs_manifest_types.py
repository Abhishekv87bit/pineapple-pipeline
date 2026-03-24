import pytest
from backend.kfs_manifest_types import (
    Vector3,
    Color,
    MeshComponent,
    Scene,
    Manifest,
)

def test_vector3_creation():
    vec = Vector3(x=1.0, y=2.0, z=3.0)
    assert vec.x == 1.0
    assert vec.y == 2.0
    assert vec.z == 3.0

def test_color_creation():
    color = Color(r=1.0, g=0.5, b=0.0)
    assert color.r == 1.0
    assert color.g == 0.5
    assert color.b == 0.0
    assert color.a == 1.0 # Default value

def test_color_creation_with_alpha():
    color = Color(r=0.2, g=0.4, b=0.6, a=0.8)
    assert color.r == 0.2
    assert color.g == 0.4
    assert color.b == 0.6
    assert color.a == 0.8

def test_mesh_component_creation():
    mesh = MeshComponent(path="models/cube.obj", material="red_plastic")
    assert mesh.path == "models/cube.obj"
    assert mesh.material == "red_plastic"
    assert mesh.transform is None

def test_mesh_component_creation_with_transform():
    transform_data = {"position": [0, 0, 0], "rotation": [0, 0, 0, 1]}
    mesh = MeshComponent(
        path="models/sphere.obj", material="blue_metal", transform=transform_data
    )
    assert mesh.path == "models/sphere.obj"
    assert mesh.material == "blue_metal"
    assert mesh.transform == transform_data

def test_scene_creation():
    gravity = Vector3(x=0.0, y=-9.81, z=0.0)
    bg_color = Color(r=0.1, g=0.1, b=0.1, a=1.0)
    mesh1 = MeshComponent(path="m1.obj", material="mat1")
    mesh2 = MeshComponent(path="m2.obj", material="mat2")

    scene = Scene(
        name="MyScene",
        components=[mesh1, mesh2],
        gravity=gravity,
        background_color=bg_color,
    )

    assert scene.name == "MyScene"
    assert len(scene.components) == 2
    assert scene.components[0].path == "m1.obj"
    assert scene.gravity.y == -9.81
    assert scene.background_color.r == 0.1

def test_manifest_creation():
    gravity = Vector3(x=0.0, y=-9.81, z=0.0)
    bg_color = Color(r=0.1, g=0.1, b=0.1, a=1.0)
    mesh1 = MeshComponent(path="m1.obj", material="mat1")
    scene = Scene(
        name="MyScene",
        components=[mesh1],
        gravity=gravity,
        background_color=bg_color,
    )

    manifest = Manifest(version="1.0", scene=scene)
    assert manifest.version == "1.0"
    assert manifest.scene.name == "MyScene"
    assert manifest.metadata is None

def test_manifest_creation_with_metadata():
    gravity = Vector3(x=0.0, y=-9.81, z=0.0)
    bg_color = Color(r=0.1, g=0.1, b=0.1, a=1.0)
    mesh1 = MeshComponent(path="m1.obj", material="mat1")
    scene = Scene(
        name="MyScene",
        components=[mesh1],
        gravity=gravity,
        background_color=bg_color,
    )
    metadata = {"author": "John Doe", "date": "2023-10-26"}

    manifest = Manifest(version="1.0", scene=scene, metadata=metadata)
    assert manifest.version == "1.0"
    assert manifest.scene.name == "MyScene"
    assert manifest.metadata == metadata
