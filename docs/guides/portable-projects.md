# Portable projects

Use **Portable projects** in the program editor toolbar to transfer selected
programs and saved data as a ZIP archive. The archive contains a manifest with
file checksums and package requirements.

## Export

Open the programs and helper modules you want to include, then select them on
the Export tab. Their current editor contents are exported, including unsaved
edits. Select any named setup snapshots, demonstration recordings, saved worlds,
and numeric run record exports the programs need. Setup snapshots include saved
TCP and camera calibration data.

The data source is the active program's project when it belongs to one, or the
shared saved data otherwise. No directory tree or environment files are bundled
automatically. Selected Python source and setup data retain their contents;
review them before sharing. Run records use the numeric debugging export that
removes captured text and replaces custom names with aliases.

Package requirements start with the installed Waldo Commander, waldoctl, and
backend versions. Edit these and add third-party skill or helper dependencies,
one package requirement per line. Import reports missing or incompatible package
versions. Optional package extras require a separate check. Dependency URLs are
not supported, and the importer does not install packages.

## Import and use

Upload an archive on the Import tab to inspect its file list and dependency
report. **Import into new folder** creates a unique folder under
`programs/projects/`; an existing project is never overwritten. This step does
not execute Python, apply calibration or world data, connect a new robot client,
or resume a previous run.

Choose a program and use **Open selected program** to open it in the editor.
Opening uses the normal isolated preview; running remains an explicit action.
Only run Python programs you trust, just as when opening a normal program file.
Saving an edited imported program keeps it in its project folder. Renaming it
through the filename field saves it in the normal programs directory.

During preview and managed execution, `load_setup("bench")` reads
`setups/bench.json` inside the project. The program's directory and the project's
`programs/` directory are available for Python helper imports. The working
directory is the project root, and `__file__` identifies the saved program.
Loading a setup gives a snapshot; applying its TCP or other configuration still
requires an explicit program command or panel action.

Use `project_file` to locate other selected resources consistently:

```python
import json

from waldo_commander.demonstrations import load_demonstration
from waldo_commander.project import project_file
from waldo_commander.setup import load_setup
from waldoctl.world import world_from_dict

setup = load_setup("bench")
recording = load_demonstration(project_file("recordings/capture.json"))
world = world_from_dict(
    json.loads(project_file("worlds/fixture.json").read_text())
)
```

Outside Waldo Commander, pass the project root explicitly to
`project_file(..., root="/path/to/project")` and load named setups through
`SetupStore("/path/to/project/setups").load("bench")`. Resource loading alone
does not replay a recording or apply a world.

## Archive limits

Archives contain up to 128 selected files: UTF-8 Python under `programs/`, named
setup JSON under `setups/`, and JSON under `recordings/`, `worlds/`, or `debug/`.
The maximum is 16 MiB per file and 64 MiB for both the ZIP and its unpacked
contents, including the manifest. Imports validate all paths, file types,
checksums, and supported data schemas before creating the project folder.
Symlinks, paths outside the project, and names that collide on a
case-insensitive filesystem are rejected.
