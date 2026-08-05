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
NanGate45, the 45nm platform OpenROAD-flow-scripts builds around the NanGate
Open Cell Library and the FreePDK45 technology files, together with the
fakeram45 generated SRAM macros distributed alongside it. The cell library is
open-source and deliberately non-manufacturable, which makes the platform a
common target for testing and benchmarking flows.

The platform is not distributed in the open_pdks format, so the build is
trivial: the platform directory is copied out of OpenROAD-flow-scripts at the
requested commit and installed as a single variant.
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
from ..github import orfs_repo
from ..common import (
    get_ciel_dir,
    mkdirp,
)

# The platform is one directory of a repository that is ~880 MB cloned whole,
# so the clone is blobless and narrowed to that directory.
PLATFORM_PATH = os.path.join("flow", "platforms", "nangate45")

# nangate45's LibreLane descriptors, which ship with ciel rather than with the
# platform: OpenROAD-flow-scripts describes the platform in config.mk and a
# handful of Tcl fragments, and nothing upstream knows what a pdk.yaml is.
# Copied into the variant's libs.tech/librelane so that an installed nangate45
# is configured for LibreLane the moment it is fetched.
DESCRIPTOR_PATH = os.path.join(os.path.dirname(__file__), "descriptors", "nangate45")


def get_orfs(version, build_directory, jobs=1, repo_path=None) -> str:
    try:
        console = Console()

        if repo_path is None:
            with Progress() as progress:
                with ThreadPoolExecutor(max_workers=jobs) as executor:
                    gmc = GitMultiClone(get_clone_directory(build_directory), progress)
                    orfs_future = executor.submit(
                        GitMultiClone.clone,
                        gmc,
                        orfs_repo.link,
                        version,
                        default_branch="master",
                        blobless=True,
                        sparse_paths=[PLATFORM_PATH],
                    )
                    repo = orfs_future.result()
                    repo_path = repo.path
            console.log(f"Done fetching {orfs_repo.name}.")
        else:
            console.log(f"Using OpenROAD-flow-scripts at {repo_path} unaltered.")

        return repo_path

    except subprocess.CalledProcessError as e:
        print(e)
        print(e.stderr)
        exit(-1)


def build_nangate45(build_directory, orfs_path):
    variant_directory = os.path.join(build_directory, "nangate45")
    platform_path = os.path.join(orfs_path, PLATFORM_PATH)
    if not os.path.isdir(platform_path):
        raise FileNotFoundError(
            f"{platform_path} not found: this OpenROAD-flow-scripts checkout does not carry the nangate45 platform."
        )
    copy_upstream_tree(platform_path, variant_directory)
    make_descriptor_dir(variant_directory, "nangate45")
    # Over the README make_descriptor_dir just wrote, which is why this is a
    # merge into the directory and not a replacement of it.
    shutil.copytree(
        DESCRIPTOR_PATH,
        os.path.join(variant_directory, "libs.tech", "librelane"),
        dirs_exist_ok=True,
    )


def install_nangate45(build_directory, pdk_root, version):
    install_trivial_build(build_directory, pdk_root, version, "nangate45")


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

    build_directory = os.path.join(
        get_ciel_dir(pdk_root, "nangate45"), "build", version
    )
    timestamp = datetime.now().strftime("build_nangate45-%Y-%m-%d-%H-%M-%S")
    log_dir = os.path.join(build_directory, "logs", timestamp)
    mkdirp(log_dir)

    console.log(f"Logging to '{log_dir}'…")

    orfs_path = get_orfs(version, build_directory, jobs, using_repos.get("orfs"))
    build_nangate45(build_directory, orfs_path)
    install_nangate45(build_directory, pdk_root, version)

    if clear_build_artifacts:
        shutil.rmtree(build_directory)
