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
GT2N, an open 2nm nanosheet PDK: gate-all-around FETs with a backside power
delivery network, 72 standard cells across two nanosheet widths and five
threshold voltage flavours. It is a predictive PDK rather than a
manufacturable one, and is BSD-3-Clause licensed.

Upstream asks that published work using GT2N cite:

* D. Jang, P. Kumar, M. N. H. Shazon, S. J. Ram, A. Svizhenko, V. Moroz,
  A. Ceyhan, N. A. Radhakrishn and A. Naeemi, "GT2N: An Open-Source 2nm
  Nanosheet PDK Enabling Multi-Width/VT Benchmarking," IEEE International
  Symposium on Circuits and Systems (ISCAS), 2026.

The PDK is published across two repositories. lambdapdk carries the tool
setup - KLayout technology and display files, OpenROAD power grid, global
connect and tapcell scripts - and names the revision of the GT2N data
repository it was written against; that repository carries the LEF, GDS,
Liberty, CDL and SPICE. Neither is in the open_pdks format, so the build is
trivial, pairing the two into a single variant.
"""
import os
import re
import shutil
import subprocess
from datetime import datetime
from typing import Optional, List, Dict, Tuple
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
from ..github import gt2n_repo, lambdapdk_repo
from ..common import (
    get_ciel_dir,
    mkdirp,
)

# lambdapdk is ~405 MB cloned whole and gt2n is one directory of it.
LAMBDAPDK_PATH = os.path.join("lambdapdk", "gt2n")

# lambdapdk names the GT2N commit its setup was written against, e.g.
#     pdk_rev = '54f81feb2b9c334d283538c1bc91bf3a34b02c02'
PDK_REV_RX = re.compile(r"^pdk_rev\s*=\s*['\"]([0-9a-fA-F]{7,40})['\"]", re.MULTILINE)

# GT2N's LibreLane descriptors, which ship with ciel rather than with the PDK:
# neither upstream repository is in the open_pdks format and neither knows what
# a pdk.yaml is. Copied into the variant's libs.tech/librelane so that an
# installed gt2n is configured for LibreLane the moment it is fetched. One
# descriptor directory per (nanosheet width, VT) flavour, beside the shared
# track grid and the OpenRCX pattern file.
DESCRIPTOR_PATH = os.path.join(os.path.dirname(__file__), "descriptors", "gt2n")


def get_data_commit(lambdapdk_path: str) -> str:
    module = os.path.join(lambdapdk_path, LAMBDAPDK_PATH, "__init__.py")
    match = PDK_REV_RX.search(open(module, encoding="utf8").read())
    if match is None:
        raise ValueError(
            f"{module} does not declare a pdk_rev: this revision of lambdapdk does not pin the GT2N data repository the way ciel expects."
        )
    return match[1]


def get_gt2n(
    version, build_directory, jobs=1, using_repos: Optional[Dict[str, str]] = None
) -> Tuple[str, str]:
    """
    :returns: The paths of the lambdapdk and GT2N checkouts, in that order.
    """
    if using_repos is None:
        using_repos = {}
    try:
        console = Console()

        clone_directory = get_clone_directory(build_directory)
        with Progress() as progress:
            with ThreadPoolExecutor(max_workers=jobs) as executor:
                gmc = GitMultiClone(clone_directory, progress)

                lambdapdk_path = using_repos.get("lambdapdk")
                if lambdapdk_path is None:
                    lambdapdk_path = (
                        executor.submit(
                            GitMultiClone.clone,
                            gmc,
                            lambdapdk_repo.link,
                            version,
                            blobless=True,
                            sparse_paths=[LAMBDAPDK_PATH],
                        )
                        .result()
                        .path
                    )
                    console.log(f"Done fetching {lambdapdk_repo.name}.")
                else:
                    console.log(f"Using lambdapdk at {lambdapdk_path} unaltered.")

                gt2n_path = using_repos.get("gt2n")
                if gt2n_path is None:
                    data_commit = get_data_commit(lambdapdk_path)
                    console.log(f"lambdapdk {version} pins GT2N at {data_commit}.")
                    gt2n_path = (
                        executor.submit(
                            GitMultiClone.clone,
                            gmc,
                            gt2n_repo.link,
                            data_commit,
                        )
                        .result()
                        .path
                    )
                    console.log(f"Done fetching {gt2n_repo.name}.")
                else:
                    console.log(f"Using GT2N at {gt2n_path} unaltered.")

        return lambdapdk_path, gt2n_path

    except subprocess.CalledProcessError as e:
        print(e)
        print(e.stderr)
        exit(-1)


def build_gt2n(build_directory, lambdapdk_path, gt2n_path):
    variant_directory = os.path.join(build_directory, "gt2n")

    # The data repository is the bulk of the PDK, so it becomes the variant
    # root; lambdapdk's tool setup sits beside it under lambdapdk/.
    copy_upstream_tree(gt2n_path, variant_directory)
    copy_upstream_tree(
        os.path.join(lambdapdk_path, LAMBDAPDK_PATH),
        os.path.join(variant_directory, "lambdapdk"),
    )

    make_descriptor_dir(variant_directory, "gt2n")
    # Merged into the directory make_descriptor_dir just made, rather than
    # replacing it, so the README it wrote survives beside the descriptors.
    shutil.copytree(
        DESCRIPTOR_PATH,
        os.path.join(variant_directory, "libs.tech", "librelane"),
        dirs_exist_ok=True,
    )


def install_gt2n(build_directory, pdk_root, version):
    install_trivial_build(build_directory, pdk_root, version, "gt2n")


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

    build_directory = os.path.join(get_ciel_dir(pdk_root, "gt2n"), "build", version)
    timestamp = datetime.now().strftime("build_gt2n-%Y-%m-%d-%H-%M-%S")
    log_dir = os.path.join(build_directory, "logs", timestamp)
    mkdirp(log_dir)

    console.log(f"Logging to '{log_dir}'…")

    lambdapdk_path, gt2n_path = get_gt2n(version, build_directory, jobs, using_repos)
    build_gt2n(build_directory, lambdapdk_path, gt2n_path)
    install_gt2n(build_directory, pdk_root, version)

    if clear_build_artifacts:
        shutil.rmtree(build_directory)
