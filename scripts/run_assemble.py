"""Assemble matrix from existing search + reader results."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from screener.config import init_run
from screener.assemble import assemble_matrix, print_summary, save_matrix


def main():
    init_run(create_new=False)

    df = assemble_matrix()
    output = save_matrix(df)
    print_summary(df)
    print(f"\nMatrix saved to: {output}")


if __name__ == "__main__":
    main()
