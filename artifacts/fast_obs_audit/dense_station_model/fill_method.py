"""Fill METHOD.md placeholders verbatim from summary_table.md, ss_summary_table.md, and hand-written verdict / data
sections (VERDICTS.md.in, DATA.md.in). Idempotent: METHOD.md.in is the template."""
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    t = (HERE / "METHOD.md.in").read_text()
    t = t.replace("SUMMARY_TABLE_PLACEHOLDER", (HERE / "summary_table.md").read_text().strip())
    t = t.replace("SS_TABLE_PLACEHOLDER", (HERE / "ss_summary_table.md").read_text().strip())
    t = t.replace("VERDICTS_PLACEHOLDER", (HERE / "VERDICTS.md.in").read_text().strip())
    t = t.replace("DATA_PLACEHOLDER", (HERE / "DATA.md.in").read_text().strip())
    (HERE / "METHOD.md").write_text(t)
    print("METHOD.md written", len(t))


if __name__ == "__main__":
    main()
