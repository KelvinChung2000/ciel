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
