# Fork-only tooling

Things this fork needs in order to develop and verify the PDK descriptors it
ships, which are not themselves part of any PDK. Nothing under this directory
is copied into an installed variant: `ciel/build` only ever reads
`ciel/build/descriptors/`.

- `drc_tests/` — a unit-test suite for the GT2N KLayout DRC deck
  (`ciel/build/descriptors/gt2n/gt2n_drc.drc`). It builds synthetic layouts
  with known-good and known-bad geometry for each primitive the deck's port of
  the vendor IC Validator runset relies on, runs the deck over them, and checks
  that exactly the expected rule categories fire. Run it inside a shell that
  has KLayout on `PATH` — LibreLane's `nix develop` is one:

      python3 .librelane-fork/drc_tests/run.py
