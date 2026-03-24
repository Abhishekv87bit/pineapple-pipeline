from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class GeometryType(str, Enum):
    """Enumeration of supported geometry types."""
    BOX = "box"
    CYLINDER = "cylinder"
    SPHERE = "sphere"
    STL = "stl"
    MESH = "mesh"


class SolverType(str, Enum):
    """Enumeration of supported motion simulation solver types."""
    OPENMM = "openmm"
    PYBULLET = "pybullet"
    CUSTOM = "custom_solver"


class Geometry(BaseModel):
    """Represents a single geometry definition within the KFS manifest."""
    name: str = Field(..., description="Unique name for the geometry object.")
    type: GeometryType = Field(..., description="Type of the geometry (e.g., box, cylinder, stl).")
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Dictionary of type-specific parameters for the geometry (e.g., dimensions, radius, file_path).",
    )


class Simulation(BaseModel):
    """Represents a single motion simulation definition within the KFS manifest."""
    name: str = Field(..., description="Unique name for the simulation task.")
    solver: SolverType = Field(..., description="The simulation solver to use.")
    target_geometries: List[str] = Field(
        default_factory=list,
        description="List of names of geometries (defined in this manifest) that this simulation targets.",
    )
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Dictionary of solver-specific parameters (e.g., time_step, duration, initial_conditions).",
    )


class KFSManifest(BaseModel):
    """The top-level structure for a Kinetic Forge Studio (KFS) manifest file."""

    version: str = Field("1.0", description="Version of the KFS manifest schema.")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional metadata about the project or manifest.",
    )
    geometries: List[Geometry] = Field(
        default_factory=list,
        description="List of geometry definitions.",
    )
    simulations: List[Simulation] = Field(
        default_factory=list,
        description="List of motion simulation definitions.",
    )
