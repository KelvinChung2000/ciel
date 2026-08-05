# Copyright 2026 The American University in Cairo
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
ASAP7, a 7nm FinFET predictive process design kit, together with the 7.5-track
and 6-track standard cell libraries and the SRAM macros distributed with it.
It is predictive rather than manufacturable, and is BSD-3-Clause licensed.

Upstream asks that published work using ASAP7 cite:

* L. T. Clark, V. Vashishtha, L. Shifren, A. Gujja, S. Sinha, B. Cline,
  C. Ramamurthy and G. Yeric, "ASAP: A 7-nm finFET predictive process design
  kit," Microelectronics Journal, vol. 53, pp. 105-115, Jul. 2016, for the
  PDK; and
* V. Vashishtha, M. Vangala and L. T. Clark, "ASAP7 predictive design kit
  development and cell design technology co-optimization," Proc. ICCAD,
  pp. 992-998, Nov. 2017, for the 7.5-track standard cell library.

The ASAP7 repository is a superproject: it holds nothing but submodules, one
per piece of collateral. It is not distributed in the open_pdks format, so
the build is trivial, assembling the submodules side by side as a single
variant.
"""
import os
import re
import shutil
import subprocess
from datetime import datetime
from typing import Optional, List, Dict
from concurrent.futures import ThreadPoolExecutor

from rich.console import Console
from rich.progress import Progress

from .common import (
    copy_upstream_tree,
    get_clone_directory,
    install_trivial_build,
    make_descriptor_dir,
)
from .git_multi_clone import GitMultiClone
from ..github import RepoInfo, asap7_repo
from ..common import (
    get_ciel_dir,
    mkdirp,
)

# The standard cell libraries are ~3 GiB checked out, almost all of it CCS
# timing models, browsable datasheets and Cadence QRC tech files that no
# open-source flow reads. These are the directories that are kept.
STANDARD_CELL_PATHS = [
    os.path.join("CDL", "LVS"),
    "GDS",
    "LEF",
    os.path.join("LIB", "NLDM"),
    "Verilog",
    "license",
    "techlef_misc",
]

# Submodules of the asap7 superproject, mapped to the sparse-checkout cone
# each one is narrowed to (None meaning "check the repository out whole").
#
# asap7sc7p5t_27 is deliberately absent: it is the previous revision of the
# 7.5-track library that asap7sc7p5t_28 supersedes, and cloning it costs
# another multi-gigabyte repository for collateral nothing would read.
SUBMODULES: Dict[str, Optional[List[str]]] = {
    "asap7_pdk_r1p7": None,
    "asap7_sram_0p0": None,
    "asap7sc6t_26": STANDARD_CELL_PATHS,
    "asap7sc7p5t_28": STANDARD_CELL_PATHS,
}

# asap7's LibreLane descriptors, which ship with ciel rather than with the
# PDK: ASAP7 is described upstream by a Cadence example script, and nothing in
# the superproject knows what a pdk.yaml is. Copied into the variant's
# libs.tech/librelane so that an installed asap7 is configured for LibreLane
# the moment it is fetched.
DESCRIPTOR_PATH = os.path.join(os.path.dirname(__file__), "descriptors", "asap7")

# Upstream compresses every liberty file with 7-Zip, which nothing in an
# open-source flow reads, so the descriptor's LIB entries point at copies
# unpacked into the descriptor directory here. Only the RVT flavour's three
# NLDM corners are unpacked, because that is what the one SCL the descriptor
# declares uses; unpacking the rest would add ~800 MiB nothing reads.
LIBERTY_SOURCE = os.path.join("asap7sc7p5t_28", "LIB", "NLDM")
LIBERTY_ARCHIVES = [
    f"asap7sc7p5t_{group}_RVT_{corner}_nldm_{date}.lib.7z"
    for group, date in [
        ("AO", "211120"),
        ("INVBUF", "220122"),
        ("OA", "211120"),
        ("SEQ", "220123"),
        ("SIMPLE", "211120"),
    ]
    for corner in ["TT", "FF", "SS"]
]
LIBERTY_DESTINATION = os.path.join("asap7sc7p5t_28_rvt", "lib")


def get_submodule_commit(superproject_path: str, version: str, submodule: str) -> str:
    entry = subprocess.check_output(
        ["git", "ls-tree", version, submodule],
        cwd=superproject_path,
        encoding="utf8",
    ).split()
    if len(entry) < 3 or entry[1] != "commit":
        raise ValueError(
            f"asap7 {version} does not record a submodule at '{submodule}'."
        )
    return entry[2]


def get_asap7(
    version, build_directory, jobs=1, using_repos: Optional[Dict[str, str]] = None
) -> Dict[str, str]:
    """
    :returns: The paths of the superproject and of every submodule in
        ``SUBMODULES``, keyed by repository name.
    """
    if using_repos is None:
        using_repos = {}
    try:
        console = Console()
        paths: Dict[str, str] = {}

        clone_directory = get_clone_directory(build_directory)
        with Progress() as progress:
            with ThreadPoolExecutor(max_workers=jobs) as executor:
                gmc = GitMultiClone(clone_directory, progress)

                superproject_path = using_repos.get("asap7")
                if superproject_path is None:
                    superproject_path = (
                        executor.submit(
                            GitMultiClone.clone,
                            gmc,
                            asap7_repo.link,
                            version,
                        )
                        .result()
                        .path
                    )
                    console.log(f"Done fetching {asap7_repo.name}.")
                else:
                    console.log(f"Using asap7 at {superproject_path} unaltered.")
                paths[asap7_repo.name] = superproject_path

                # The submodules are cloned as repositories in their own right
                # rather than through `git submodule update`, because they need
                # the sparse checkouts above and `git submodule update` has no
                # way to ask for one.
                futures = {}
                for submodule, sparse_paths in SUBMODULES.items():
                    if submodule in using_repos:
                        paths[submodule] = using_repos[submodule]
                        console.log(
                            f"Using {submodule} at {paths[submodule]} unaltered."
                        )
                        continue
                    commit = get_submodule_commit(superproject_path, version, submodule)
                    futures[submodule] = executor.submit(
                        GitMultiClone.clone,
                        gmc,
                        RepoInfo(asap7_repo.owner, submodule).link,
                        commit,
                        blobless=True,
                        sparse_paths=sparse_paths,
                    )
                for submodule, future in futures.items():
                    paths[submodule] = future.result().path
                    console.log(f"Done fetching {submodule}.")

        return paths

    except subprocess.CalledProcessError as e:
        print(e)
        print(e.stderr)
        exit(-1)


def build_asap7(build_directory, paths: Dict[str, str]):
    variant_directory = os.path.join(build_directory, "asap7")
    try:
        shutil.rmtree(variant_directory)
    except FileNotFoundError:
        pass
    mkdirp(variant_directory)

    superproject_path = paths[asap7_repo.name]
    for file in ["README.md", "LICENSE"]:
        source = os.path.join(superproject_path, file)
        if os.path.isfile(source):
            shutil.copy2(source, os.path.join(variant_directory, file))

    for submodule in SUBMODULES:
        copy_upstream_tree(paths[submodule], os.path.join(variant_directory, submodule))

    make_descriptor_dir(variant_directory, "asap7")
    descriptor_directory = os.path.join(variant_directory, "libs.tech", "librelane")
    # Merged into the directory make_descriptor_dir just made, rather than
    # replacing it, so the README it wrote survives beside the descriptors.
    shutil.copytree(DESCRIPTOR_PATH, descriptor_directory, dirs_exist_ok=True)
    build_liberty(variant_directory, descriptor_directory)


def build_liberty(variant_directory: str, descriptor_directory: str):
    """
    Produces the three liberty files the descriptor's ``LIB`` entries name, in
    ``<descriptor_directory>/asap7sc7p5t_28_rvt/lib``.

    Upstream ships each corner as five 7-Zip archives, one per cell group.
    Neither shape is usable: nothing in an open-source flow reads 7-Zip, and
    Yosys drops a library when a corner supplies more than one file -- the
    INVBUF group went missing on the way into ABC's merged .scl, leaving it
    with no inverter and no buffer and segfaulting its mapper. So each corner
    is unpacked and then concatenated into one library, which is the shape
    every other PDK gives LibreLane.
    """
    source_directory = os.path.join(variant_directory, LIBERTY_SOURCE)
    destination = os.path.join(descriptor_directory, LIBERTY_DESTINATION)
    mkdirp(destination)

    # bsdtar reads 7-Zip through libarchive and is what macOS installs as
    # ``tar``; ``7z``/``7za`` are the p7zip spelling. One of them has to be on
    # PATH: no 7-Zip reader is in the standard library and ciel does not
    # depend on one, so there is no Python fallback.
    extractor = None
    for candidate in ["bsdtar", "7z", "7za", "tar"]:
        if shutil.which(candidate) is not None:
            extractor = candidate
            break
    if extractor is None:
        raise RuntimeError(
            "asap7 ships its liberty files as 7-Zip archives and none of "
            "bsdtar, 7z or 7za is on PATH to unpack them. Install p7zip or "
            "libarchive and build again."
        )

    unpacked = os.path.join(destination, "unpacked")
    mkdirp(unpacked)
    for archive in LIBERTY_ARCHIVES:
        archive_path = os.path.join(source_directory, archive)
        if extractor in ["7z", "7za"]:
            command = [extractor, "x", "-y", f"-o{unpacked}", archive_path]
        else:
            command = [extractor, "-x", "-f", archive_path, "-C", unpacked]
        subprocess.check_call(command, stdout=subprocess.DEVNULL)

    for corner in ["TT", "FF", "SS"]:
        parts = sorted(
            os.path.join(unpacked, name)
            for name in os.listdir(unpacked)
            if re.fullmatch(rf"asap7sc7p5t_\w+_RVT_{corner}_nldm_\d+\.lib", name)
        )
        merged = merge_liberty(parts, f"asap7sc7p5t_RVT_{corner}_nldm")
        with open(
            os.path.join(destination, f"asap7sc7p5t_RVT_{corner}_nldm.lib"), "w"
        ) as f:
            f.write(merged)
    shutil.rmtree(unpacked)


def _split_library(text: str):
    """
    Splits one liberty file into (preamble, cell groups).

    The preamble is everything inside ``library (...) { ... }`` ahead of the
    first cell: the units, the operating conditions and the lookup-table
    templates.
    """
    brace = text.index("{", text.index("library ("))
    body = text[brace + 1 : text.rindex("}")]

    cells = []
    first = None
    for match in re.finditer(r"^  cell \((\S+)\) \{", body, re.M):
        if first is None:
            first = match.start()
        depth = 0
        for i in range(match.start(), len(body)):
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
                if depth == 0:
                    cells.append(body[match.start() : i + 1])
                    break
    return body[:first], cells


def _preamble_groups(preamble: str) -> Dict[str, str]:
    """The preamble's top-level named groups, keyed ``type(name)``."""
    groups = {}
    for match in re.finditer(r"^  (\w+) \((\S+?)\) \{", preamble, re.M):
        depth = 0
        for i in range(match.start(), len(preamble)):
            if preamble[i] == "{":
                depth += 1
            elif preamble[i] == "}":
                depth -= 1
                if depth == 0:
                    key = f"{match.group(1)}({match.group(2)})"
                    groups[key] = preamble[match.start() : i + 1]
                    break
    return groups


