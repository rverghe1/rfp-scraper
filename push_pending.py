"""Pushes any local output/*.csv backups to the Google Sheet, then moves them
to output/pushed/ so they don't get re-sent next time."""
import csv
import shutil
from pathlib import Path
from core.sheets import append_rows

OUT_DIR = Path(__file__).parent / "output"
PUSHED_DIR = OUT_DIR / "pushed"


def main():
    PUSHED_DIR.mkdir(exist_ok=True)
    csvs = sorted(OUT_DIR.glob("*.csv"))
    if not csvs:
        print("No pending CSVs to push.")
        return
    for path in csvs:
        with open(path) as f:
            rows = list(csv.DictReader(f))
        if not rows:
            continue
        n = append_rows(rows)
        print(f"Pushed {n} rows from {path.name}")
        shutil.move(str(path), str(PUSHED_DIR / path.name))


if __name__ == "__main__":
    main()
