"""Create a stl from NXtransformation groups"""
import os
from sys import version_info
from os import path
from typing import Callable, Dict, List, Mapping, Union
import numpy as np
from numpy.typing import NDArray
import h5py
from pint import UnitRegistry
import click

from nexus3d.matrix import rotate, translate
from nexus3d.formats.stl_cube_mesh import write_stl_file
from nexus3d.formats.gltf_cube_mesh import write_gltf_file

ureg = UnitRegistry()

TransformationMatrixDict = Mapping[
    str, Union[List[NDArray[np.float64]], NDArray[np.float64]]
]


def transformation_matrices_from(
    fname: str, include_process: bool, store_intermediate: bool = False
) -> TransformationMatrixDict:
    """Reads all NXtransformations from a nexus file
    and creates a transformation matrix from them."""

    def store_in_chain(matrix: NDArray[np.float64]):
        if store_intermediate:
            matrix_chain.append(matrix)

        return matrix

    def get_transformation_matrix(h5file: h5py.File, entry: str):
        required_attrs = ["depends_on", "vector", "transformation_type", "units"]
        attrs = h5file[entry].attrs

        for req_attr in required_attrs:
            if req_attr not in attrs:
                raise ValueError(f"`{req_attr}` attribute not found in {entry}")

        if "offset" in attrs and "offsets_units" not in attrs:
            raise ValueError(
                f"Found `offset` attribute in {entry} but no `offset_units` could be found."
            )
        offset = attrs.get("offset", np.zeros((3,)))
        offset_unit = ureg(attrs.get("offset_unit", ""))
        offset_si = (offset * offset_unit).to_base_units().magnitude

        vector = attrs["vector"]

        # Always use the first element and ignore the dimension
        # With gltf we probably could also animate it!
        field = h5file[entry][()].flat[0]
        if not isinstance(field, (int, float)):
            raise NotImplementedError("Only float fields are supported yet.")
        field_si = ureg(f"{field} {attrs['units']}").to_base_units().magnitude  # type: ignore

        if attrs["transformation_type"] == "translation":
            matrix = translate(field_si * vector, offset_si)
        elif attrs["transformation_type"] == "rotation":
            matrix = rotate(field_si, vector, offset_si)
        else:
            raise ValueError(
                f"Unknown transformation type `{attrs['transformation_type']}`"
            )

        if attrs["depends_on"] == ".":
            return store_in_chain(matrix)

        if "/" in attrs["depends_on"]:
            return store_in_chain(
                get_transformation_matrix(h5file, attrs["depends_on"]) @ matrix
            )

        return store_in_chain(
            get_transformation_matrix(
                h5file, f"{entry.rsplit('/', 1)[0]}/{attrs['depends_on']}"
            )
            @ matrix
        )

    def get_transformation_group_names(name: str, dataset: h5py.Dataset):
        if not include_process and name.startswith("entry/process"):
            return

        if "depends_on" in name:
            transformation_groups[
                name.rsplit("/", 1)[0].split("/", 1)[-1]
                if version_info.minor < 9
                else name.removesuffix("/depends_on").removeprefix("entry/")
            ] = dataset[()].decode("utf-8")

    transformation_groups: Dict[str, h5py.Dataset] = {}
    transformation_matrices = {}
    with h5py.File(fname, "r") as h5file:
        h5file.visititems(get_transformation_group_names)

        for name, transformation_group in transformation_groups.items():
            if store_intermediate:
                matrix_chain: List[NDArray[np.float64]] = []

            transformation_matrix = get_transformation_matrix(
                h5file, transformation_group
            )

            transformation_matrices[name] = (
                matrix_chain if store_intermediate else transformation_matrix
            )

    return transformation_matrices


@click.command()
@click.argument("file")
@click.option(
    "-o",
    "--output",
    default="experiment.glb",
    help="The filename to write to (default: experiment.glb).",
)
@click.option(
    "-s",
    "--size",
    default=0.1,
    type=float,
    help="The side length of a cube in meters. (default: 0.1 m).",
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    default=False,
    type=bool,
    help="Force overwriting of output file.",
)
@click.option(
    "--include-process",
    is_flag=True,
    default=False,
    type=bool,
    help="Include transformations inside /entry/process",
)
@click.option(
    "--store-intermediate",
    is_flag=True,
    default=False,
    type=bool,
    help=(
        "Store the intermediate matrices in gltf child nodes. "
        "Only applicable for gltf or glb files."
    ),
)
def cli(  # pylint: disable=too-many-arguments
    file: str,
    output: str,
    force: bool,
    size: float,
    include_process: bool,
    store_intermediate: bool,
):
    """
    Create a glb/gltf or stl from a nexus file via the command line.
    The actual file format is chosen from the
    file ending of the output file (default: experiment.glb).
    """

    if not path.exists(file):
        raise click.FileError(file, hint="File does not exist.")

    if not path.isfile(file):
        raise click.FileError(file, hint="Is not a file.")

    if not h5py.is_hdf5(file):
        raise click.FileError(file, hint="Not a valid HDF5 file.")

    if path.exists(output) and not force:
        raise click.FileError(output, hint="File already exists. Use -f to overwrite.")

    file_format = os.path.splitext(output)[1]
    if file_format not in [".stl", ".gltf", ".glb"]:
        raise click.UsageError(
            message=f"Unsupported file format {file_format} in output file `{output}`"
        )

    if size <= 0:
        raise click.BadOptionUsage(
            "size", f"Not a valid size: {size}. Size needs to be > 0."
        )

    def format_not_implemented(output: str, *_args, **_kwargs):
        raise NotImplementedError(
            f"Cannot write to format {file_format} for output file {output}"
        )

    format_map: Dict[str, Callable[[str, TransformationMatrixDict, float], None]] = {
        ".stl": write_stl_file,
        ".gltf": write_gltf_file,
        ".glb": write_gltf_file,
    }

    format_map.get(file_format, format_not_implemented)(
        output,
        transformation_matrices_from(file, include_process, store_intermediate),
        size,
    )
