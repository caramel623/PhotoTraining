"""Offline installation acceptance test, runnable inside the actual release EXE."""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path


def check_installer(directory: str) -> int:
    from vehicle_dataset_manager.detection.cuda_env import _run_pip_in_process

    root = Path(directory).resolve()
    # Never overwrite an existing user directory.
    root.mkdir(parents=True, exist_ok=False)
    wheel = root / "vdm_installer_probe-1.0-py3-none-any.whl"
    metadata = "vdm_installer_probe-1.0.dist-info"
    files = {
        "vdm_installer_probe.py": "def main():\n    return 0\n",
        f"{metadata}/METADATA": "Metadata-Version: 2.1\nName: vdm-installer-probe\nVersion: 1.0\n",
        f"{metadata}/WHEEL": "Wheel-Version: 1.0\nGenerator: vdm-check\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{metadata}/entry_points.txt": "[console_scripts]\nvdm-installer-probe = vdm_installer_probe:main\n",
    }
    files[f"{metadata}/RECORD"] = "".join(f"{name},,\n" for name in [*files, f"{metadata}/RECORD"])
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    runs = []
    for number in (1, 2):
        target = root / f"target-{number}"
        result = _run_pip_in_process([
            "install", "--no-index", "--no-deps", "--no-cache-dir",
            "--disable-pip-version-check", "--target", str(target), str(wheel),
        ])
        launchers = list(target.rglob("vdm-installer-probe.exe"))
        installed = (target / "vdm_installer_probe.py").is_file()
        scripts_ok = bool(launchers) if sys.platform == "win32" else True
        runs.append({
            "success": result.success and installed and scripts_ok,
            "returncode": result.returncode, "installed": installed,
            "launcher_created": scripts_ok, "output": result.output_tail,
        })
    report = {"frozen": bool(getattr(sys, "frozen", False)), "runs": runs}
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(run["success"] for run in runs) else 1