def merge_liberty(paths: List[str], library_name: str) -> str:
    """
    Concatenates liberty files that describe one corner into one library.

    Safe for asap7 specifically: the five files per corner share their units
    and operating conditions, and every lookup-table template they define is
    either byte-identical across files or unique to one, so the merge adds
    templates and never redefines one. A template that disagreed between two
    files would need renaming and reference rewriting, which this does not do
    -- so it raises instead.
    """
    preamble, cells = _split_library(open(paths[0]).read())
    seen = _preamble_groups(preamble)
    additional = []
    for path in paths[1:]:
        other_preamble, other_cells = _split_library(open(path).read())
        for key, text in _preamble_groups(other_preamble).items():
            if key in seen:
                if seen[key] != text:
                    raise RuntimeError(
                        f"'{os.path.basename(path)}' redefines '{key}' with a "
                        f"different body; the corner's files can no longer be "
                        f"concatenated as they are."
                    )
                continue
            seen[key] = text
            additional.append(text)
        cells += other_cells

    return (
        f"library ({library_name}) {{\n"
        + preamble.rstrip("\n")
        + "\n"
        + "\n".join(additional)
        + "\n"
        + "\n".join(cells)
        + "\n}\n"
    )


def install_asap7(build_directory, pdk_root, version):
    install_trivial_build(build_directory, pdk_root, version, "asap7")


def build(
    pdk_root: str,
    version: str,
    jobs: int = 1,
    clear_build_artifacts: bool = True,
    include_libraries: Optional[List[str]] = None,
    using_repos: Optional[Dict[str, str]] = None,
):
    console = Console()
    if include_libraries is not None:
        console.log(
            "Note: all libraries will be acquired as part of the trivial PDK build."
        )

    if using_repos is None:
        using_repos = {}

    build_directory = os.path.join(get_ciel_dir(pdk_root, "asap7"), "build", version)
    timestamp = datetime.now().strftime("build_asap7-%Y-%m-%d-%H-%M-%S")
    log_dir = os.path.join(build_directory, "logs", timestamp)
    mkdirp(log_dir)

    console.log(f"Logging to '{log_dir}'…")

    paths = get_asap7(version, build_directory, jobs, using_repos)
    build_asap7(build_directory, paths)
    install_asap7(build_directory, pdk_root, version)

    if clear_build_artifacts:
        shutil.rmtree(build_directory)
