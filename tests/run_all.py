"""Alle Tests nacheinander ausführen.
  python3 tests/run_all.py            App-Tests (ohne Root)
  sudo python3 tests/run_all.py --root   zusätzlich die Wartungsskripte (brauchen Root für chown)
"""
import glob, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_TESTS = {"test_skripte.py", "test_angriffe.py"}
want_root = "--root" in sys.argv
only_root = "--nur-root" in sys.argv
files = sorted(os.path.basename(f) for f in glob.glob(os.path.join(HERE, "test_*.py")))
if only_root:
    files = [f for f in files if f in ROOT_TESTS]
elif not want_root:
    files = [f for f in files if f not in ROOT_TESTS]
if (want_root or only_root) and os.geteuid() != 0:
    sys.exit("--root/--nur-root nur als root (sudo) ausführen")
failed = []
for f in files:
    t = time.time()
    r = subprocess.run([sys.executable, os.path.join(HERE, f)], cwd=HERE, capture_output=True, text=True, timeout=900)
    last = (r.stdout.strip().splitlines() or [""])[-1]
    print(f"{'OK  ' if r.returncode == 0 else 'FEHLER'} {f:28} {time.time() - t:5.1f}s  {last if r.returncode == 0 else ''}", flush=True)
    if r.returncode:
        failed.append(f)
        print(r.stdout[-2000:], r.stderr[-4000:], sep="\n")
print(f"\n{len(files) - len(failed)}/{len(files)} Testdateien bestanden")
sys.exit(1 if failed else 0)
