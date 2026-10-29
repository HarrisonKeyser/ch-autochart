# PyInstaller recipe for the Windows program.
# Builds dist/ch-autochart/ (ch-autochart.exe plus everything it needs).
# Run from the repository root:   pyinstaller packaging/ch-autochart.spec --noconfirm
# The separation model must already be in models/ (the GitHub workflow downloads it).
import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 (provided by PyInstaller)

datas = [
    (os.path.join(ROOT, "web"), "web"),
    (os.path.join(ROOT, "models"), "models"),
    (os.path.join(ROOT, "packaging", "icon.ico"), "packaging"),
]
binaries = []
hiddenimports = ["app", "autochart", "desktop"]

# Packages that load data files or plugins at run time, which PyInstaller
# can't discover by reading imports alone.
for pkg in ["demucs", "librosa", "imageio_ffmpeg", "soundfile", "_soundfile_data", "soxr",
            "webview", "julius", "openunmix", "mutagen", "sklearn", "scipy", "numba", "llvmlite",
            "lazy_loader", "audioread", "pooch", "flask", "werkzeug", "jinja2"]:
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "desktop.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["matplotlib", "IPython", "pytest", "tkinter", "torch.utils.tensorboard", "tensorboard",
              "triton", "torchvision"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ch-autochart",
    console=False,
    icon=os.path.join(ROOT, "packaging", "icon.ico"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ch-autochart", upx=False)  # noqa: F821
