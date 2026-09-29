"""打包安裝程式：python installer/build_package.py [輸出資料夾]

產生「DENKI伴奏工具-vX.Y.Z-安裝.zip」：
  DENKI伴奏工具-vX.Y.Z\
    安裝.bat
    app\   ← repo 裡 git 有追蹤的檔案（加上還沒 commit 的新檔案以外，跟 GitHub 上的一樣）
"""
import re
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
version = re.search(r'__version__ = "([^"]+)"', (REPO / "karaoke" / "__init__.py").read_text(encoding="utf-8")).group(1)
out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO.parent
top = f"DENKI伴奏工具-v{version}"
zip_path = out_dir / f"{top}-安裝.zip"

files = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout.decode().split("\0")
files = [f for f in files if f and (REPO / f).is_file()]
for need in ("ui/dist/index.html", "requirements-lock.txt", "installer/install.ps1", "wheels"):
    if not any(f == need or f.startswith(need + "/") for f in files):
        sys.exit(f"缺少 {need}（git add 了嗎？）")

with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    z.write(REPO / "installer" / "安裝.bat", f"{top}/安裝.bat")
    for f in files:
        z.write(REPO / f, f"{top}/app/{f}")
print(f"{zip_path}（{len(files)} 個檔案，{zip_path.stat().st_size / 1e6:.1f} MB）")
