# cloud-kit

The cloud surface: moving things between machines. If a tool's job is to get
something from one box to another, it lives here.

Its sibling is `dev-kit`, which holds what you write code WITH. The split is
by what a thing is for, not by where it runs.

## What is here

| Submodule | What it does |
|---|---|
| `machine-sync` | push and pull files between two machines over SSH, with the paths named in config instead of typed |

Standalone tools in `tools/`, plain Python 3 (tested on 3.13), each with a
`--self-test`:

| tool | what it does for you |
|---|---|
| `sync-branch.py` | Brings a local branch level with its remote: fast-forwards when behind, pushes when ahead, rebases your commits on top when diverged, and keeps uncommitted work safe. `--dry-run` shows the plan. |
| `stash-triage.py` | Reports what each git stash holds and whether that change already landed, so old stashes can be dropped without guessing. Read-only. |

## Clone

```
git clone --recursive https://github.com/iasolb/cloud-kit.git
```

An existing clone picks up the submodules with
`git submodule update --init --recursive`.

## Related, and deliberately not here

- **`ensure-utf8`** lives in `data-kit`. `machine-sync --csv` runs it over
  transferred CSVs, resolving the installed command first, so install it with
  `pip install git+https://github.com/iasolb/data-kit.git` if you want that
  flag to do anything.
- **The data loaders and the research framework** live in `research-kit`.
  They were briefly submodules of this repo, which was a filing mistake:
  they are research tools, not transfer tools.

## Where this is going

`machine-sync` currently understands exactly two machines. The intended
replacement is a registry: register a device's SSH address under an alias,
then register named locations on that device beneath it, so a transfer names
two machines and a spot rather than a path anyone has to remember.

## Start here

Open the `machine-sync` submodule: see `docs/setup.md` there for setup and
the test checklist. The naming convention is `<domain>-kit`; siblings are
`dev-kit`, `data-kit`, `research-kit`, and `ai-kit`.
