"""Quick sanity tests for core.py — run before trusting it with rm."""
import tempfile, shutil
from pathlib import Path
from godot_sweeper import core

def make_fake_project(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.godot").write_text("[application]\n")
    (root / "main.gd").write_text("extends Node\n")  # source, must survive
    g = root / ".godot"
    (g / "imported").mkdir(parents=True)
    (g / "imported" / "tex.png-abc.ctex").write_text("x" * 100)
    (g / "shader_cache").mkdir()
    (g / "shader_cache" / "s.cache").write_text("y" * 50)
    (g / "project.godot").write_text("keep me")  # NEVER_DELETE name inside .godot
    (g / "uid_cache.bin").write_text("z" * 10)    # not on allowlist, must survive
    return root

def run():
    tmp = Path(tempfile.mkdtemp())
    try:
        proj = make_fake_project(tmp / "MyGame")
        proj.mkdir(exist_ok=True) if not proj.exists() else None

        # detection
        assert core.is_godot_project(proj), "should detect project.godot"

        # scan
        scan = core.scan_project(proj)
        names = {i.path.name for i in scan.items}
        assert "imported" in names, "imported should be found"
        assert "shader_cache" in names, "shader_cache should be found"
        assert "project.godot" not in names, "never delete project.godot"
        assert "uid_cache.bin" not in names, "non-allowlisted must be skipped"
        assert scan.total_bytes == 150, f"expected 150 bytes, got {scan.total_bytes}"

        # find_projects
        found = core.find_projects(tmp)
        assert proj.resolve() in [p.resolve() for p in found], "find should locate project"

        # delete
        res = core.delete_items(scan, scan.items)
        assert not res.errors, f"unexpected errors: {res.errors}"
        assert res.freed_bytes == 150, f"freed {res.freed_bytes}"
        assert not (proj / ".godot" / "imported").exists(), "imported gone"
        assert not (proj / ".godot" / "shader_cache").exists(), "shader_cache gone"

        # critical: source + project file survived
        assert (proj / "main.gd").exists(), "SOURCE FILE DELETED — FAIL"
        assert (proj / "project.godot").exists(), "project.godot deleted — FAIL"
        assert (proj / ".godot" / "uid_cache.bin").exists(), "non-allowlisted deleted — FAIL"

        # safety gate: try to delete something outside .godot
        evil = core.CacheItem(proj / "main.gd", 10, is_dir=False)
        res2 = core.delete_items(scan, [evil])
        assert (proj / "main.gd").exists(), "GATE FAILED — deleted file outside .godot"
        assert res2.errors, "should have logged a safety-check error"

        print("ALL TESTS PASSED")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    run()
