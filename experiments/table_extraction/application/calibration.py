from pathlib import Path
import os
from experiments.table_extraction.domain.calibration import calculate_rows

def run_calibration(labels_path: Path, output_root: Path, chart_path: Path, *, read_labels,
                    parse_labels, load_candidates, update_label_markdown, render_chart, write_text):
    markdown = read_labels(labels_path)
    slots, labels = parse_labels(markdown)
    candidates = {name: load_candidates(output_root, name) for name in sorted({x.pdf_name for x in labels})}
    rows = calculate_rows(labels, slots, candidates)
    chart = render_chart(rows)
    updated = update_label_markdown(markdown, rows, os.path.relpath(chart_path, labels_path.parent).replace('\\','/'))
    write_text(chart_path, chart)
    write_text(labels_path, updated)
    return rows
